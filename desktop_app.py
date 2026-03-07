"""
desktop_app.py — AI Memory 桌面客户端入口

架构：
  - pywebview 创建原生系统窗口（类 Electron）
  - 内嵌 SimpleHTTPServer 以 http://127.0.0.1:PORT/ 方式服务 client/ 目录
    → CDN 脚本、字体等网络资源均可正常加载
  - AppApi 类通过 pywebview js_api 桥接 Python ↔ JS
  - 所有流式输出通过 window.evaluate_js() 推送到前端

运行：
    python desktop_app.py
"""

import bisect
import http.server
import json
import os
import pickle
import socket
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import webview

from client_core import ClientCore

# ──────────────────────────────────────────────────────────────────────────────
CLIENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client")

# ── IME 后端拼音引擎（全局状态）──────────────────────────────────────────────
_ime_index: Dict[str, List[str]] = {}   # 拼音/简拼 → [候选词, ...]
_ime_keys:  List[str]            = []   # 有序 key 列表，用于前缀二分查找
_ime_ready  = threading.Event()          # 索引构建完成标志


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


def _build_ime_index() -> None:
    """后台构建拼音→词语反向索引（jieba 词表 + pypinyin 音节映射）。

    首次运行约需 20-40 秒；之后从磁盘缓存加载，几乎瞬间完成。
    未就绪期间前端 JS 内置字典照常运行，不影响使用体验。
    """
    global _ime_index, _ime_keys
    if sys.platform == "win32":
        _ime_cache_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AI_Memory"
        )
    else:
        _ime_cache_dir = os.path.expanduser("~/.cache")
    cache_path = os.path.join(_ime_cache_dir, "aim_ime_v1.pkl")

    # 尝试从缓存快速加载
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                saved = pickle.load(f)
            _ime_index = saved["index"]
            _ime_keys  = saved["keys"]
            _ime_ready.set()
            print(f"[AI Memory IME] 索引已从缓存加载（共 {len(_ime_index)} 条拼音）")
            return
        except Exception:
            pass

    print("[AI Memory IME] 首次运行：正在构建拼音索引（约需 20-40 秒）…")
    try:
        import jieba
        from pypinyin import Style, lazy_pinyin

        jieba.initialize()
        dict_path = os.path.join(os.path.dirname(jieba.__file__), "dict.txt")
        if not os.path.exists(dict_path):
            print("[AI Memory IME] 找不到 jieba 词典，跳过")
            _ime_ready.set()
            return

        # 读取词典并按词频降序排列
        entries: List = []
        with open(dict_path, encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        entries.append((parts[0], int(parts[1])))
                    except ValueError:
                        pass
        entries.sort(key=lambda x: -x[1])  # 高频词优先

        idx: Dict[str, List[str]] = {}

        def add_entry(key: str, word: str) -> None:
            if key not in idx:
                idx[key] = []
            if word not in idx[key]:
                idx[key].append(word)

        seen: set = set()
        for word, freq in entries:
            if freq < 3:
                continue
            wlen = len(word)
            if wlen == 1:
                # 单字：直接用拼音作 key，频率已保证顺序
                if word in seen:
                    continue
                seen.add(word)
                py_parts = lazy_pinyin(word, style=Style.NORMAL, errors="ignore")
                if not py_parts:
                    continue
                add_entry(py_parts[0], word)
                continue
            if not (wlen > 1):
                continue
            if word in seen:
                continue
            seen.add(word)
            py_parts = lazy_pinyin(word, style=Style.NORMAL, errors="ignore")
            if not py_parts:
                continue
            full_py = "".join(py_parts)
            abbr_py = "".join(p[0] for p in py_parts if p)
            add_entry(full_py, word)
            if abbr_py != full_py:
                add_entry(abbr_py, word)

        keys = sorted(idx.keys())
        _ime_index = idx
        _ime_keys  = keys

        # 持久化缓存
        os.makedirs(_ime_cache_dir, exist_ok=True)
        with open(cache_path, "wb") as f:
            pickle.dump({"index": idx, "keys": keys}, f)

        _ime_ready.set()
        print(f"[AI Memory IME] 拼音索引构建完成（共 {len(idx)} 条拼音，{len(seen)} 个词语）")
    except ImportError as e:
        print(f"[AI Memory IME] 依赖缺失: {e}，将使用 JS 内置字典")
        _ime_ready.set()
    except Exception as e:
        print(f"[AI Memory IME] 索引构建失败: {e}")
        _ime_ready.set()


def _lookup_ime(q: str, limit: int = 9) -> List[str]:
    """在拼音索引中查找候选词（精确匹配 + 前缀二分查找）。"""
    if not q or not _ime_index:
        return []
    seen: set = set()
    result: List[str] = []

    def absorb(words: List[str]) -> bool:
        for w in words:
            if w not in seen:
                seen.add(w)
                result.append(w)
        return len(result) >= limit

    # 精确匹配
    if q in _ime_index:
        if absorb(_ime_index[q]):
            return result[:limit]

    # 前缀匹配（二分定位起点，线性扫描同前缀段）
    lo = bisect.bisect_left(_ime_keys, q)
    for k in _ime_keys[lo:]:
        if not k.startswith(q):
            break
        if k != q and absorb(_ime_index[k]):
            return result[:limit]

    return result[:limit]


def _start_file_server(directory: str, port: int) -> None:
    """在后台线程启动本地静态文件服务器，仅绑定 127.0.0.1。
    同时处理 /api/ime 请求（拼音候选词查询 JSON 接口）。
    """

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def do_GET(self):
            if self.path.startswith("/api/ime"):
                self._handle_ime()
            else:
                super().do_GET()

        def _handle_ime(self):
            try:
                qs    = parse_qs(urlparse(self.path).query)
                q     = (qs.get("q", [""])[0]).strip().lower()
                limit = min(50, int(qs.get("limit", ["9"])[0]))
                cands = _lookup_ime(q, limit)
                body  = json.dumps(
                    {"cands": cands, "ready": _ime_ready.is_set()},
                    ensure_ascii=False,
                ).encode("utf-8")
            except Exception:
                body = b'{"cands":[],"ready":false}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # 静默，不打印访问日志

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()


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

    def test_connection(self) -> Dict:
        return self._core.test_connection()

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

    def _stream_thread(
        self,
        conv_id: str,
        content: str,
        options: Dict,
        stop_flag: threading.Event,
    ) -> None:
        import time
        w = webview.windows[0]
        # 流式输出节流：把短时间内的 delta 事件合并批量推送，
        # 减少高频 evaluate_js 调用，降低 UI 卡顿
        _FLUSH_INTERVAL = 0.03   # 30 ms / 帧
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
                # 非 delta 事件（done/error/title_update）立即发送
                if event.get("type") != "delta":
                    _flush()
                    payload = json.dumps(event, ensure_ascii=False)
                    w.evaluate_js(f"window.__onStreamEvent({payload})")
                else:
                    buf.append(event)
                    if time.monotonic() - last_flush >= _FLUSH_INTERVAL:
                        _flush()
                if stop_flag.is_set():
                    _flush()
                    cancel_evt = json.dumps({"type": "cancelled", "conv_id": conv_id})
                    w.evaluate_js(f"window.__onStreamEvent({cancel_evt})")
                    return
            _flush()
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


# ──────────────────────────────────────────────────────────────────────────────

def _setup_ime() -> None:
    """禁用 OS IM 模块，由前端 JS 内置拼音处理。

    WSLg + QtWebEngine 环境中，系统 IME（fcitx5/ibus）存在两道死墙：
      1. WSLg Wayland 合成器不支持 zwp_input_method_v1 协议，fcitx5 启动即崩溃；
      2. 系统 fcitx5-frontend-qt6 编译自系统 Qt（6.4.x），与 PyQt6 内置 Qt（6.10.x）
         ABI 不兼容，插件无法加载。
    JS 内置拼音在所有平台（WSL2/Linux/Windows）均可正常工作，Ctrl+Space 切换。
    """
    # "none" 是 Qt 明确禁用 IM 的值；空字符串会回退到平台默认 IM 并消费 Ctrl+Space
    os.environ["QT_IM_MODULE"] = "none"
    if sys.platform != "win32":
        os.environ["GTK_IM_MODULE"] = "none"
        os.environ["XMODIFIERS"]    = ""
    print("[AI Memory] IME: JS 内置拼音  切换快捷键: Ctrl+Space")


def _get_system_scale() -> float:
    """尝试读取系统 DPI/缩放比例，失败时返回 1.25。"""
    # Windows：通过 ctypes 读取物理 DPI
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            dc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, dc)
            return round(dpi / 96.0, 2)
        except Exception:
            return 1.0
    # 1. GTK / Wayland 环境变量
    for env in ("GDK_SCALE", "QT_SCALE_FACTOR"):
        val = os.environ.get(env, "").strip()
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    # 2. GNOME gsettings text-scaling-factor
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "text-scaling-factor"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return 1.25


def main() -> None:
    if not os.path.isdir(CLIENT_DIR):
        print(f"[错误] 找不到客户端目录: {CLIENT_DIR}")
        print("请确保 client/index.html 存在后再启动。")
        return

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

    _setup_ime()
    threading.Thread(target=_build_ime_index, daemon=True).start()
    if sys.platform != "win32":
        print(f"[AI Memory] GTK_IM_MODULE = {os.environ.get('GTK_IM_MODULE', 'NOT SET')}")

    port = _find_free_port(9127)
    _start_file_server(CLIENT_DIR, port)
    print(f"[AI Memory] 本地文件服务已启动: http://127.0.0.1:{port}/")

    scale = _get_system_scale()
    print(f"[AI Memory] 系统缩放比例: {scale}")

    api = AppApi()

    window = webview.create_window(
        title="AI Memory",
        url=f"http://127.0.0.1:{port}/?scale={scale}",
        js_api=api,
        width=1600,
        height=1000,
        min_size=(800, 600),
        background_color="#0d1117",
        text_select=True,
        zoomable=True,
    )

    webview.start(debug=False, gui="qt")


if __name__ == "__main__":
    main()
