"""AcompaLLM Desktop Client — Core Logic

处理配置、对话存储和上游 API 通信。
将网关逻辑（工具调用、搜索、RAG）直接内嵌，无需独立服务器。

"""

from __future__ import annotations

import json
import logging
import os
import base64
import mimetypes
import queue
import re
import time
import uuid
import threading
from typing import Any, Dict, Generator, List, Optional

import httpx

# ─── 核心逻辑导入 ─────────────────────────────────────────────────────
from app.core_logic import _SEARCH_TRIGGERS, _DEEP_THINK_TRIGGERS, _needs_search, _needs_deep_think, _strip_dsml, _format_search_results, _inject_context_to_system

# ─── 可选适配器（使用安全的导入工具）────────────────────────────────────
from utils.imports import safe_import

# 设置日志记录（如果尚未设置）
logger = logging.getLogger(__name__)

# 安全导入工具适配器
_invoke_tool = safe_import(
    "app.tools_adapter",
    "invoke_tool",
    reason="工具调用功能将不可用"
)

_retrieve_context = safe_import(
    "app.rag_adapter",
    "retrieve_context",
    reason="RAG 检索功能将不可用"
)

_add_document = safe_import(
    "app.rag_adapter",
    "add_document",
    reason="文档添加功能将不可用"
)

_agentic_search = safe_import(
    "kb",
    "agentic_search",
    reason="知识库代理搜索功能将不可用"
)

# ─── 存储路径 ─────────────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    "api_keys": {},            # {"deepseek": "sk-...", "openai": "sk-...", ...}
    "upstream_models_map": {},
    "model": "deepseek-chat",
    "temperature": 0.7,
    "tool_web_search": "auto",          # "auto" | "true" | "false"
    "tool_web_search_engine": "ddg",    # "ddg" | "tavily" | "bing" | "brave" | "serp"
    "tool_tavily_key": "",
    "tool_bing_key": "",
    "tool_brave_key": "",
    "tool_serp_key": "",
    "tool_rag": False,
    "kb_embed_provider": "default",
    "kb_embed_model": "BAAI/bge-small-zh-v1.5",
    "kb_embed_base_url": "",
    "kb_embed_api_keys": {},
    "kb_embed_api_key": "",
    "kb_embed_models_map": {},
    "system_prompt": "",
    "theme": "dark",
    "background": {                     # 主题和背景配置
        "theme": "dark",                # "dark" | "light" | "auto"
        "type": "color",                # "color" | "gradient" | "image"
        "value": "#0d1117",             # hex颜色 / 渐变名称 / 图片URL
        "custom_image_url": ""          # 图片类型时的自定义URL
    },
}

# ─── 共享辅助函数 ───────────────────────────────────────────────────────
# 具有原生思考模式 API 的厂商（无需 system prompt 注入）
_NATIVE_THINK_PROVIDERS = {"deepseek", "openai", "qwen", "gemini"}

# ─── 工具循环消息轮次上限 ─────────────────────────────────────────────────────
# 计数单位：消息来回次数（一轮 = 模型发一条消息，不论该消息包含几个工具调用）
# 有 KB 绑定时（无论是否同时联网）使用 MAX_KB_TOOL_ITERS
# 纯联网无 KB 时使用 MAX_WEB_TOOL_ITERS
MAX_KB_TOOL_ITERS  = 3
MAX_WEB_TOOL_ITERS = 3
MAX_MEMORY_ITERS   = 3

_MEMORY_SEARCH_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "memory_search",
        "description": "检索历史对话记忆中与当前问题语义相关的内容。可多次调用以从不同角度检索记忆片段。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "检索关键词或问题描述"}},
            "required": ["query"],
        },
    },
}

_KB_SEARCH_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "kb_search",
        "description": "检索知识库中与问题相关的文档内容。知识库包含专业资料、文档、笔记等结构化知识。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "检索关键词或问题描述"}},
            "required": ["query"],
        },
    },
}

_WEB_SEARCH_TOOL_DEF: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索互联网获取最新、实时的信息。当用户询问需要最新数据、时事、新闻、天气、价格等实时信息时使用。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        },
    },
}


def _format_memory_results(hits: List[Dict]) -> str:
    """格式化记忆检索结果，供注入 system 上下文。"""
    if not hits:
        return ""
    lines = []
    for i, h in enumerate(hits, 1):
        # v2 格式：每条已包含 "[用户]…\n[AI]…" 标记，topic 作为标签
        topic = (h.get("topic") or "").strip()
        label = f"[{topic}] " if topic and topic != "通用" else ""
        lines.append(f"{i}. {label}{(h.get('text') or '').strip()}")
    return "\n".join(lines)


_TEXT_FILE_EXTS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".xml", ".html", ".css",
    ".sql", ".log", ".sh", ".bat",
}


def _safe_read_text_attachment(path: str, limit: int = 5000) -> str:
    try:
        if not path or not os.path.isfile(path):
            return ""
        if os.path.getsize(path) > 2 * 1024 * 1024:
            return ""
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return (f.read(limit) or "").strip()
    except Exception:
        return ""


def _path_to_data_url(path: str, size_limit: int = 2 * 1024 * 1024) -> str:
    try:
        if not path or not os.path.isfile(path):
            return ""
        if os.path.getsize(path) > size_limit:
            return ""
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return ""


