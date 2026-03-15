"""
desktop_app.py — AcompaLLM 桌面客户端入口

架构：
  - pywebview 创建原生系统窗口（类 Electron）
  - 内嵌 SimpleHTTPServer 以 http://127.0.0.1:PORT/ 方式服务 client/ 目录
    → CDN 脚本、字体等网络资源均可正常加载
  - AppApi 类通过 pywebview js_api 桥接 Python ↔ JS
  - 所有流式输出通过 window.evaluate_js() 推送到前端

运行：
    python desktop_app.py
"""

import http.server
import json
import logging
import mimetypes
import os
import base64
import pathlib
import socket
import sys
import time
import threading
import shutil
import urllib.parse
import uuid
from typing import Dict, List, Optional

import webview

# ── 屏蔽无害的 BertModel UNEXPECTED key 噪音日志 ────────────────────────────
logging.getLogger("hf_transfer").setLevel(logging.ERROR)
logging.getLogger("transformers.modeling_utils").setLevel(logging.ERROR)


def _configure_hf_offline() -> None:
    """
    若嵌入模型已在本地缓存，则启用 HuggingFace 离线模式，
    跳过每次启动时的 Hub 网络握手（节省 1-3 秒）。
    首次运行（缓存为空）会正常联网下载，之后永久离线加载。
    """
    model_id = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
    # HuggingFace Hub 标准缓存目录（Windows/macOS/Linux 均适用）
    hf_hub_dir = pathlib.Path.home() / ".cache" / "huggingface" / "hub"
    model_dir  = hf_hub_dir / ("models--" + model_id.replace("/", "--"))
    # sentence-transformers 自有缓存目录（旧版路径兼容）
    st_dir = pathlib.Path.home() / ".cache" / "torch" / "sentence_transformers" / model_id.replace("/", "_")

    if model_dir.exists() or st_dir.exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
        print(f"[AcompaLLM] 嵌入模型已缓存，启用离线模式（跳过 Hub 检查）")
    else:
        print(f"[AcompaLLM] 嵌入模型缓存未找到，将联网下载: {model_id}")

from app.client_core import ClientCore

# ──────────────────────────────────────────────────────────────────────────────
CLIENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BACKGROUND_DIR = os.path.join(DATA_DIR, "background")
os.makedirs(BACKGROUND_DIR, exist_ok=True)


