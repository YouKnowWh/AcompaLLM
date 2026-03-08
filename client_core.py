"""AcompaLLM Desktop Client — Core Logic

处理配置、对话存储和上游 API 通信。
将网关逻辑（工具调用、搜索、RAG）直接内嵌，无需独立服务器。

"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
import threading
from typing import Any, Dict, Generator, List, Optional

import httpx

# ─── 可选适配器（与 APIAgent.py 相同策略）────────────────────────────────────
try:
    from tools_adapter import invoke_tool as _invoke_tool  # type: ignore
except Exception:
    _invoke_tool = None

try:
    from rag_adapter import retrieve_context as _retrieve_context  # type: ignore
except Exception:
    _retrieve_context = None

try:
    from rag_adapter import add_document as _add_document  # type: ignore
except Exception:
    _add_document = None

try:
    from kb import agentic_search as _agentic_search  # type: ignore
except Exception:
    _agentic_search = None

# ─── 存储路径 ─────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(_BASE_DIR, "data")
CHATS_DIR  = os.path.join(DATA_DIR, "chats")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
os.makedirs(CHATS_DIR, exist_ok=True)

# ─── 模型提供商预设 ───────────────────────────────────────────────────────────
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "deepseek": {"name": "DeepSeek",   "url": "https://api.deepseek.com",                               "no_key": False},
    "openai":   {"name": "OpenAI",     "url": "https://api.openai.com",                                 "no_key": False},
    "gemini":   {"name": "Gemini",     "url": "https://generativelanguage.googleapis.com/v1beta/openai","no_key": False},
    "qwen":     {"name": "通义千问",   "url": "https://dashscope.aliyuncs.com/compatible-mode/v1",      "no_key": False},
    "ollama":   {"name": "Ollama",     "url": "http://localhost:11434/v1",                               "no_key": True},
    "custom":   {"name": "自定义",     "url": "",                                                        "no_key": False},
}

# ─── 默认配置 ─────────────────────────────────────────────────────────────────
DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": "deepseek",
    "upstream_base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 0.7,
    "tool_web_search": "auto",          # "auto" | "true" | "false"
    "tool_web_search_engine": "ddg",    # "ddg" | "tavily" | "bing" | "brave" | "serp"
    "tool_tavily_key": "",
    "tool_bing_key": "",
    "tool_brave_key": "",
    "tool_serp_key": "",
    "tool_rag": False,
    "system_prompt": "",
    "theme": "dark",
}

# ─── 从 APIAgent.py 提取的共享辅助函数 ───────────────────────────────────────
# 触发联网搜索的关键词（与 APIAgent.py 保持一致）
_SEARCH_TRIGGERS = [
    "今天", "今日", "现在", "最新", "最近", "当前", "此刻",
    "实时", "新闻", "头条", "天气", "股价", "价格", "汇率",
    "今年", "本周", "本月", "刚刚", "发生了", "怎么了",
    "latest", "today", "now", "current", "news", "price", "weather",
    "帮我查", "查一下", "查查", "搜一下", "搜搜", "搜索",
    "查找", "查询", "找一下", "找找", "检索",
    "网上", "网络", "互联网", "在线",
    "search for", "look up", "find out", "google",
    "权威", "官方", "来源", "出处", "参考", "根据资料",
]

_DSML_RE = re.compile(r'<｜DSML｜.*?｜DSML｜>', re.DOTALL)


def _needs_search(text: str) -> bool:
    """判断用户消息是否需要联网搜索（与 APIAgent.py 逻辑相同）。"""
    t = text.lower()
    return any(kw in t for kw in _SEARCH_TRIGGERS)


_DEEP_THINK_TRIGGERS = [
    "深度思考", "深思", "仔细想", "认真想", "仔细分析", "深入分析",
    "用reasoner", "启用reasoner", "开启reasoner", "深度推理", "用r1", "启用r1",
    "think harder", "think deeply", "use reasoner", "deep think",
]


def _needs_deep_think(text: str) -> bool:
    """判断用户是否明确要求深度推理。"""
    t = text.lower()
    return any(kw in t for kw in _DEEP_THINK_TRIGGERS)


# 具有原生思考模式 API 的厂商（无需 system prompt 注入）
_NATIVE_THINK_PROVIDERS = {"deepseek", "openai", "qwen", "gemini"}


def _apply_deep_think(provider: str, model: str, payload: Dict) -> tuple:
    """按厂商为 payload 配置原生深度思考参数，返回 (updated_payload, effective_model)。

    各厂商实现：
      deepseek → 切换模型 deepseek-reasoner
      openai   → o 系列模型添加 reasoning_effort: "high"，去掉 temperature
      qwen     → payload 加 enable_thinking: true
      gemini   → payload 加 thinking_config.thinking_budget
      其他     → 不修改（由调用方通过 system prompt 注入回退）
    """
    if provider == "deepseek":
        return payload, "deepseek-reasoner"

    if provider == "openai":
        # o1 / o3 / o4-mini 等 o 系列推理模型
        if re.match(r"^o\d", model):
            payload.pop("temperature", None)
            payload["reasoning_effort"] = "high"
        return payload, model

    if provider == "qwen":
        payload["enable_thinking"] = True
        return payload, model

    if provider == "gemini":
        payload["thinking_config"] = {"thinking_budget": 8192}
        return payload, model

    return payload, model


def _strip_dsml(text: str) -> str:
    """清除 DeepSeek 内部 DSML 标记（与 APIAgent.py 逻辑相同）。"""
    if '｜DSML｜' not in text:
        return text
    text = _DSML_RE.sub('', text)
    return '\n'.join(ln for ln in text.splitlines() if '｜DSML｜' not in ln).strip()


def _format_search_results(results: List[Dict]) -> str:
    """格式化搜索结果（与 APIAgent.py 逻辑相同）。"""
    import datetime
    if not results:
        return ""
    lines = [f"[联网搜索结果 — {datetime.date.today()}]"]
    for i, r in enumerate(results, 1):
        title   = r.get("title", "")
        snippet = r.get("snippet") or r.get("body") or ""
        url     = r.get("url") or r.get("href") or ""
        lines.append(f"{i}. {title}\n   {snippet}\n   来源: {url}")
    return "\n".join(lines)


def _inject_context_to_system(messages: List[Dict], context_str: str) -> List[Dict]:
    """将工具/RAG 上下文注入 system 消息末尾；若无 system 消息则在首位插入。"""
    messages = list(messages)
    injection = (
        f"\n\n[实时搜索结果]\n{context_str}\n"
        "——搜索已完成。请直接输出回答正文。"
    )
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            messages[i] = {**m, "content": (m.get("content") or "") + injection}
            return messages
    messages.insert(0, {"role": "system", "content": injection.lstrip()})
    return messages


# ─── Config ───────────────────────────────────────────────────────────────────
class Config:
    def __init__(self) -> None:
        self._data: Dict[str, Any] = {**DEFAULT_CONFIG}
        self._load()

    def _load(self) -> None:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self._data.update(json.load(f))
            except Exception:
                pass

    def get(self) -> Dict[str, Any]:
        return {**self._data}

    def save(self, updates: Dict[str, Any]) -> bool:
        # 不允许空 api_key 覆盖已保存的值
        if "api_key" in updates and not str(updates.get("api_key", "")).strip():
            updates = {k: v for k, v in updates.items() if k != "api_key"}
        self._data.update(updates)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @property
    def api_key(self) -> str:
        return (
            self._data.get("api_key", "")
            or os.getenv("DEEPSEEK_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", "")
        ).strip()

    @property
    def base_url(self) -> str:
        return (self._data.get("upstream_base_url") or "https://api.deepseek.com").rstrip("/")

    @property
    def model(self) -> str:
        return self._data.get("model") or "deepseek-chat"

    @property
    def temperature(self) -> float:
        try:
            return float(self._data.get("temperature", 0.7))
        except (TypeError, ValueError):
            return 0.7


# ─── 对话存储 ─────────────────────────────────────────────────────────────────
class ConversationStore:
    def list_all(self) -> List[Dict]:
        result: List[Dict] = []
        for fname in os.listdir(CHATS_DIR):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(CHATS_DIR, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
                result.append({
                    "id":            data["id"],
                    "title":         data.get("title", "New Chat"),
                    "updated_at":    data.get("updated_at", 0),
                    "message_count": len(data.get("messages", [])),
                })
            except Exception:
                pass
        result.sort(key=lambda x: x["updated_at"], reverse=True)
        return result

    def get(self, conv_id: str) -> Optional[Dict]:
        path = os.path.join(CHATS_DIR, f"{conv_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def create(self) -> Dict:
        now = int(time.time())
        conv: Dict[str, Any] = {
            "id":         "conv_" + uuid.uuid4().hex[:12],
            "title":      "New Chat",
            "created_at": now,
            "updated_at": now,
            "messages":   [],
        }
        self._write(conv)
        return conv

    def save(self, conv: Dict) -> bool:
        conv["updated_at"] = int(time.time())
        return self._write(conv)

    def _write(self, conv: Dict) -> bool:
        try:
            with open(os.path.join(CHATS_DIR, f"{conv['id']}.json"), "w", encoding="utf-8") as f:
                json.dump(conv, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def delete(self, conv_id: str) -> bool:
        try:
            os.remove(os.path.join(CHATS_DIR, f"{conv_id}.json"))
            return True
        except Exception:
            return False

    def rename(self, conv_id: str, title: str) -> bool:
        conv = self.get(conv_id)
        if not conv:
            return False
        conv["title"] = title.strip()[:80] or "New Chat"
        return self.save(conv)

    def update(self, conv_id: str, updates: Dict) -> bool:
        conv = self.get(conv_id)
        if not conv:
            return False
        if "title" in updates:
            conv["title"] = (updates["title"] or "").strip()[:80] or "New Chat"
        if "system_prompt" in updates:
            v = updates["system_prompt"]
            conv["system_prompt"] = v if v is not None else ""
        if "temperature" in updates:
            conv["temperature"] = updates["temperature"]  # None → use global
        return self.save(conv)


# ─── 上游 API 客户端 ──────────────────────────────────────────────────────────
class UpstreamClient:
    """同步 httpx 封装，供后台线程调用（不阻塞 GUI 主线程）。"""

    def __init__(self, config: Config) -> None:
        self.config = config

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type":  "application/json",
        }

    def test_connection(self) -> Dict:
        is_local = any(h in self.config.base_url for h in ("localhost", "127.0.0.1", "::1"))
        if not self.config.api_key and not is_local:
            return {"ok": False, "message": "未配置 API Key"}
        try:
            with httpx.Client(timeout=8.0) as c:
                resp = c.get(f"{self.config.base_url}/v1/models", headers=self._headers())
            if resp.status_code == 200:
                return {"ok": True, "message": "连接成功"}
            return {"ok": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def list_models(self) -> List[Dict]:
        fallback = [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]
        is_local = any(h in self.config.base_url for h in ("localhost", "127.0.0.1", "::1"))
        if not self.config.api_key and not is_local:
            return fallback
        try:
            with httpx.Client(timeout=8.0) as c:
                resp = c.get(f"{self.config.base_url}/v1/models", headers=self._headers())
            if resp.status_code == 200:
                return resp.json().get("data", fallback)
        except Exception:
            pass
        return fallback

    def simple_chat(
        self,
        messages: List[Dict],
        model: str = "",
        temperature: float = 0.0,
    ) -> str:
        """同步非流式 LLM 调用，返回助手回复文本。用于 Agentic RAG 查询改写/反思。"""
        _model = model or self.config.model
        payload = {
            "model":       _model,
            "messages":    messages,
            "temperature": temperature,
            "stream":      False,
        }
        try:
            with httpx.Client(timeout=30.0) as c:
                resp = c.post(
                    f"{self.config.base_url}/v1/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
            resp.raise_for_status()
            data = resp.json()
            return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        except Exception:
            return ""

    def stream_chat(
        self,
        messages: List[Dict],
        model: str,
        temperature: float,
        stop_flag: threading.Event,
        deep_think: bool = False,
        provider: str = "",
    ) -> Generator:
        """
        生成器，yield (event_type, data) 元组：
          ("reasoning", str)  — 推理过程片段
          ("content",   str)  — 正文片段
          ("usage",     dict) — token 用量
          ("error",     str)  — 错误信息
        """
        payload = {
            "model":       model,
            "messages":    messages,
            "temperature": temperature,
            "stream":      True,
        }
        if deep_think:
            payload, model = _apply_deep_think(provider, model, payload)
            payload["model"] = model  # 更新（deepseek 可能切换了模型名）
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0)
            ) as c:
                with c.stream(
                    "POST",
                    f"{self.config.base_url}/v1/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as resp:
                    if resp.status_code >= 400:
                        err = resp.read().decode("utf-8", errors="ignore")
                        try:
                            msg = json.loads(err).get("error", {}).get("message", err)
                        except Exception:
                            msg = err
                        yield ("error", msg[:400])
                        return

                    for line in resp.iter_lines():
                        if stop_flag.is_set():
                            return
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            return
                        try:
                            data   = json.loads(data_str)
                            if data.get("usage"):
                                yield ("usage", data["usage"])
                            choice = (data.get("choices") or [{}])[0]
                            delta  = choice.get("delta", {})
                            rc = _strip_dsml(delta.get("reasoning_content") or "")
                            ct = _strip_dsml(delta.get("content") or "")
                            if rc:
                                yield ("reasoning", rc)
                            if ct:
                                yield ("content", ct)
                        except Exception:
                            pass
        except httpx.TimeoutException:
            yield ("error", "连接超时，请检查网络或 API 地址")
        except Exception as e:
            yield ("error", str(e))


# ─── ClientCore：总入口 ───────────────────────────────────────────────────────
class ClientCore:
    def __init__(self) -> None:
        self.config   = Config()
        self.store    = ConversationStore()
        self._upstream = UpstreamClient(self.config)

    # ── 配置 ──────────────────────────────────────────────────────────────────
    def get_providers(self) -> Dict:
        return PROVIDERS

    def get_config(self) -> Dict:
        cfg = self.config.get()
        key = cfg.get("api_key", "")
        # 返回掩码版本，不暴露完整 Key 到前端
        cfg["api_key_masked"] = (key[:3] + "·" * 6 + key[-3:]) if len(key) > 8 else ("••••••" if key else "")
        return cfg

    def save_config(self, updates: Dict) -> bool:
        result = self.config.save(updates)
        # 配置更新后刷新上游客户端
        self._upstream = UpstreamClient(self.config)
        return result

    def test_connection(self) -> Dict:
        return self._upstream.test_connection()

    def test_web_search(self) -> Dict:
        cfg    = self.get_config()
        engine = cfg.get("tool_web_search_engine", "ddg")
        key_map = {
            "tavily": cfg.get("tool_tavily_key", ""),
            "bing":   cfg.get("tool_bing_key", ""),
            "brave":  cfg.get("tool_brave_key", ""),
            "serp":   cfg.get("tool_serp_key", ""),
        }
        api_key = key_map.get(engine, "")
        try:
            from tools_adapter import invoke_tool
            results = invoke_tool("web_search", {"query": "test", "engine": engine, "api_key": api_key, "max_results": 1})
            if isinstance(results, list) and results:
                return {"ok": True, "message": f"引擎 [{engine}] 可用，已成功获取结果"}
            elif isinstance(results, dict) and results.get("error"):
                return {"ok": False, "message": results["error"]}
            else:
                return {"ok": False, "message": "搜索未返回结果"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def list_upstream_models(self) -> List[Dict]:
        return self._upstream.list_models()

    # ── 对话管理 ──────────────────────────────────────────────────────────────
    def list_conversations(self) -> List[Dict]:
        return self.store.list_all()

    def get_conversation(self, conv_id: str) -> Optional[Dict]:
        return self.store.get(conv_id)

    def new_conversation(self) -> Dict:
        return self.store.create()

    def delete_conversation(self, conv_id: str) -> bool:
        return self.store.delete(conv_id)

    def rename_conversation(self, conv_id: str, title: str) -> bool:
        return self.store.rename(conv_id, title)

    def update_conversation(self, conv_id: str, updates: Dict) -> bool:
        return self.store.update(conv_id, updates)

    def clear_conversation(self, conv_id: str) -> bool:
        conv = self.store.get(conv_id)
        if not conv:
            return False
        conv["messages"] = []
        return self.store.save(conv)

    def delete_message(self, conv_id: str, msg_id: str) -> bool:
        conv = self.store.get(conv_id)
        if not conv:
            return False
        conv["messages"] = [m for m in conv["messages"] if m.get("id") != msg_id]
        return self.store.save(conv)

    # ── 记忆库 ────────────────────────────────────────────────────────────────
    def add_to_memory(self, text: str, title: str = "", source: str = "") -> bool:
        if _add_document is None:
            return False
        try:
            _add_document(text, title=title, source=source)
            return True
        except Exception:
            return False

    # ── 知识库管理 ────────────────────────────────────────────────────────────
    def list_kb_collections(self) -> List[Dict]:
        try:
            import kb
            return kb.list_collections()
        except Exception:
            return []

    def kb_ingest_file(self, path: str, name: str = "", embed_model: str = "") -> Dict:
        try:
            import kb
            result = kb.ingest_file(path, name=name or None, embed_model=embed_model or None)
            return {"ok": True, **result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kb_ingest_folder(self, folder: str, name: str = "", embed_model: str = "") -> Dict:
        try:
            import kb
            results = kb.ingest_folder(folder, name=name or None, embed_model=embed_model or None)
            total_chunks = sum(r.get("chunks", 0) for r in results)
            first_name = results[0]["name"] if results else (name or folder)
            return {"ok": True, "chunks": total_chunks, "files": len(results), "name": first_name}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kb_ingest_url(self, url: str, name: str, embed_model: str = "") -> Dict:
        try:
            import kb
            result = kb.ingest_url(url, name, embed_model=embed_model or None)
            return {"ok": True, **result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kb_delete(self, name: str) -> bool:
        try:
            import kb
            return kb.delete_collection(name)
        except Exception:
            return False

    def kb_list_sources(self, collection_name: str) -> List[Dict]:
        try:
            import kb
            return kb.list_sources(collection_name)
        except Exception:
            return []

    def kb_delete_source(self, collection_name: str, source: str) -> Dict:
        try:
            import kb
            deleted = kb.delete_source(collection_name, source)
            return {"ok": True, "deleted": deleted}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 流式消息发送 ──────────────────────────────────────────────────────────
    def stream_message(
        self,
        conv_id: str,
        user_text: str,
        options: Dict,
        stop_flag: threading.Event,
    ) -> Generator:
        """
        生成器，yield 流事件 dict，供 desktop_app.py 的后台线程消费：
          {type: "start",      message_id, user_message_id, model}
          {type: "tool_start", tool, query}
          {type: "tool_done",  tool, query, results, elapsed}
          {type: "tool_error", tool, error}
          {type: "reasoning_chunk", text}
          {type: "content_chunk",   text}
          {type: "done",       usage, message_id, model}
          {type: "error",      message}
          {type: "title_update", conv_id, title}
        """
        conv = self.store.get(conv_id)
        if not conv:
            yield {"type": "error", "message": f"找不到对话 {conv_id}"}
            return

        if not self.config.api_key:
            is_local = any(h in self.config.base_url for h in ("localhost", "127.0.0.1", "::1"))
            if not is_local:
                yield {"type": "error", "message": "请先在设置中填写 API Key"}
                return

        model         = options.get("model")       or self.config.model
        _c_temp = conv.get("temperature")  # None 表示未单独设置
        temperature   = float(options.get("temperature") or (_c_temp if _c_temp is not None else self.config.temperature))
        web_search_mode = options.get("tool_web_search") or self.config.get().get("tool_web_search", "auto")
        use_rag       = options.get("tool_rag") or self.config.get().get("tool_rag", False)

        # ── 生成 ID（提前，确保 start 事件可立即发出）─────────────────────────
        user_msg_id = "msg_" + uuid.uuid4().hex[:10]
        ai_msg_id   = "msg_" + uuid.uuid4().hex[:10]

        # ── 构建消息列表 ──────────────────────────────────────────────────────
        history: List[Dict] = []
        _c_sys = conv.get("system_prompt")
        sys_prompt = (_c_sys if _c_sys is not None else self.config.get().get("system_prompt", "")).strip()
        if sys_prompt:
            history.append({"role": "system", "content": sys_prompt})

        for m in conv.get("messages", []):
            if m.get("role") not in ("user", "assistant", "system"):
                continue
            history.append({"role": m["role"], "content": m.get("content") or ""})

        history.append({"role": "user", "content": user_text})

        # ── 立即发出 start，让前端建立消息气泡（工具块将插入其中）────────────
        yield {"type": "start", "message_id": ai_msg_id, "user_message_id": user_msg_id, "model": model}

        # ── 前置工具执行 ──────────────────────────────────────────────────────
        import datetime as _dt
        context_parts: List[str] = [
            f"[当前日期] 今天是 {_dt.date.today().strftime('%Y年%m月%d日')}，请以此为准回答所有时间相关问题。"
        ]
        cfg       = self.config.get()
        web_engine   = cfg.get("tool_web_search_engine", "ddg")
        tavily_key   = cfg.get("tool_tavily_key", "")
        bing_key     = cfg.get("tool_bing_key", "")
        brave_key    = cfg.get("tool_brave_key", "")
        serp_key     = cfg.get("tool_serp_key", "")
        # Route the engine-specific key
        _engine_key_map = {"tavily": tavily_key, "bing": bing_key, "brave": brave_key, "serp": serp_key}
        engine_api_key  = _engine_key_map.get(web_engine, "")

        should_search = (
            web_search_mode == "true"
            or (web_search_mode == "auto" and _needs_search(user_text))
        )
        if should_search:
            if _invoke_tool is not None:
                search_query = user_text[:200]
                t0 = time.time()
                yield {"type": "tool_start", "tool": "web_search", "query": search_query}
                try:
                    raw = _invoke_tool("web_search", {
                        "query":       search_query,
                        "max_results": 5,
                        "engine":      web_engine,
                        "api_key":     engine_api_key,
                    })
                    elapsed = round((time.time() - t0) * 1000)
                    results = raw if isinstance(raw, list) else None
                    yield {"type": "tool_done", "tool": "web_search",
                           "query": search_query, "results": results, "elapsed": elapsed}
                    context_parts.append(
                        _format_search_results(raw) if isinstance(raw, list) else str(raw)
                    )
                except Exception as exc:
                    yield {"type": "tool_error", "tool": "web_search", "error": str(exc)}
            else:
                yield {"type": "tool_error", "tool": "web_search", "error": "搜索插件未安装，请运行: pip install ddgs"}

        # ── [Level 3 预留槽位] 记忆库独立检索 ─────────────────────────────────
        # memory_hits = _memory_search(user_text)  # 记忆库系统（待实现）

        if use_rag:
            yield {"type": "tool_start", "tool": "rag", "query": user_text[:100]}
            try:
                t0 = time.time()
                if _agentic_search is not None:
                    # Agentic RAG Level 2：查询改写 → 检索 → 反思迭代
                    def _llm_call(msgs: List[Dict]) -> str:
                        return self._upstream.simple_chat(msgs, model=model, temperature=0.0)
                    hits = list(_agentic_search(user_text, _llm_call, top_k=5))
                elif _retrieve_context is not None:
                    # 降级：普通向量检索
                    hits = list(_retrieve_context(user_text, top_k=3))
                else:
                    hits = []
                elapsed = round((time.time() - t0) * 1000)
                if hits:
                    rag_lines = [
                        f"- {h.get('title') or h.get('source') or '片段'}: "
                        f"{h.get('body') or h.get('text') or str(h)}"
                        for h in hits
                    ]
                    context_parts.append("[知识库相关内容]\n" + "\n".join(rag_lines))
                yield {"type": "tool_done", "tool": "rag",
                       "query": user_text[:100], "results": hits if hits else None, "elapsed": elapsed}
            except Exception as exc:
                yield {"type": "tool_error", "tool": "rag", "error": str(exc)}

        if context_parts:
            history = _inject_context_to_system(history, "\n\n".join(context_parts))

        # ── 深度思考 ─────────────────────────────────────────────────────────
        deep_think = options.get("deep_think") or _needs_deep_think(user_text)
        provider   = cfg.get("provider", "")
        # 无原生思考 API 的厂商：降级到 system prompt 注入
        if deep_think and provider not in _NATIVE_THINK_PROVIDERS:
            history = _inject_context_to_system(
                history,
                "用户希望你启动深度思考模式，请尽可能进行详细的内心推理和分析再回答。"
            )

        # ── 保存用户消息 ──────────────────────────────────────────────────────
        now = int(time.time())
        user_entry: Dict[str, Any] = {
            "id":         user_msg_id,
            "role":       "user",
            "content":    user_text,
            "created_at": now,
        }
        conv["messages"].append(user_entry)

        if len(conv["messages"]) == 1 and conv.get("title") == "New Chat":
            title = user_text.strip()[:50]
            if len(user_text) > 50:
                title += "…"
            conv["title"] = title or "New Chat"
            self.store.save(conv)
            yield {"type": "title_update", "conv_id": conv_id, "title": conv["title"]}
        else:
            self.store.save(conv)

        # ── 流式上游调用 ──────────────────────────────────────────────────────
        full_content   = ""
        full_reasoning = ""
        usage: Dict    = {}

        for ev_type, ev_data in self._upstream.stream_chat(
            history, model, temperature, stop_flag,
            deep_think=deep_think, provider=provider
        ):
            if stop_flag.is_set():
                break
            if ev_type == "reasoning":
                full_reasoning += ev_data
                yield {"type": "reasoning_chunk", "text": ev_data}
            elif ev_type == "content":
                full_content += ev_data
                yield {"type": "content_chunk", "text": ev_data}
            elif ev_type == "usage":
                usage = ev_data
            elif ev_type == "error":
                yield {"type": "error", "message": ev_data}
                break

        # ── 保存 AI 消息 ──────────────────────────────────────────────────────
        ai_entry: Dict[str, Any] = {
            "id":                ai_msg_id,
            "role":              "assistant",
            "content":           full_content,
            "reasoning_content": full_reasoning,
            "model":             model,
            "usage":             usage,
            "created_at":        int(time.time()),
        }
        conv["messages"].append(ai_entry)
        self.store.save(conv)

        yield {"type": "done", "message_id": ai_msg_id, "usage": usage, "model": model}
