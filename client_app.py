"""AI Memory Desktop Client — 应用入口

启动方式：
    python client_app.py
"""

from __future__ import annotations

import base64
import json
import os
import threading

import webview  # pip install pywebview

from client_core import ClientCore


class ClientAPI:
    """暴露给 JavaScript 的 Python API（通过 window.pywebview.api.* 调用）。"""

    def __init__(self, core: ClientCore) -> None:
        self._core      = core
        self._window: webview.Window | None = None
        self._stop_flag = threading.Event()

    # ── 配置 ──────────────────────────────────────────────────────────────────
    def get_config(self) -> dict:
        return self._core.get_config()

    def save_config(self, config: dict) -> bool:
        return self._core.save_config(config)

    def test_connection(self) -> dict:
        return self._core.test_connection()

    def list_upstream_models(self) -> list:
        return self._core.list_upstream_models()

    # ── 对话 ──────────────────────────────────────────────────────────────────
    def list_conversations(self) -> list:
        return self._core.list_conversations()

    def get_conversation(self, conv_id: str) -> dict | None:
        return self._core.get_conversation(conv_id)

    def new_conversation(self) -> dict:
        return self._core.new_conversation()

    def delete_conversation(self, conv_id: str) -> bool:
        return self._core.delete_conversation(conv_id)

    def rename_conversation(self, conv_id: str, title: str) -> bool:
        return self._core.rename_conversation(conv_id, title)

    def clear_conversation(self, conv_id: str) -> bool:
        return self._core.clear_conversation(conv_id)

    def delete_message(self, conv_id: str, msg_id: str) -> bool:
        return self._core.delete_message(conv_id, msg_id)

    # ── 聊天（非阻塞，流式） ──────────────────────────────────────────────────
    def send_message(self, conv_id: str, message: str, options: dict = None) -> str:
        """立即返回 "ok"，在后台线程开始流式请求。"""
        self._stop_flag.clear()
        threading.Thread(
            target=self._stream_worker,
            args=(conv_id, message, options or {}),
            daemon=True,
        ).start()
        return "ok"

    def stop_generation(self) -> bool:
        self._stop_flag.set()
        return True

    def _stream_worker(self, conv_id: str, message: str, options: dict) -> None:
        try:
            for event in self._core.stream_message(conv_id, message, options, self._stop_flag):
                if self._stop_flag.is_set():
                    self._emit({"type": "stopped"})
                    return
                self._emit(event)
        except Exception as exc:
            self._emit({"type": "error", "message": str(exc)})

    def _emit(self, event: dict) -> None:
        """将事件序列化为 base64-JSON 并在 JS 窗口中执行（线程安全）。"""
        if not self._window:
            return
        b64 = base64.b64encode(
            json.dumps(event, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        self._window.evaluate_js(f"window.__onStreamEvent(JSON.parse(atob('{b64}')))")

    # ── 记忆库 ────────────────────────────────────────────────────────────────
    def add_to_memory(self, text: str, title: str = "", source: str = "") -> bool:
        return self._core.add_to_memory(text, title, source)


def main() -> None:
    core = ClientCore()
    api  = ClientAPI(core)

    ui_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "client_ui", "index.html"
    )

    window = webview.create_window(
        title            = "AI Memory",
        url              = f"file://{ui_path}",
        js_api           = api,
        width            = 1280,
        height           = 840,
        min_size         = (900, 600),
        background_color = "#0d1117",
    )
    api._window = window

    webview.start(debug=False)


if __name__ == "__main__":
    main()