def _prepare_attachments_for_prompt(attachments: Any) -> tuple[list, str, list]:
    """
    返回:
      - persist_attachments: 持久化到会话 JSON 的简化附件信息
      - attachment_text: 注入到用户文本的附件内容/说明
      - image_data_urls: 可传给多模态接口的 data URL 列表
    """
    if not isinstance(attachments, list):
        return [], "", []

    persist_attachments: List[Dict[str, Any]] = []
    text_blocks: List[str] = []
    image_data_urls: List[str] = []

    for item in attachments[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "附件").strip()[:120]
        path = str(item.get("path") or "").strip()
        kind = str(item.get("kind") or "file").strip() or "file"
        mime = str(item.get("mime") or "").strip()
        data_url = str(item.get("data_url") or "").strip()
        size_val = item.get("size")
        try:
            size = int(size_val) if size_val is not None else 0
        except Exception:
            size = 0

        persist_attachments.append({
            "name": name,
            "kind": "image" if kind == "image" else "file",
            "mime": mime,
            "size": size,
            "path": path,
        })

        ext = os.path.splitext(path.lower())[1] if path else ""
        if kind == "image" or (mime.startswith("image/") if mime else False):
            url = data_url if data_url.startswith("data:image/") else _path_to_data_url(path)
            if url:
                image_data_urls.append(url)
                text_blocks.append(f"[图片附件] {name}")
            else:
                text_blocks.append(f"[图片附件] {name}（无法读取图像数据）")
            continue

        if path and ext in _TEXT_FILE_EXTS:
            txt = _safe_read_text_attachment(path)
            if txt:
                text_blocks.append(f"[文件附件: {name}]\n{txt}")
            else:
                text_blocks.append(f"[文件附件] {name}（内容较大或不可读取）")
        else:
            text_blocks.append(f"[文件附件] {name}")

    attachment_text = ""
    if text_blocks:
        attachment_text = "\n\n[用户提供的附件]\n" + "\n\n".join(text_blocks)
    return persist_attachments, attachment_text, image_data_urls


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
        # deepseek-reasoner 对采样参数兼容性较严格，避免携带可能触发 400 的字段
        payload.pop("temperature", None)
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
        """返回当前配置的浅拷贝，供调用方安全读取。"""
        if not isinstance(self._data.get("upstream_models_map"), dict):
            self._data["upstream_models_map"] = {}
        else:
            cleaned_upstream_models_map: Dict[str, List[str]] = {}
            for pid, arr in self._data.get("upstream_models_map", {}).items():
                if not isinstance(arr, list):
                    continue
                cleaned = [str(x).strip() for x in arr if str(x).strip()]
                if cleaned:
                    cleaned_upstream_models_map[str(pid).strip()] = cleaned
            self._data["upstream_models_map"] = cleaned_upstream_models_map
        if not isinstance(self._data.get("kb_embed_api_keys"), dict):
            self._data["kb_embed_api_keys"] = {}
        if not isinstance(self._data.get("kb_embed_models_map"), dict):
            self._data["kb_embed_models_map"] = {}
        else:
            cleaned_models_map: Dict[str, List[str]] = {}
            for pid, arr in self._data.get("kb_embed_models_map", {}).items():
                if not isinstance(arr, list):
                    continue
                cleaned = [str(x).strip() for x in arr if str(x).strip()]
                if cleaned:
                    cleaned_models_map[str(pid).strip()] = cleaned
            self._data["kb_embed_models_map"] = cleaned_models_map
        ep = (self._data.get("kb_embed_provider") or "default").strip()
        old_key = (self._data.get("kb_embed_api_key") or "").strip()
        if old_key and ep and ep != "default" and not self._data["kb_embed_api_keys"].get(ep):
            self._data["kb_embed_api_keys"][ep] = old_key
        return {**self._data}

    def save(self, updates: Dict[str, Any]) -> bool:
        """保存配置更新并持久化到 `config.json`。"""
        # 不允许空 api_key 覆盖已保存的值
        if "api_key" in updates and not str(updates.get("api_key", "")).strip():
            updates = {k: v for k, v in updates.items() if k != "api_key"}

        if "kb_embed_api_keys" in updates and not isinstance(updates.get("kb_embed_api_keys"), dict):
            updates["kb_embed_api_keys"] = {}

        if "kb_embed_models_map" in updates:
            if not isinstance(updates.get("kb_embed_models_map"), dict):
                updates["kb_embed_models_map"] = {}
            else:
                normalized_models_map: Dict[str, List[str]] = {}
                for pid, arr in updates.get("kb_embed_models_map", {}).items():
                    if not isinstance(arr, list):
                        continue
                    cleaned = [str(x).strip() for x in arr if str(x).strip()]
                    if cleaned:
                        normalized_models_map[str(pid).strip()] = cleaned
                updates["kb_embed_models_map"] = normalized_models_map

        if "upstream_models_map" in updates:
            if not isinstance(updates.get("upstream_models_map"), dict):
                updates["upstream_models_map"] = {}
            else:
                normalized_upstream_models_map: Dict[str, List[str]] = {}
                for pid, arr in updates.get("upstream_models_map", {}).items():
                    if not isinstance(arr, list):
                        continue
                    cleaned = [str(x).strip() for x in arr if str(x).strip()]
                    if cleaned:
                        normalized_upstream_models_map[str(pid).strip()] = cleaned
                updates["upstream_models_map"] = normalized_upstream_models_map

        if "kb_embed_api_key" in updates:
            provider = (updates.get("kb_embed_provider") or self._data.get("kb_embed_provider") or "default").strip()
            key_val = str(updates.get("kb_embed_api_key") or "").strip()
            merged = dict(self._data.get("kb_embed_api_keys") or {})
            merged.update(dict(updates.get("kb_embed_api_keys") or {}))
            if provider and provider != "default":
                merged[provider] = key_val
            updates["kb_embed_api_keys"] = merged
        self._data.update(updates)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @property
    def api_key(self) -> str:
        # 首先检查当前提供商的独立密钥
        provider = self._data.get("provider", "deepseek")
        provider_keys = self._data.get("api_keys", {})
        provider_key = provider_keys.get(provider, "").strip()
        
        if provider_key:
            return provider_key
        
        # 降级到全局 api_key 字段
        global_key = self._data.get("api_key", "").strip()
        if global_key:
            return global_key
        
        # 最后检查环境变量
        return (
            os.getenv("DEEPSEEK_API_MOD_KEY", "")
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
        """列出全部会话摘要并按更新时间倒序返回。"""
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
        """按会话 ID 读取完整会话内容，若不存在则返回 `None`。"""
        path = os.path.join(CHATS_DIR, f"{conv_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def create(self) -> Dict:
        """创建一个新的空会话并落盘。"""
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
        if "kb_names" in updates:
            v = updates["kb_names"]
            conv["kb_names"] = list(v) if isinstance(v, list) else []
        if "memory_person" in updates:
            conv["memory_person"] = str(updates["memory_person"]).strip() if updates["memory_person"] else ""
        if "context_window" in updates:
            v = updates["context_window"]
            try:
                conv["context_window"] = max(1, int(v)) if v is not None else 5
            except (TypeError, ValueError):
                conv["context_window"] = 5
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

    def tool_chat(
        self,
        messages: List[Dict],
        tools: List[Dict],
        model: str = "",
        temperature: float = 0.0,
        parallel_tool_calls: bool = False,
        deep_think: bool = False,
        provider: str = "",
    ) -> Dict:
        """
        非流式工具调用。返回三种结果之一：
          {"tool_calls": [...]}  — 模型要调用工具（继续循环）
          {"content": "..."}     — 模型主动停止调用（循环结束信号）
          {"error": "..."}       — 请求失败（4xx / 解析异常 / 网络错误）
        """
        _model = model or self.config.model
        payload: Dict[str, Any] = {
            "model":               _model,
            "messages":            messages,
            "temperature":         temperature,
            "stream":              False,
            "tools":               tools,
            "parallel_tool_calls": parallel_tool_calls,
        }
        if deep_think:
            payload, _model = _apply_deep_think(provider, _model, payload)
            payload["model"] = _model
        try:
            with httpx.Client(timeout=30.0) as c:
                resp = c.post(
                    f"{self.config.base_url}/v1/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
            if resp.status_code >= 400:
                try:
                    msg = resp.json().get("error", {}).get("message", resp.text[:300])
                except Exception:
                    msg = resp.text[:300]
                return {"error": msg}
            data    = resp.json()
            choice  = (data.get("choices") or [{}])[0]
            message = choice.get("message", {})
            tc = message.get("tool_calls")
            if tc:
                return {"tool_calls": tc}
            return {"content": message.get("content") or ""}
        except Exception as e:
            return {"error": str(e)}

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

        def _extract_upstream_error(resp: httpx.Response, raw_text: str) -> str:
            text = (raw_text or "").strip()
            code = resp.status_code
            parsed = ""
            try:
                obj = json.loads(text) if text else {}
            except Exception:
                obj = {}
            if isinstance(obj, dict):
                err = obj.get("error")
                if isinstance(err, dict):
                    parsed = str(err.get("message") or err.get("detail") or "").strip()
                elif isinstance(err, str):
                    parsed = err.strip()
                if not parsed and obj.get("detail"):
                    parsed = str(obj.get("detail")).strip()
                if not parsed and obj.get("message"):
                    parsed = str(obj.get("message")).strip()
            detail = (parsed or text or f"HTTP {code}")[:400]
            return f"HTTP {code}: {detail}" if not detail.startswith("HTTP ") else detail

        def _as_text(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, str):
                return val
            if isinstance(val, list):
                parts: List[str] = []
                for item in val:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        t = item.get("text") or item.get("content") or ""
                        if t:
                            parts.append(str(t))
                    elif item is not None:
                        parts.append(str(item))
                return "".join(parts)
            if isinstance(val, dict):
                t = val.get("text") or val.get("content")
                if t:
                    return str(t)
                try:
                    return json.dumps(val, ensure_ascii=False)
                except Exception:
                    return str(val)
            return str(val)

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
                        msg = _extract_upstream_error(resp, err)
                        yield ("error", msg[:400])
                        return

                    for line in resp.iter_lines():
                        if stop_flag.is_set():
                            return
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            return
                        try:
                            data   = json.loads(data_str)
                            if data.get("usage"):
                                yield ("usage", data["usage"])
                            choice = (data.get("choices") or [{}])[0]
                            delta  = choice.get("delta", {}) if isinstance(choice.get("delta", {}), dict) else {}
                            message_obj = choice.get("message", {}) if isinstance(choice, dict) else {}
                            rc = _strip_dsml(
                                _as_text(
                                    delta.get("reasoning_content")
                                    or delta.get("reasoning")
                                    or choice.get("reasoning_content")
                                    or choice.get("reasoning")
                                    or message_obj.get("reasoning_content")
                                    or message_obj.get("reasoning")
                                )
                            )
                            ct = _strip_dsml(
                                _as_text(
                                    delta.get("content")
                                    or choice.get("content")
                                    or message_obj.get("content")
                                )
                            )
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
        """返回内置模型提供商配置字典。"""
        return PROVIDERS

    def get_config(self) -> Dict:
        """读取当前配置并附加脱敏后的 `api_key_masked` 字段。"""
        cfg = self.config.get()
        key = cfg.get("api_key", "")
        # 返回掩码版本，不暴露完整 Key 到前端
        cfg["api_key_masked"] = (key[:3] + "·" * 6 + key[-3:]) if len(key) > 8 else ("••••••" if key else "")
        return cfg

    def save_config(self, updates: Dict) -> bool:
        """保存配置并重建上游客户端，使新配置立即生效。"""
        result = self.config.save(updates)
        # 配置更新后刷新上游客户端
        self._upstream = UpstreamClient(self.config)
        return result

    def test_connection(self) -> Dict:
        """测试与上游模型服务的连通性并返回结果。"""
        return self._upstream.test_connection()

    def test_web_search(self) -> Dict:
        """按当前搜索引擎配置执行一次最小搜索自检。"""
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
            from app.tools_adapter import invoke_tool
            results = invoke_tool("web_search", {"query": "test", "engine": engine, "api_key": api_key, "max_results": 1})
            if isinstance(results, list) and results:
                return {"ok": True, "message": f"引擎 [{engine}] 可用，已成功获取结果"}
            elif isinstance(results, dict) and results.get("error"):
                return {"ok": False, "message": results["error"]}
            else:
                return {"ok": False, "message": "搜索未返回结果"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def test_embed_connection(self, params: Dict) -> Dict:
        """测试嵌入配置连通性（default/ollama/custom）。"""
        cfg = self.get_config()
        provider = (params.get("provider") or cfg.get("kb_embed_provider") or "default").strip().lower()
        model = (params.get("model") or cfg.get("kb_embed_model") or "").strip()
        base_url = (params.get("base_url") or cfg.get("kb_embed_base_url") or "").strip()
        embed_key_map = cfg.get("kb_embed_api_keys") or {}
        api_key = (params.get("api_key") or embed_key_map.get(provider) or cfg.get("kb_embed_api_key") or "").strip()

        if provider == "default":
            return {"ok": True, "message": "默认本地模式，无需远程连接测试"}

        if provider == "ollama" and not base_url:
            base_url = "http://localhost:11434"
        if not base_url:
            return {"ok": False, "message": "请填写主机 URL"}

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            with httpx.Client(timeout=8.0) as c:
                if provider == "ollama":
                    if not model:
                        ping = c.get(f"{base_url.rstrip('/')}/api/tags", headers=headers)
                        if ping.status_code < 400:
                            return {"ok": True, "message": "Ollama 服务连接成功"}
                        return {"ok": False, "message": f"HTTP {ping.status_code}: {ping.text[:160]}"}
                    resp = c.post(
                        f"{base_url.rstrip('/')}/api/embeddings",
                        headers=headers,
                        json={"model": model, "prompt": "ping"},
                    )
                    if resp.status_code < 400:
                        return {"ok": True, "message": "Ollama 嵌入服务连接成功"}
                    return {"ok": False, "message": f"HTTP {resp.status_code}: {resp.text[:160]}"}

                # custom: 优先 OpenAI-compatible embeddings，失败后尝试 Ollama 风格
                if model:
                    resp = c.post(
                        f"{base_url.rstrip('/')}/v1/embeddings",
                        headers=headers,
                        json={"model": model, "input": "ping"},
                    )
                    if resp.status_code < 400:
                        return {"ok": True, "message": "自定义嵌入服务连接成功"}

                if model:
                    resp2 = c.post(
                        f"{base_url.rstrip('/')}/api/embeddings",
                        headers=headers,
                        json={"model": model, "prompt": "ping"},
                    )
                    if resp2.status_code < 400:
                        return {"ok": True, "message": "自定义嵌入服务连接成功（Ollama风格）"}

                ping = c.get(f"{base_url.rstrip('/')}/v1/models", headers=headers)
                if ping.status_code < 400:
                    return {"ok": True, "message": "自定义服务连接成功"}
                ping2 = c.get(f"{base_url.rstrip('/')}/api/tags", headers=headers)
                if ping2.status_code < 400:
                    return {"ok": True, "message": "自定义服务连接成功（Ollama风格）"}
                return {"ok": False, "message": f"HTTP {ping2.status_code}: {ping2.text[:160]}"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def list_embed_models(self, params: Dict) -> Dict:
        """获取嵌入可选模型列表（default/ollama/custom）。"""
        cfg = self.get_config()
        provider = (params.get("provider") or cfg.get("kb_embed_provider") or "default").strip().lower()
        base_url = (params.get("base_url") or cfg.get("kb_embed_base_url") or "").strip()
        embed_key_map = cfg.get("kb_embed_api_keys") or {}
        api_key = (params.get("api_key") or embed_key_map.get(provider) or cfg.get("kb_embed_api_key") or "").strip()

        if provider == "default":
            return {"ok": True, "models": ["BAAI/bge-small-zh-v1.5"], "message": "默认模式"}

        if provider == "ollama" and not base_url:
            base_url = "http://localhost:11434"
        if not base_url:
            return {"ok": False, "models": [], "message": "请填写主机 URL"}

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        models: List[str] = []
        try:
            with httpx.Client(timeout=8.0) as c:
                if provider in ("ollama", "custom"):
                    resp_tags = c.get(f"{base_url.rstrip('/')}/api/tags", headers=headers)
                    if resp_tags.status_code < 400:
                        data = resp_tags.json() if resp_tags.text else {}
                        tags = data.get("models") if isinstance(data, dict) else []
                        if isinstance(tags, list):
                            for item in tags:
                                if isinstance(item, dict):
                                    name = (item.get("name") or "").strip()
                                    if name:
                                        models.append(name)

                if provider == "custom":
                    resp_models = c.get(f"{base_url.rstrip('/')}/v1/models", headers=headers)
                    if resp_models.status_code < 400:
                        data = resp_models.json() if resp_models.text else {}
                        entries = data.get("data") if isinstance(data, dict) else []
                        if isinstance(entries, list):
                            for item in entries:
                                if isinstance(item, dict):
                                    mid = (item.get("id") or "").strip()
                                    if mid:
                                        models.append(mid)

            deduped = sorted({m for m in models if m})
            if deduped:
                return {"ok": True, "models": deduped, "message": f"已获取 {len(deduped)} 个模型"}
            return {"ok": False, "models": [], "message": "未获取到可用模型"}
        except Exception as e:
            return {"ok": False, "models": [], "message": str(e)}

    def list_upstream_models(self) -> List[Dict]:
        """获取上游可用模型列表。"""
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

    def conv_set_kb_names(self, conv_id: str, kb_names: List[str]) -> bool:
        """设置对话绑定的知识库列表，持久化到对话 JSON 的 kb_names 字段。"""
        return self.store.update(conv_id, {"kb_names": kb_names})

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
        target = next((m for m in conv.get("messages", []) if m.get("id") == msg_id), None)
        if target is None:
            return False

        conv["messages"] = [m for m in conv["messages"] if m.get("id") != msg_id]
        ok = self.store.save(conv)

        # 同步删除记忆库关联向量块（best effort）
        memory_person = (conv.get("memory_person") or "").strip()
        if ok and memory_person:
            try:
                import memory as _mem
                role = (target.get("role") or "").strip()
                if role == "assistant":
                    _mem.delete_round_entries(
                        memory_person,
                        conv_id=conv_id,
                        user_msg_id=str(target.get("for_user_id") or ""),
                        ai_msg_id=str(target.get("id") or ""),
                        ai_text=str(target.get("content") or ""),
                    )
                elif role == "user":
                    uid = str(target.get("id") or "")
                    # 用户消息对应的所有助手版本都视为关联轮次
                    related_as = [
                        m for m in conv.get("messages", [])
                        if (m.get("role") == "assistant" and str(m.get("for_user_id") or "") == uid)
                    ]
                    if related_as:
                        for am in related_as:
                            _mem.delete_round_entries(
                                memory_person,
                                conv_id=conv_id,
                                user_msg_id=uid,
                                ai_msg_id=str(am.get("id") or ""),
                                user_text=str(target.get("content") or ""),
                                ai_text=str(am.get("content") or ""),
                            )
                    else:
                        _mem.delete_round_entries(
                            memory_person,
                            conv_id=conv_id,
                            user_msg_id=uid,
                            user_text=str(target.get("content") or ""),
                        )
            except Exception:
                pass

        return ok

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

    def kb_ingest_file(self, path: str, name: str = "", embed_model: str = "", on_progress=None, source_name: str = None) -> Dict:
        try:
            import kb
            result = kb.ingest_file(path, name=name or None, embed_model=embed_model or None, on_progress=on_progress, source_name=source_name or None)
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

    def kb_peek_chunks(self, collection_name: str, source: str, limit: int = 100) -> List[Dict]:
        try:
            import kb
            return kb.peek_chunks(collection_name, source, limit)
        except Exception:
            return []

    def kb_delete_source(self, collection_name: str, source: str) -> Dict:
        try:
            import kb
            deleted = kb.delete_source(collection_name, source)
            return {"ok": True, "deleted": deleted}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 对话记忆人管理 ────────────────────────────────────────────────────────
    def mem_list_persons(self) -> List[Dict]:
        try:
            import memory as _mem
            return _mem.list_persons()
        except Exception:
            return []

    def mem_create_person(self, name: str) -> bool:
        try:
            import memory as _mem
            return _mem.create_person(name)
        except Exception:
            return False

    def mem_delete_person(self, name: str) -> bool:
        try:
            import memory as _mem
            return _mem.delete_person(name)
        except Exception:
            return False

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
          {type: "start",          message_id, user_message_id, model}
          {type: "tool_start",     tool, query}
          {type: "tool_done",      tool, query, results, elapsed}
          {type: "tool_error",     tool, error}
          {type: "kb_tool_done",   kb_name, query, hit_count, chunks}
          {type: "fallback_notice",message}          — 工具调用失败退化时
          {type: "reasoning_chunk",text}
          {type: "content_chunk",  text}
          {type: "done",           usage, message_id, model}
          {type: "error",          message}
          {type: "title_update",   conv_id, title}
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
        # kb_names 先取 options（前端本次传入），再取对话持久化值
        kb_names: List[str] = options.get("kb_names") or conv.get("kb_names") or []
        memory_person = conv.get("memory_person", "").strip()
        # 重新生成时设为 True，跳过保存用户消息（用户消息已存在于 conv JSON）
        skip_user_save = bool(options.get("skip_user_save"))
        persist_attachments, user_attachment_text, user_image_urls = _prepare_attachments_for_prompt(
            options.get("attachments")
        )
        user_text_for_model = user_text + user_attachment_text

        # ── 生成 ID（提前，确保 start 事件可立即发出）─────────────────────────
        # 重新生成时复用原用户消息 ID，确保前端 start 事件携带正确 user_message_id
        user_msg_id = str(options.get("existing_user_msg_id") or "") or ("msg_" + uuid.uuid4().hex[:10])
        ai_msg_id   = "msg_" + uuid.uuid4().hex[:10]

        # ── 构建消息列表 ──────────────────────────────────────────────────────
        history: List[Dict] = []
        _c_sys = conv.get("system_prompt")
        sys_prompt = (_c_sys if _c_sys is not None else self.config.get().get("system_prompt", "")).strip()
        if sys_prompt:
            history.append({"role": "system", "content": sys_prompt})

        # 按 context_window 轮数截取历史，并正规化为 user->assistant 轮次，避免连续 assistant 触发上游 400。
        context_window = int(conv.get("context_window") or 5)
        raw_msgs = [m for m in conv.get("messages", []) if m.get("role") in ("user", "assistant")]
        user_seq: List[Dict[str, Any]] = []
        assistant_latest_by_user: Dict[str, Dict[str, Any]] = {}
        anon_user_idx = 0
        last_user_id = ""

        for m in raw_msgs:
            role = m.get("role")
            if role == "user":
                uid = str(m.get("id") or "").strip()
                if not uid:
                    anon_user_idx += 1
                    uid = f"__u_{anon_user_idx}"
                user_seq.append({"uid": uid, "msg": m})
                last_user_id = uid
            elif role == "assistant":
                uid = str(m.get("for_user_id") or "").strip() or last_user_id
                if not uid:
                    continue
                assistant_latest_by_user[uid] = m

        turn_msgs: List[Dict[str, str]] = []
        for item in user_seq:
            _u = item["msg"]
            uid = item["uid"]
            turn_msgs.append({"role": "user", "content": _u.get("content") or ""})
            _a = assistant_latest_by_user.get(uid)
            if _a:
                turn_msgs.append({"role": "assistant", "content": _a.get("content") or ""})

        past_msgs = turn_msgs[-(context_window * 2):]
        history.extend(past_msgs)

        # 无论是否重新生成，本轮请求都必须带上当前用户消息，
        # 否则在某些历史形态下会触发上游 "Invalid consecutive assistant message"。
        history.append({"role": "user", "content": user_text_for_model})

        # ── 立即发出 start，让前端建立消息气泡（工具块将插入其中）────────────
        yield {"type": "start", "message_id": ai_msg_id, "user_message_id": user_msg_id, "model": model}

        # ── 前置工具执行 ──────────────────────────────────────────────────────
        import datetime as _dt
        context_parts: List[str] = [
            f"[当前日期] 今天是 {_dt.date.today().strftime('%Y年%m月%d日')}，请以此为准回答所有时间相关问题。"
        ]
        cfg          = self.config.get()
        provider     = cfg.get("provider", "")
        deep_think   = bool(options.get("deep_think")) or _needs_deep_think(user_text)
        web_engine   = cfg.get("tool_web_search_engine", "ddg")
        tavily_key   = cfg.get("tool_tavily_key", "")
        bing_key     = cfg.get("tool_bing_key", "")
        brave_key    = cfg.get("tool_brave_key", "")
        serp_key     = cfg.get("tool_serp_key", "")
        # Route the engine-specific key
        _engine_key_map = {"tavily": tavily_key, "bing": bing_key, "brave": brave_key, "serp": serp_key}
        engine_api_key  = _engine_key_map.get(web_engine, "")

        def _to_tool_keywords(text: str, max_terms: int = 8) -> str:
            """将自然语言句子压缩为工具检索关键词组。"""
            raw = str(text or "").strip()
            if not raw:
                return ""
            normalized = re.sub(r"[\r\n\t]+", " ", raw)
            normalized = re.sub(r"[，。！？；：、,.!?;:()（）\[\]{}\"'“”‘’<>《》【】|/\\]+", " ", normalized)
            parts = re.findall(r"[A-Za-z0-9_\-.]{2,}|[\u4e00-\u9fff]{2,}", normalized)
            stop_words = {
                "请问", "一下", "这个", "那个", "就是", "是否", "什么", "怎么", "为什么", "可以",
                "需要", "帮我", "我们", "你们", "他们", "如果", "然后", "以及", "并且", "一个",
                "一些", "进行", "关于", "相关", "问题", "内容", "信息", "时候", "现在", "还有",
                "依旧", "还是", "工具", "调用", "完整", "一句话",
            }
            terms: List[str] = []
            seen: set = set()
            for p in parts:
                token = p.strip()
                if len(token) < 2:
                    continue
                if token in stop_words:
                    continue
                key = token.lower()
                if key in seen:
                    continue
                seen.add(key)
                terms.append(token)
                if len(terms) >= max_terms:
                    break
            if terms:
                return " ".join(terms)
            fallback = re.sub(r"\s+", " ", raw).strip()
            return fallback[:40]

        _tool_snapshots: list = []  # 收集工具调用结果，随 ai_entry 持久化

        def _tool_chat_cancelable(msgs: List[Dict], tools: List[Dict], **kwargs) -> Dict:
            """可取消的 tool_chat 调用：stop_flag 置位时尽快返回 cancelled。"""
            if stop_flag.is_set():
                return {"cancelled": True}

            # 工具阶段（planner/reflect/query改写等）一律禁用深度思考，
            # 避免与 DS reasoner 混用导致协议/消息序列异常。
            kwargs["deep_think"] = False

            _done = threading.Event()
            _box: Dict[str, Any] = {}

            def _run() -> None:
                try:
                    _box["result"] = self._upstream.tool_chat(msgs, tools, **kwargs)
                except Exception as exc:
                    _box["error"] = str(exc)
                finally:
                    _done.set()

            threading.Thread(target=_run, daemon=True, name="tool-chat-cancelable").start()
            while not _done.wait(0.05):
                if stop_flag.is_set():
                    return {"cancelled": True}

            if "error" in _box:
                return {"error": _box["error"]}
            return _box.get("result") or {"error": "tool_chat empty result"}

        # ── 工具充足性反思：每轮工具结束后判断是否还需要继续检索 ──────────────
        def _reflect_sufficient(context_text: str) -> bool:
            """调用模型评估当前已检索信息是否足以回答用户问题。"""
            if stop_flag.is_set() or not context_text.strip():
                return False
            _r_msgs = [
                {"role": "system", "content": (
                    "你是一个信息充足性评估助手，只输出一个词。"
                    "根据用户问题和已检索到的信息，判断是否已足以给出高质量回答。"
                    "如果足够，输出 SUFFICIENT；如果信息不足或相关性较弱，输出 NEED_MORE。"
                )},
                {"role": "user", "content": (
                    f"用户问题：{user_text}\n\n"
                    f"已检索到的信息：\n{context_text[:2000]}\n\n"
                    "以上信息是否已足够回答用户问题？"
                )},
            ]
            try:
                _r = _tool_chat_cancelable(
                    _r_msgs, [], model=model, temperature=0.0,
                    deep_think=False, provider=provider,
                )
                if _r.get("cancelled"):
                    return False
                _ans = (_r.get("content") or "").upper()
                return bool(re.search(r'\bSUFFICIENT\b', _ans))
            except Exception:
                return False

        def _emit_reflect_for_tool(tool_name: str, context_text: str) -> None:
            """每个工具调用后执行一次反思，结果仅对当前工具块负责。"""
            nonlocal _context_sufficient
            if stop_flag.is_set() or _context_sufficient:
                return
            _label_map = {
                "memory_search": "记忆检索",
                "kb_search": "知识库检索",
                "web_search": "联网搜索",
            }
            _label = _label_map.get(tool_name, tool_name)
            yield_start = {"type": "tool_start", "tool": "reflect", "query": f"评估{_label}信息是否足够"}
            yield_done_tpl = {
                "type": "tool_done",
                "tool": "reflect",
                "query": "",
                "results": [],
            }
            # 通过外层生成器输出
            yield_events.append(yield_start)
            _ok = _reflect_sufficient(context_text[:2000]) if context_text else False
            yield_done = dict(yield_done_tpl)
            yield_done["query"] = f"{_label}反思：{'SUFFICIENT' if _ok else 'NEED_MORE'}"
            yield_events.append(yield_done)
            _tool_snapshots.append({
                "tool": "reflect",
                "query": f"{_label}反思",
                "result": "SUFFICIENT" if _ok else "NEED_MORE",
            })
            if _ok:
                _context_sufficient = True

        # ── 统一 Agentic 工具循环 ─────────────────────────────────────────────
        # 第一轮：强制调用记忆库（如果启用）
        # 后续轮：提供 [memory_search, kb_search, web_search] 让 LLM 自主决策
        
        _tool_msgs: List[Dict] = list(history)  # 工具循环的消息上下文
        _context_sufficient = False  # 反思后认为信息充足时置 True
        _total_iters = 0
        _max_iters = MAX_KB_TOOL_ITERS if kb_names else MAX_WEB_TOOL_ITERS
        
        # 第一轮：强制调用记忆库
        if memory_person and _invoke_tool is not None:
            _total_iters += 1
            _mem_query = _to_tool_keywords(user_text)
            yield {"type": "tool_start", "tool": "memory_search", "query": _mem_query}
            t0 = time.time()
            try:
                # 为 memory_search 提供真实的 LLM 函数（用于 query 改写）
                def _mem_llm(msgs: List[Dict]) -> str:
                    res = self._upstream.tool_chat(msgs, [], model=model, temperature=0.0)
                    return (res.get("content") or "").strip()
                
                # 通过 tools_adapter 调用（需传入额外参数）
                mem_results = _invoke_tool("memory_search", {
                    "query": _mem_query,
                    "person": memory_person,
                    "top_k": 5,
                })
                elapsed = round((time.time() - t0) * 1000)
                
                if isinstance(mem_results, dict) and mem_results.get("error"):
                    yield {"type": "tool_error", "tool": "memory_search", "error": mem_results["error"]}
                elif isinstance(mem_results, list) and mem_results:
                    _mem_text = _format_memory_results(mem_results)
                    context_parts.append(f"[历史对话记忆]\n{_mem_text}")
                    _tool_snapshots.append({
                        "tool": "memory_search",
                        "query": _mem_query,
                        "results": [{"topic": h.get("topic", ""), "text": (h.get("text") or "")[:100]} for h in mem_results[:3]],
                    })
                    yield {"type": "tool_done", "tool": "memory_search", "query": _mem_query, "results": mem_results, "elapsed": elapsed}
                    
                    # 立即反思
                    yield {"type": "tool_start", "tool": "reflect", "query": "评估记忆检索信息是否足够"}
                    _mem_ok = _reflect_sufficient(_mem_text)
                    yield {
                        "type": "tool_done",
                        "tool": "reflect",
                        "query": f"记忆反思：{'SUFFICIENT' if _mem_ok else 'NEED_MORE'}",
                        "results": [],
                    }
                    _tool_snapshots.append({
                        "tool": "reflect",
                        "query": "记忆反思",
                        "result": "SUFFICIENT" if _mem_ok else "NEED_MORE",
                    })
                    if _mem_ok:
                        _context_sufficient = True
                else:
                    yield {"type": "tool_done", "tool": "memory_search", "query": _mem_query, "results": None, "elapsed": elapsed}
            except Exception as exc:
                yield {"type": "tool_error", "tool": "memory_search", "error": str(exc)}
        
        if stop_flag.is_set():
            return
        
        # 后续轮：构建可用工具列表，让 LLM 自主决策
        _available_tools: List[Dict] = []
        
        # memory_search（后续轮仍可调用）
        if memory_person and _invoke_tool is not None:
            _available_tools.append(_MEMORY_SEARCH_TOOL)
        
        # kb_search
        if kb_names and _invoke_tool is not None:
            try:
                import kb as _kb_module
                _available_tools.append(_KB_SEARCH_TOOL)
            except Exception:
                pass
        
        # web_search
        if web_search_mode != "false" and _invoke_tool is not None:
            _available_tools.append(_WEB_SEARCH_TOOL_DEF)
        
        # 工具循环
        yield_events: List[Dict[str, Any]] = []
        while _available_tools and not _context_sufficient and _total_iters < _max_iters and not stop_flag.is_set():
            _total_iters += 1
            
            yield {
                "type": "tool_start",
                "tool": "planner",
                "query": f"第{_total_iters}轮：规划工具调用",
            }
            
            _tres = _tool_chat_cancelable(
                _tool_msgs, _available_tools,
                model=model, temperature=0.0,
            )
            if _tres.get("cancelled") or stop_flag.is_set():
                return
            
            _planned = len(_tres.get("tool_calls") or []) if isinstance(_tres, dict) else 0
            yield {
                "type": "tool_done",
                "tool": "planner",
                "query": f"第{_total_iters}轮：计划调用 {_planned} 个工具",
                "results": [],
            }
            
            # 模型主动停止或出错
            if "error" in _tres or "content" in _tres:
                break
            
            _called_any = False
            for _tc in (_tres.get("tool_calls") or []):
                _fn_name = (_tc.get("function") or {}).get("name", "")
                _fn = (_tc.get("function") or {})
                try:
                    _args = json.loads(_fn.get("arguments") or "{}")
                except Exception:
                    _args = {}
                
                _raw_query = (_args.get("query") or user_text)
                _query = _to_tool_keywords(_raw_query)
                t0 = time.time()
                
                # 执行具体工具
                if _fn_name == "memory_search":
                    yield {"type": "tool_start", "tool": "memory_search", "query": _query}
                    try:
                        def _mem_llm(msgs: List[Dict]) -> str:
                            res = self._upstream.tool_chat(msgs, [], model=model, temperature=0.0)
                            return (res.get("content") or "").strip()
                        
                        mem_results = _invoke_tool("memory_search", {
                            "query": _query,
                            "person": memory_person,
                            "top_k": 5,
                        })
                        elapsed = round((time.time() - t0) * 1000)
                        
                        if isinstance(mem_results, dict) and mem_results.get("error"):
                            result_text = f"错误：{mem_results['error']}"
                        elif isinstance(mem_results, list) and mem_results:
                            result_text = _format_memory_results(mem_results)
                            context_parts.append(f"[记忆检索 - {_query}]\n{result_text}")
                            _tool_snapshots.append({
                                "tool": "memory_search",
                                "query": _query,
                                "results": [{"topic": h.get("topic", ""), "text": (h.get("text") or "")[:100]} for h in mem_results[:3]],
                            })
                            yield {"type": "tool_done", "tool": "memory_search", "query": _query, "results": mem_results, "elapsed": elapsed}
                        else:
                            result_text = "未找到相关记忆"
                            yield {"type": "tool_done", "tool": "memory_search", "query": _query, "results": None, "elapsed": elapsed}
                        
                        _tool_msgs.append({"role": "assistant", "tool_calls": [_tc]})
                        _tool_msgs.append({
                            "role": "tool",
                            "tool_call_id": _tc.get("id", ""),
                            "content": result_text,
                        })
                        _emit_reflect_for_tool("memory_search", result_text)
                        for _ev in yield_events:
                            yield _ev
                        yield_events.clear()
                        _called_any = True
                    except Exception as exc:
                        yield {"type": "tool_error", "tool": "memory_search", "error": str(exc)}
                
                elif _fn_name == "kb_search":
                    yield {"type": "tool_start", "tool": "kb_search", "query": _query}
                    try:
                        import kb as _kb_module
                        
                        _kb_errors: List[str] = []
                        _ev_q: "queue.SimpleQueue[Optional[Dict]]" = queue.SimpleQueue()
                        _result: Dict[str, Any] = {}
                        
                        class _KbCancelled(Exception):
                            pass
                        
                        def _tool_chat_fn(msgs: List[Dict], tools: List[Dict]) -> Dict:
                            if stop_flag.is_set():
                                raise _KbCancelled()
                            res = _tool_chat_cancelable(msgs, tools, model=model, temperature=0.0)
                            if res.get("cancelled") or stop_flag.is_set():
                                raise _KbCancelled()
                            if "error" in res:
                                _kb_errors.append(res["error"])
                            return res
                        
                        def _kb_worker() -> None:
                            try:
                                hits, _ = _kb_module.level3_search(
                                    _query,
                                    _tool_chat_fn,
                                    kb_names=kb_names,
                                    top_k=3,
                                    max_iters=2,  # 子循环限制
                                    char_budget=16000,
                                    on_round=lambda ev: _ev_q.put(ev),
                                )
                                _result["hits"] = hits
                            except _KbCancelled:
                                _result["cancelled"] = True
                            except Exception as exc:
                                _result["error"] = str(exc)
                            finally:
                                _ev_q.put(None)
                        
                        _kb_t0 = time.time()
                        _worker = threading.Thread(target=_kb_worker, daemon=True, name="kb-search")
                        _worker.start()
                        
                        _kb_round_events: list = []
                        while True:
                            if stop_flag.is_set():
                                _result["cancelled"] = True
                                break
                            try:
                                _ev = _ev_q.get(timeout=0.1)
                            except queue.Empty:
                                if not _worker.is_alive():
                                    break
                                continue
                            if _ev is None:
                                break
                            _kb_round_events.append(_ev)
                            yield {"type": "kb_tool_done", **_ev}
                        
                        _worker.join(timeout=0.1)
                        elapsed = round((time.time() - _kb_t0) * 1000)
                        
                        if _result.get("cancelled") or stop_flag.is_set():
                            return
                        
                        if "error" in _result:
                            result_text = f"错误：{_result['error']}"
                            yield {"type": "tool_error", "tool": "kb_search", "error": _result["error"]}
                        else:
                            hits = _result.get("hits") or []
                            if hits:
                                kb_context = _kb_module.format_kb_hits_for_context(hits)
                                result_text = kb_context
                                context_parts.append(f"[知识库检索 - {_query}]\n{kb_context}")
                                _tool_snapshots.append({
                                    "tool": "kb_search",
                                    "query": _query,
                                    "rounds": [
                                        {"kb_name": rd.get("kb_name", ""), "query": rd.get("query", ""), "hit_count": rd.get("hit_count", 0)}
                                        for rd in _kb_round_events
                                    ],
                                })
                                yield {"type": "tool_done", "tool": "kb_search", "query": _query, "results": hits, "elapsed": elapsed}
                            else:
                                result_text = "未找到相关知识"
                                yield {"type": "tool_done", "tool": "kb_search", "query": _query, "results": None, "elapsed": elapsed}
                        
                        _tool_msgs.append({"role": "assistant", "tool_calls": [_tc]})
                        _tool_msgs.append({
                            "role": "tool",
                            "tool_call_id": _tc.get("id", ""),
                            "content": result_text,
                        })
                        _emit_reflect_for_tool("kb_search", result_text)
                        for _ev in yield_events:
                            yield _ev
                        yield_events.clear()
                        _called_any = True
                    except Exception as exc:
                        yield {"type": "tool_error", "tool": "kb_search", "error": str(exc)}
                
                elif _fn_name == "web_search":
                    yield {"type": "tool_start", "tool": "web_search", "query": _query}
                    try:
                        raw = _invoke_tool("web_search", {
                            "query": _query,
                            "max_results": 5,
                            "engine": web_engine,
                            "api_key": engine_api_key,
                        })
                        elapsed = round((time.time() - t0) * 1000)
                        
                        if isinstance(raw, dict) and raw.get("error"):
                            result_text = f"错误：{raw['error']}"
                            yield {"type": "tool_error", "tool": "web_search", "error": raw["error"]}
                        elif isinstance(raw, list) and raw:
                            result_text = _format_search_results(raw)
                            context_parts.append(f"[联网搜索 - {_query}]\n{result_text}")
                            _tool_snapshots.append({
                                "tool": "web_search",
                                "query": _query,
                                "results": [{"title": r.get("title", ""), "url": r.get("url", "")} for r in raw[:8]],
                            })
                            yield {"type": "tool_done", "tool": "web_search", "query": _query, "results": raw, "elapsed": elapsed}
                        else:
                            result_text = "未找到搜索结果"
                            yield {"type": "tool_done", "tool": "web_search", "query": _query, "results": None, "elapsed": elapsed}
                        
                        _tool_msgs.append({"role": "assistant", "tool_calls": [_tc]})
                        _tool_msgs.append({
                            "role": "tool",
                            "tool_call_id": _tc.get("id", ""),
                            "content": result_text,
                        })
                        _emit_reflect_for_tool("web_search", result_text)
                        for _ev in yield_events:
                            yield _ev
                        yield_events.clear()
                        _called_any = True
                    except Exception as exc:
                        yield {"type": "tool_error", "tool": "web_search", "error": str(exc)}
            
            # 本轮未调用任何工具，跳出循环
            if not _called_any:
                break
        
        # 工具循环结束后，收集所有上下文并反思
        if context_parts and not _context_sufficient and not stop_flag.is_set():
            _all_ctx = "\n\n".join(p for p in context_parts if not p.startswith("[当前日期]"))
            if _all_ctx:
                yield {"type": "tool_start", "tool": "reflect", "query": "最终信息充足性评估"}
                _final_ok = _reflect_sufficient(_all_ctx[-2000:])
                yield {
                    "type": "tool_done",
                    "tool": "reflect",
                    "query": f"最终反思：{'SUFFICIENT' if _final_ok else 'NEED_MORE'}",
                    "results": [],
                }
                _tool_snapshots.append({
                    "tool": "reflect",
                    "query": "最终反思",
                    "result": "SUFFICIENT" if _final_ok else "NEED_MORE",
                })
        
        if stop_flag.is_set():
            return
        
        # 注入所有收集的上下文
        if context_parts:
            history = _inject_context_to_system(history, "\n\n".join(context_parts))

        # ── 深度思考 ─────────────────────────────────────────────────────────
        # 无原生思考 API 的厂商：降级到 system prompt 注入
        if deep_think and provider not in _NATIVE_THINK_PROVIDERS:
            history = _inject_context_to_system(
                history,
                "用户希望你启动深度思考模式，请尽可能进行详细的内心推理和分析再回答。"
            )

        # ── 保存用户消息（重新生成时跳过，用户消息已在 conv JSON 中）────────
        if not skip_user_save:
            now = int(time.time())
            user_entry: Dict[str, Any] = {
                "id":         user_msg_id,
                "role":       "user",
                "content":    user_text,
                "created_at": now,
            }
            if persist_attachments:
                user_entry["attachments"] = persist_attachments
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

        history_for_model = list(history)
        if user_image_urls:
            for i in range(len(history_for_model) - 1, -1, -1):
                if history_for_model[i].get("role") != "user":
                    continue
                if history_for_model[i].get("content") != user_text_for_model:
                    continue
                _parts = [{"type": "text", "text": user_text_for_model}]
                for _u in user_image_urls[:4]:
                    _parts.append({"type": "image_url", "image_url": {"url": _u}})
                history_for_model[i] = {**history_for_model[i], "content": _parts}
                break

        had_stream_error = False
        stream_error_message = ""

        for ev_type, ev_data in self._upstream.stream_chat(
            history_for_model, model, temperature, stop_flag,
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
                had_stream_error = True
                stream_error_message = str(ev_data or "").strip()
                yield {"type": "error", "message": stream_error_message, "message_id": ai_msg_id}
                break

        if had_stream_error and stream_error_message:
            _err_line = f"⚠ {stream_error_message}"
            if full_content:
                if _err_line not in full_content:
                    full_content = full_content.rstrip() + "\n\n" + _err_line
            else:
                full_content = _err_line

        # ── 保存 AI 消息 ──────────────────────────────────────────────────────
        ai_entry: Dict[str, Any] = {
            "id":                ai_msg_id,
            "role":              "assistant",
            "for_user_id":       user_msg_id,
            "content":           full_content,
            "reasoning_content": full_reasoning,
            "model":             model,
            "usage":             usage,
            "created_at":        int(time.time()),
            "tool_results":      _tool_snapshots,
        }
        conv["messages"].append(ai_entry)
        self.store.save(conv)

        # ── 自动将本轮对话存入记忆库（后台话题提取与块合并）─────────────────
        def _should_skip_memory_write(text: str) -> bool:
            t = (text or "").strip()
            if not t:
                return True
            # 明显的上游/协议错误回复不写入记忆，避免污染后续检索上下文
            _err_markers = (
                "⚠ HTTP ",
                "Invalid consecutive assistant message",
                "连接超时，请检查网络或 API 地址",
                "tool_chat empty result",
            )
            return any(m in t for m in _err_markers)

        if memory_person and full_content and not stop_flag.is_set() and not _should_skip_memory_write(full_content):
            try:
                import memory as _mem_module

                def _save_llm(msgs: List[Dict]) -> str:
                    res = self._upstream.tool_chat(msgs, [], model=model, temperature=0.0)
                    return (res.get("content") or "").strip()

                _mem_module.add_round(
                    memory_person,
                    user_text,
                    full_content,
                    conv_id=conv_id,
                    round_id=user_msg_id,
                    user_msg_id=user_msg_id,
                    ai_msg_id=ai_msg_id,
                    llm_call=_save_llm,
                )
            except Exception:
                pass

        yield {"type": "done", "message_id": ai_msg_id, "usage": usage, "model": model}