def _find_free_port(start: int = 9127) -> int:
    """从 start 往后找一个可用的 127.0.0.1 端口。"""
    for port in range(start, start + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start


def _start_file_server(directory: str, port: int) -> None:
    """在后台线程启动本地静态文件服务器，仅绑定 127.0.0.1。"""

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def do_GET(self):
            if self.path.startswith("/data/background/"):
                rel = urllib.parse.unquote(self.path[len("/data/background/"):])
                filename = os.path.basename(rel)
                if not filename:
                    self.send_error(404)
                    return
                file_path = os.path.join(BACKGROUND_DIR, filename)
                if not os.path.isfile(file_path):
                    self.send_error(404)
                    return
                ctype, _ = mimetypes.guess_type(file_path)
                self.send_response(200)
                self.send_header("Content-type", ctype or "application/octet-stream")
                self.send_header("Content-Length", str(os.path.getsize(file_path)))
                self.end_headers()
                with open(file_path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
                return
            super().do_GET()

        def log_message(self, *args):
            pass  # 静默，不打印访问日志

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────

class AppApi:
    """
    所有 public 方法均暴露给前端 JS，通过 pywebview.api.xxx() 调用（返回 Promise）。
    命名约定：与 client_core.ClientCore 保持一致，前端直接对应。
    """

    def __init__(self) -> None:
        self._core = ClientCore()
        self._stop_flags: Dict[str, threading.Event] = {}

    # ── 配置 ────────────────────────────────────────────────────────────────

    def get_config(self) -> Dict:
        return self._core.get_config()

    def save_config(self, updates: Dict) -> Dict:
        ok = self._core.save_config(updates)
        return {"ok": ok}

    # ── 窗口控制 ─────────────────────────────────────────────────────────

    def win_minimize(self) -> None:
        webview.windows[0].minimize()

    def win_maximize(self) -> None:
        w = webview.windows[0]
        if w.maximized:
            w.restore()
        else:
            w.maximize()

    def win_close(self) -> None:
        webview.windows[0].destroy()

    def test_connection(self) -> Dict:
        return self._core.test_connection()

    def test_web_search(self) -> Dict:
        return self._core.test_web_search()

    def test_embed_connection(self, params: Optional[Dict] = None) -> Dict:
        return self._core.test_embed_connection(params or {})

    def list_embed_models(self, params: Optional[Dict] = None) -> Dict:
        return self._core.list_embed_models(params or {})

    def list_upstream_models(self) -> List[str]:
        models = self._core.list_upstream_models()
        return [m["id"] for m in models]

    # ── 对话管理 ─────────────────────────────────────────────────────────────

    def list_conversations(self) -> List[Dict]:
        return self._core.list_conversations()

    def new_conversation(self) -> Dict:
        return self._core.new_conversation()

    def get_conversation(self, conv_id: str) -> Optional[Dict]:
        return self._core.get_conversation(conv_id)

    def rename_conversation(self, conv_id: str, title: str) -> Dict:
        ok = self._core.rename_conversation(conv_id, title)
        return {"ok": ok}

    def update_conversation(self, conv_id: str, updates: Dict) -> Dict:
        ok = self._core.update_conversation(conv_id, updates)
        return {"ok": ok}

    def delete_conversation(self, conv_id: str) -> Dict:
        ok = self._core.delete_conversation(conv_id)
        return {"ok": ok}

    def clear_conversation(self, conv_id: str) -> Dict:
        ok = self._core.clear_conversation(conv_id)
        return {"ok": ok}

    def delete_message(self, conv_id: str, msg_id: str) -> Dict:
        ok = self._core.delete_message(conv_id, msg_id)
        return {"ok": ok}

    # ── 流式消息发送 ──────────────────────────────────────────────────────────

    def send_message(
        self,
        conv_id: str,
        content: str,
        options: Optional[Dict] = None,
    ) -> Dict:
        """
        启动后台流式线程。
        进度事件通过 window.__onStreamEvent(event) 推送到 JS。
        """
        stop_flag = threading.Event()
        self._stop_flags[conv_id] = stop_flag

        t = threading.Thread(
            target=self._stream_thread,
            args=(conv_id, content, options or {}, stop_flag),
            daemon=True,
        )
        t.start()
        return {"ok": True}

    def cancel_stream(self, conv_id: str) -> Dict:
        flag = self._stop_flags.get(conv_id)
        if flag:
            flag.set()
        return {"ok": True}

    def stop_generation(self, conv_id: Optional[str] = None) -> Dict:
        if conv_id:
            return self.cancel_stream(conv_id)
        for flag in self._stop_flags.values():
            flag.set()
        return {"ok": True}

    def _stream_thread(
        self,
        conv_id: str,
        content: str,
        options: Dict,
        stop_flag: threading.Event,
    ) -> None:
        import time
        w = webview.windows[0]
        # 流式输出节流：把高频 chunk 事件合并批量推送，减少 evaluate_js 调用
        _FLUSH_INTERVAL = 0.02   # 20 ms / 帧
        _BATCHABLE_TYPES = {"content_chunk", "reasoning_chunk"}
        buf: list[dict] = []
        last_flush = time.monotonic()

        def _flush():
            nonlocal last_flush
            if not buf:
                return
            batch = json.dumps(buf, ensure_ascii=False)             
            w.evaluate_js(f"window.__onStreamBatch({batch})")
            buf.clear()
            last_flush = time.monotonic()

        try:
            for event in self._core.stream_message(conv_id, content, options, stop_flag):
                event["conv_id"] = conv_id
                etype = event.get("type")
                # 高频文本事件走批处理，其他事件立即发送
                if etype in _BATCHABLE_TYPES:
                    if (
                        buf
                        and buf[-1].get("type") == etype
                        and isinstance(buf[-1].get("text"), str)
                        and isinstance(event.get("text"), str)
                    ):
                        buf[-1]["text"] += event["text"]
                    else:
                        buf.append(event)
                    if time.monotonic() - last_flush >= _FLUSH_INTERVAL:
                        _flush()
                else:
                    _flush()
                    payload = json.dumps(event, ensure_ascii=False)
                    w.evaluate_js(f"window.__onStreamEvent({payload})")
                if stop_flag.is_set():
                    _flush()
                    cancel_evt = json.dumps({"type": "cancelled", "conv_id": conv_id})
                    w.evaluate_js(f"window.__onStreamEvent({cancel_evt})")
                    return
            _flush()
            # 若生成器提前返回（工具阶段被取消）仍需通知前端
            if stop_flag.is_set():
                cancel_evt = json.dumps({"type": "cancelled", "conv_id": conv_id})
                w.evaluate_js(f"window.__onStreamEvent({cancel_evt})")
        except Exception as exc:
            _flush()
            err_evt = json.dumps(
                {"type": "error", "message": str(exc), "conv_id": conv_id},
                ensure_ascii=False,
            )
            w.evaluate_js(f"window.__onStreamEvent({err_evt})")

    # ── 记忆库 ───────────────────────────────────────────────────────────────

    def add_to_memory(
        self, text: str, title: str = "", source: str = ""
    ) -> Dict:
        ok = self._core.add_to_memory(text, title, source)
        return {"ok": ok, "error": "" if ok else "RAG 适配器未安装，请先安装 rag_adapter"}

    # ── 知识库管理 ──────────────────────────────────────────────────────────

    def kb_list(self) -> List[Dict]:
        return self._core.list_kb_collections()

    def kb_ingest_file(self, path: str, name: str = "", embed_model: str = "") -> Dict:
        w = webview.windows[0]

        def _progress(done: int, total: int) -> None:
            payload = json.dumps({"path": path, "done": done, "total": total}, ensure_ascii=False)
            w.evaluate_js(f"window.__onKbProgress({payload})")

        return self._core.kb_ingest_file(path, name, embed_model, on_progress=_progress)

    def kb_ingest_bytes(self, filename: str, b64data: str, name: str = "", embed_model: str = "") -> Dict:
        """接收 base64 文件内容（WebView2 拖拽无法获取路径时使用），写临时文件后导入。"""
        import base64 as _b64, tempfile, os as _os
        w = webview.windows[0]

        def _progress(done: int, total: int) -> None:
            payload = json.dumps({"path": filename, "done": done, "total": total}, ensure_ascii=False)
            w.evaluate_js(f"window.__onKbProgress({payload})")

        tmp_path = None
        try:
            data = _b64.b64decode(b64data)
            suffix = _os.path.splitext(filename)[1] or '.bin'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="acompallm_") as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            return self._core.kb_ingest_file(tmp_path, name, embed_model, on_progress=_progress, source_name=filename)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            if tmp_path:
                try:
                    _os.unlink(tmp_path)
                except Exception:
                    pass

    def kb_ingest_folder(self, folder: str, name: str = "", embed_model: str = "") -> Dict:
        return self._core.kb_ingest_folder(folder, name, embed_model)

    def kb_ingest_url(self, url: str, name: str, embed_model: str = "") -> Dict:
        return self._core.kb_ingest_url(url, name, embed_model)

    def kb_delete(self, name: str) -> Dict:
        ok = self._core.kb_delete(name)
        return {"ok": ok}

    def kb_list_sources(self, collection_name: str) -> List[Dict]:
        return self._core.kb_list_sources(collection_name)

    def kb_peek_chunks(self, collection_name: str, source: str, limit: int = 100) -> List[Dict]:
        return self._core.kb_peek_chunks(collection_name, source, limit)

    def kb_delete_source(self, collection_name: str, source: str) -> Dict:
        return self._core.kb_delete_source(collection_name, source)

    def conv_set_kb_names(self, conv_id: str, kb_names: List) -> Dict:
        """持久化对话绑定的知识库列表。"""
        ok = self._core.conv_set_kb_names(conv_id, kb_names)
        return {"ok": ok}

    # ── 对话记忆人管理 ──────────────────────────────────────────────

    def mem_list_persons(self) -> List[Dict]:
        return self._core.mem_list_persons()

    def mem_create_person(self, name: str) -> Dict:
        return {"ok": self._core.mem_create_person(name)}

    def mem_delete_person(self, name: str) -> Dict:
        return {"ok": self._core.mem_delete_person(name)}

    def import_background_image(self, path: str) -> Dict:
        if not path:
            return {"ok": False, "error": "empty path"}
        try:
            base_dir = BACKGROUND_DIR
            os.makedirs(base_dir, exist_ok=True)
            filename = os.path.basename(path)
            stem, ext = os.path.splitext(filename)
            token = uuid.uuid4().hex[:8]
            target = os.path.join(base_dir, f"{stem}_{token}{ext}")
            shutil.copy2(path, target)
            return {"ok": True, "path": os.path.basename(target)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def open_image_dialog(self) -> List[str]:
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("Images (*.png;*.jpg;*.jpeg;*.gif;*.webp)", "All Files (*.*)"),
        )
        if not result:
            return []
        return list(result)

    def open_file_dialog(self) -> str:
      result = webview.windows[0].create_file_dialog(
          webview.FileDialog.OPEN,
          allow_multiple=True,
          file_types=("Documents (*.txt;*.md;*.pdf;*.docx)", "All Files (*.*)"),
      )
      if not result:
          return []
      return list(result)

    def open_attach_dialog(self) -> List[str]:
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=(
                "Attachments (*.txt;*.md;*.pdf;*.docx;*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp;*.svg)",
                "All Files (*.*)",
            ),
        )
        if not result:
            return []
        return list(result)

    def get_attachment_preview(self, path: str) -> Dict:
        if not path or not os.path.isfile(path):
            return {"ok": False, "error": "file not found"}
        try:
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            size = os.path.getsize(path)
            name = os.path.basename(path)
            if not mime.startswith("image/"):
                return {
                    "ok": True,
                    "name": name,
                    "size": size,
                    "mime": mime,
                    "data_url": "",
                }
            if size > 2 * 1024 * 1024:
                return {
                    "ok": True,
                    "name": name,
                    "size": size,
                    "mime": mime,
                    "data_url": "",
                }
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return {
                "ok": True,
                "name": name,
                "size": size,
                "mime": mime,
                "data_url": f"data:{mime};base64,{b64}",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def open_folder_dialog(self) -> str:
        result = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else ""


# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    # ── 设置统一日志记录 ────────────────────────────────────────────────
    try:
        from utils.imports import setup_logging
        setup_logging(level=logging.INFO)
        print(f"[AcompaLLM] 日志系统已初始化，级别: INFO")
    except ImportError as e:
        # 回退到基础日志配置
        logging.basicConfig(
            level=logging.WARNING,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        print(f"[AcompaLLM] 使用基础日志配置: {e}")

    # ── 离线检查：跳过 HuggingFace Hub 网络握手 ─────────────────────────────
    _configure_hf_offline()

    # ── 后台启动内嵌模型预热（与 GUI 初始化并行）─────────────────────────
    def _warmup_embed():
        try:
            import kb
            kb.warmup()
        except Exception as exc:
            logging.warning(f"[AcompaLLM] KB 嵌入模型预热失败: {exc}")
        try:
            import memory as _mem
            _mem.warmup()
        except Exception as exc:
            logging.warning(f"[AcompaLLM] 记忆模型预热失败: {exc}")
    threading.Thread(target=_warmup_embed, daemon=True, name="embed-warmup").start()

    if not os.path.isdir(CLIENT_DIR):
        print(f"[错误] 找不到客户端目录: {CLIENT_DIR}")
        print("请确保 client/index.html 存在后再启动。")
        return

    # ── 调试：打印 pywebview 可用后端 ────────────────────────────────────────
    import webview.guilib as guilib
    print(f"[AcompaLLM] 系统平台: {sys.platform}")
    print(f"[AcompaLLM] WebView2 版本: 145.0.3800.97 (已安装)")
    
    # 列出所有可用后端
    available_guis = []
    for gui_name in ['edgechromium', 'cef', 'qt', 'gtk', 'winforms', 'cocoa']:
        try:
            if guilib.try_import([gui_name]):
                available_guis.append(gui_name)
        except:
            pass
    print(f"[AcompaLLM] 可用 GUI 后端: {available_guis}")
    
    # 强制使用 edgechromium，如果不可用则使用第一个可用的后端
    preferred_gui = "edgechromium"
    if "edgechromium" not in available_guis and available_guis:
        preferred_gui = available_guis[0]
        print(f"[AcompaLLM] EdgeChromium 不可用，将使用: {preferred_gui}")
    
    # ── QtWebEngine / Chromium 渲染性能优化 ──────────────────────────────────
    if sys.platform == "win32":
        # Windows：让 QtWebEngine 使用 ANGLE/DirectX 硬件加速，无需特殊标志
        os.environ.setdefault(
            "QTWEBENGINE_CHROMIUM_FLAGS",
            "--disable-background-networking --disable-extensions",
        )
    else:
        # WSL2/Linux：Mesa D3D12/Vulkan 路径不稳定，禁用 GPU 进程，用 Skia CPU 光栅化
        os.environ.setdefault(
            "QTWEBENGINE_CHROMIUM_FLAGS",
            " ".join([
                "--disable-gpu",
                "--disable-dev-shm-usage",     # WSL2 /dev/shm 小，避免 shm 崩溃
                "--no-sandbox",                # WSL2 namespace 不完整
                "--disable-background-networking",
                "--disable-extensions",
            ])
        )

    port = _find_free_port(9127)
    _start_file_server(CLIENT_DIR, port)
    print(f"[AcompaLLM] 本地文件服务已启动: http://127.0.0.1:{port}/")

    scale = 1.0
    print(f"[AcompaLLM] 系统缩放比例: {scale}")

    api = AppApi()

    window = webview.create_window(
        title="AcompaLLM",
        url=f"http://127.0.0.1:{port}/?scale={scale}",
        js_api=api,
        width=1280,
        height=720,
        min_size=(800, 600),
        background_color="#0d1117",
        text_select=True,
        zoomable=True,
        frameless=True,
        easy_drag=False,
        hidden=False,
    )

    webview.start(debug=False, gui=preferred_gui)


if __name__ == "__main__":
    main()
