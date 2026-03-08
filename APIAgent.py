"""
APIAgent（最小网关）：
- 职责：对外暴露 OpenAI 兼容的 /v1/chat/completions，并把请求转发到上游模型（如 DeepSeek）。
- 预留调用点，但不在本文件实现：
	1) 外部工具接口（如联网搜索、计算器等）
	2) RAG 检索接口（向量库召回）
本地运行：
	uvicorn APIAgent:app --reload --port 8000
"""

from typing import Any, Dict, List, Optional

import json
import logging
import time
import asyncio
import re
import secrets
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
try:
	from dotenv import load_dotenv  # 可选：读取 .env
	load_dotenv()
except Exception:
	pass

# ===== 可选适配器：若不存在则忽略 =====
try:
	from tools_adapter import invoke_tool as external_invoke_tool  # type: ignore
except Exception:
	external_invoke_tool = None

try:
	from rag_adapter import retrieve_context as external_retrieve_context  # type: ignore
except Exception:
	external_retrieve_context = None

# ===== 从 client_core 导入共享工具函数（避免重复维护）=====
from client_core import (
	_SEARCH_TRIGGERS,
	_DEEP_THINK_TRIGGERS,
	_needs_search,
	_needs_deep_think,
	_strip_dsml,
	_format_search_results,
	_inject_context_to_system,
)

# TODO: 替换为你的上游模型服务（如 DeepSeek 的 OpenAI 兼容地址）
UPSTREAM_BASE_URL = "https://api.deepseek.com"  # 占位
UPSTREAM_API_KEY = ""  # 占位
PUBLIC_BASE_URL = "http://127.0.0.1:8000"

# 对外（下游客户端）暴露的统一虚拟模型名，可由 GATEWAY_MODEL_NAME 覆盖。
# 下游只看到这一个模型；实际调用哪个上游模型由 GATEWAY_DEFAULT_MODEL 决定。
_GATEWAY_MODEL_NAME_DEFAULT = "ai-memory"


app = FastAPI(title="AcompaLLM Gateway", version="0.2.0")

app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_methods=["*"],
	allow_headers=["*"],
)

# 挂载前端静态文件（访问 http://localhost:8000/ 即可打开 UI）
app.mount("/ui", StaticFiles(directory="static", html=True), name="static")


def _ensure_gateway_key() -> str:
	"""读取或生成本网关的访问 Key，并持久化到 .env。"""
	key = os.getenv("GATEWAY_API_KEY", "").strip()
	if key:
		return key
	# 首次启动：生成一个 sk- 前缀的随机 Key
	key = "sk-gw-" + secrets.token_hex(24)
	env_path = os.path.join(os.path.dirname(__file__), ".env")
	try:
		lines: list = []
		if os.path.exists(env_path):
			with open(env_path, "r", encoding="utf-8") as f:
				lines = f.readlines()
		lines.append(f"\nGATEWAY_API_KEY={key}\n")
		with open(env_path, "w", encoding="utf-8") as f:
			f.writelines(lines)
	except Exception:
		pass
	os.environ["GATEWAY_API_KEY"] = key
	return key


@app.on_event("startup")
async def _startup() -> None:
	# 确保网关 Key 已生成
	_ensure_gateway_key()
	# 创建共享 HTTP 客户端，用于上游调用
	app.state.http_client = httpx.AsyncClient(timeout=30.0)
	# 请求计数器（进程内累计）
	app.state.request_count = 0


@app.on_event("shutdown")
async def _shutdown() -> None:
	client: httpx.AsyncClient = app.state.http_client
	await client.aclose()


@app.get("/health")
async def health() -> Dict[str, str]:
	"""存活探针。"""
	return {"status": "ok"}


@app.get("/")
async def root_redirect() -> RedirectResponse:
	"""根路径默认跳转到可视化页面。"""
	return RedirectResponse(url="/ui/")


@app.get("/v1/public-info")
async def public_info() -> JSONResponse:
	"""返回给前端/客户端展示的固定主机、API路径和网关 Key。"""
	base_url = os.getenv("PUBLIC_BASE_URL", PUBLIC_BASE_URL).rstrip("/")
	gw_key = os.getenv("GATEWAY_API_KEY", _ensure_gateway_key())
	return JSONResponse(content={
		"base_url": base_url,
		"gateway_api_key": gw_key,
		"chat_path": "/v1/chat/completions",
		"stream_path": "/v1/chat/stream",
		"models_path": "/v1/models",
		"chat_url": f"{base_url}/v1/chat/completions",
		"stream_url": f"{base_url}/v1/chat/stream",
		"models_url": f"{base_url}/v1/models",
	})


@app.get("/v1/models")
async def list_models() -> JSONResponse:
	"""对下游只暴露一个统一虚拟模型名，屏蔽上游模型细节。

	下游（Chatbox 等任意客户端）只需选择这一个模型即可；
	实际调用哪个上游模型由服务端 GATEWAY_DEFAULT_MODEL 决定。
	"""
	model_name = os.getenv("GATEWAY_MODEL_NAME", _GATEWAY_MODEL_NAME_DEFAULT)
	return JSONResponse(content={
		"object": "list",
		"data": [{"id": model_name, "object": "model", "owned_by": "gateway"}],
	})


@app.get("/v1/upstream-models")
async def list_upstream_models() -> JSONResponse:
	"""代理查询上游真实的模型列表，仅供管理界面使用。

	会自动过滤 _INTERNAL_ONLY_MODELS 中的条目（这些模型由网关内部工具调用，
	不应作为独立的上游模型选项暴露给用户）。
	"""
	base_url = os.getenv("UPSTREAM_BASE_URL", UPSTREAM_BASE_URL).rstrip("/")
	api_key  = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or UPSTREAM_API_KEY
	fallback = [
		{"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
	]

	def _filter(data: list) -> list:
		return [m for m in data if m.get("id") not in _INTERNAL_ONLY_MODELS]

	if not api_key:
		return JSONResponse(content={"object": "list", "data": fallback})
	try:
		client: httpx.AsyncClient = app.state.http_client
		resp = await client.get(
			f"{base_url}/v1/models",
			headers={"Authorization": f"Bearer {api_key}"},
			timeout=8.0,
		)
		if resp.status_code == 200:
			body = resp.json()
			body["data"] = _filter(body.get("data", []))
			return JSONResponse(content=body)
	except Exception:
		pass
	return JSONResponse(content={"object": "list", "data": fallback})


def maybe_call_tool(tool_name: Optional[str], args: Optional[Dict[str, Any]]) -> Optional[Any]:
	"""工具调用占位：若外部实现存在则调用，否则返回 None。"""
	if not tool_name or external_invoke_tool is None:
		return None
	try:
		return external_invoke_tool(tool_name, args or {})
	except Exception as exc:
		return {"tool_error": str(exc)}


def maybe_call_rag(query: Optional[str], top_k: int = 3) -> List[Dict[str, Any]]:
	"""RAG 检索占位：若外部实现存在则返回片段列表，否则空列表。"""
	if not query or external_retrieve_context is None:
		return []
	try:
		return list(external_retrieve_context(query, top_k=top_k))
	except Exception as exc:
		return [{"rag_error": str(exc)}]


# ── 工具前置执行引擎 ────────────────────────────────────────────────────────────
# 设计原则：工具在进入模型推理前全部执行完毕，结果以 system 消息注入。
# 模型只看到"已附带上下文的 prompt"，无需支持 function calling，任何模型通用。

def _estimate_tokens(messages: List[Dict]) -> int:
	"""
	对消息列表做粗略 token 估算：
	中文字符约 1.5 token/字，英文/数字约 0.3 token/char。
	"""
	total = 0.0
	for m in messages:
		content = m.get("content") or ""
		if not isinstance(content, str):
			continue
		for ch in content:
			if '\u4e00' <= ch <= '\u9fff':
				total += 1.5
			else:
				total += 0.3
	return int(total)


def _client_already_searched(messages: List[Dict]) -> bool:
	"""检测客户端（如 Chatbox）是否已经对本轮用户问题执行过搜索。

	两种检测信号：
	1. 历史中有 role='tool' 消息 → 客户端用了标准 tool 协议
	2. 最后一条 user 消息的 content 被客户端追加了搜索结果
	   （典型形式：用户原始问题 + \n\n + {'...'} 或 JSON 列表）
	"""
	for m in messages:
		if m.get("role") == "tool":
			return True
	# 检查最后一条 user 消息是否被客户端追加了搜索结果
	for m in reversed(messages):
		if m.get("role") == "user":
			content = m.get("content", "")
			if isinstance(content, str) and "\n\n" in content:
				after = content[content.index("\n\n") + 2:].strip()
				# Chatbox 追加的内容往往是 dict/list 字符串或搜索结果块
				if after.startswith(("{", "[", "title:", "- ", "http")):
					return True
			break
	return False


def _sanitize_messages(messages: List[Dict]) -> List[Dict]:
	"""
	发送到任何上游 API 之前调用。
	根据 DeepSeek 官方文档：多轮对话中 assistant 消息不能含 reasoning_content，
	否则 API 返回 400。此函数剥除该字段及其他非标准内部字段。
	"""
	clean = []
	for m in messages:
		if not isinstance(m, dict):
			clean.append(m)
			continue
		m2 = {k: v for k, v in m.items() if k != "reasoning_content"}
		clean.append(m2)
	return clean


def _strip_fc_exchange(messages: List[Dict]) -> tuple:
	"""剥离 FC 交换轮次，提取 KB/工具内容。

	FC Round 1 模型回复可能含有：
	  1. 标准 OpenAI tool_calls 字段（会被正确过滤）
	  2. DeepSeek Prompt Engineering XML 格式（<function_calls>...）
	     ——此种情况下需过滤含 XML 的 assistant 消息，否则 Step4 模型会原样复制输出。

	返回：(clean_messages, kb_text)
	  clean_messages: 去除了 role=tool 消息和含 tool_calls/XML 的 assistant 消息
	  kb_text:        所有 role=tool 内容合并（供注入 system 提示）
	"""
	tool_texts: List[str] = []
	clean: List[Dict] = []
	for m in messages:
		role = m.get("role", "")
		content = m.get("content") or ""
		if isinstance(content, list):
			content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
		# role=tool：提取内容，剥除消息本身
		if role == "tool":
			tool_texts.append(content)
			continue
		# 含标准 tool_calls 的 assistant（FC Round1 模型回调）
		if role == "assistant" and m.get("tool_calls"):
			continue
		# 含 XML PE 格式 <function_calls> 的 assistant（DeepSeek PE 风格）
		if role == "assistant" and "<function_calls>" in content:
			continue
		clean.append(m)
	return clean, "\n\n".join(tool_texts)


def pre_execute_tools(payload: Dict[str, Any], last_user_msg: str) -> List[Dict[str, Any]]:
	"""
	前置工具执行器：在模型推理前执行所有工具，
	返回 [(tool_name, call_id, result_str), ...] 列表。
	调用方根据模型能力决定如何注入（原生 tool 消息 or 追加文本）。

	判断优先级：
	0. 客户端已执行搜索（tool 消息或已追加结果）→ 跳过搜索
	1. 客户端明确指定 use_search=true  → 执行搜索
	2. 客户端明确指定 use_search=false → 跳过搜索
	3. 客户端未指定（auto）            → 启发式检测
	"""
	import uuid
	results: List[Dict[str, Any]] = []  # [{name, call_id, result}]

	# —— 联网搜索 ——
	use_search = payload.get("use_search")  # 请求体显式指定（True/False/None）
	search_query = payload.get("search_query") or last_user_msg
	messages = payload.get("messages", [])

	# 若请求体未指定，读取服务端配置作为默认值
	# GATEWAY_TOOL_WEB_SEARCH: "auto"（默认）| "true"（总是）| "false"（总是禁用）
	if use_search is None:
		_env_ws = os.getenv("GATEWAY_TOOL_WEB_SEARCH", "auto").strip().lower()
		if _env_ws == "true":
			use_search = True
		elif _env_ws == "false":
			use_search = False
		# else: 保持 None → 启发式判断

	# 服务端未配置 RAG 时，也读取 GATEWAY_TOOL_RAG 作为默认开关
	if not payload.get("use_rag") and os.getenv("GATEWAY_TOOL_RAG", "false").strip().lower() == "true":
		payload = {**payload, "use_rag": True}

	# 优先级 0：客户端已搜索，网关不重复执行
	client_searched = use_search is not True and _client_already_searched(messages)

	should_search = (
		not client_searched
		and (
			use_search is True
			or (use_search is None and _needs_search(last_user_msg))
		)
	)
	if should_search and external_invoke_tool is not None:
		try:
			raw = external_invoke_tool("web_search", {"query": search_query, "max_results": 5})
			content = _format_search_results(raw) if isinstance(raw, list) else str(raw)
		except Exception as exc:
			content = f"[搜索失败: {exc}，请基于已有知识回答]"
		results.append({"name": "web_search", "call_id": "call_" + uuid.uuid4().hex[:8], "result": content})

	# —— 显式工具调用（如计算器） ——
	tool_name: Optional[str] = payload.get("tool_name")
	tool_args: Optional[Dict[str, Any]] = payload.get("tool_args")
	if tool_name:
		raw = maybe_call_tool(tool_name, tool_args)
		if raw is not None:
			results.append({"name": tool_name, "call_id": "call_" + uuid.uuid4().hex[:8], "result": str(raw)})

	# —— RAG 检索 ——
	if bool(payload.get("use_rag")):
		rag_query = payload.get("rag_query") or last_user_msg
		rag_hits = maybe_call_rag(rag_query, top_k=int(payload.get("rag_top_k", 3)))
		if rag_hits:
			ctx_lines = [
				f"- {h.get('title') or h.get('source') or '片段'}: {h.get('body') or h.get('text') or str(h)}"
				for h in rag_hits
			]
			# result 不含前置标签，_inject_context_to_system 会通过 _TOOL_INJECT_LABELS 自动添加
			results.append({"name": "memory_search", "call_id": "call_" + uuid.uuid4().hex[:8], "result": "\n".join(ctx_lines)})

	return results


# 统一走 pre_execute 路径的模型：不尝试注入 tools 定义，协议一致
# • deepseek-chat / deepseek-reasoner 都在此列表 —— 参考 SiliconFlow 统一模型方案
#   两者内部客户端（Chatbox）system prompt 都含有 web_search 描述，
#   若再向 payload 注入 GATEWAY_TOOLS，模型会在文字里“演”搜索而非真正调用。
# 正确方式：网关自己运行搜索，结果注入 system，不传 tools/tool_choice。
_NO_TOOL_CALL_MODELS = ("deepseek",)

# 仅在网关内部使用的模型 ID（如作为 deep_think 工具的后端），不在管理界面作为可选上游模型展示
_INTERNAL_ONLY_MODELS: frozenset[str] = frozenset({
	"deepseek-reasoner",
})

# ===== 网关对外暴露的工具定义（注入到支持 function calling 的模型请求中） =====
GATEWAY_TOOLS = [
	{
		"type": "function",
		"function": {
			"name": "web_search",
			"description": "搜索互联网获取实时/最新信息，如新闻、价格、天气、最新事件等。当用户询问当前时事或需要最新数据时调用。",
			"parameters": {
				"type": "object",
				"properties": {"query": {"type": "string", "description": "搜索关键词"}},
				"required": ["query"],
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "deep_think",
			"description": "调用深度推理模型（deepseek-reasoner）对复杂数学、逻辑推导、多步骤分析等问题进行深度思考。当问题需要严密推理或当前回答不够确定时调用。",
			"parameters": {
				"type": "object",
				"properties": {"question": {"type": "string", "description": "需要深度推理的问题或任务"}},
				"required": ["question"],
			},
		},
	},
]


async def _execute_gateway_tool(name: str, args: Dict[str, Any]) -> str:
	"""执行网关内置工具，返回字符串结果。"""
	if name == "web_search":
		query = args.get("query", "")
		if external_invoke_tool is not None:
			try:
				raw = external_invoke_tool("web_search", {"query": query, "max_results": 5})
				return _format_search_results(raw) if isinstance(raw, list) else str(raw)
			except Exception as exc:
				return f"[搜索失败: {exc}]"
		return "[搜索工具不可用]"

	if name == "deep_think":
		question = args.get("question") or args.get("query", "")
		base_url = os.getenv("UPSTREAM_BASE_URL", UPSTREAM_BASE_URL).rstrip("/")
		api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or UPSTREAM_API_KEY
		req_body = {
			"model": "deepseek-reasoner",
			"stream": False,
			"messages": [{"role": "user", "content": question}],
		}
		try:
			client: httpx.AsyncClient = app.state.http_client
			resp = await client.post(
				f"{base_url}/v1/chat/completions",
				headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
				json=req_body,
				timeout=180.0,
			)
			resp.raise_for_status()
			msg = resp.json()["choices"][0]["message"]
			reasoning = _strip_dsml(msg.get("reasoning_content") or "")
			content = _strip_dsml(msg.get("content") or "")
			res = ""
			if reasoning:
				res += f"[深度推理过程]\n{reasoning}\n\n"
			res += f"[推理结论]\n{content}"
			return res
		except Exception as exc:
			return f"[深度推理失败: {exc}]"

	return f"[未知工具: {name}]"


async def _stream_deep_think(question: str):
	"""流式调用 deepseek-reasoner，实时 yield (type, text)。
	type: "reasoning" | "content" | "error"
	使用独立 client（不复用共享 client），避免 30s 全局超时中断长推理。
	"""
	base_url = os.getenv("UPSTREAM_BASE_URL", UPSTREAM_BASE_URL).rstrip("/")
	api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or UPSTREAM_API_KEY
	req_body = {
		"model": "deepseek-reasoner",
		"stream": True,
		"messages": [{"role": "user", "content": question}],
	}
	try:
		async with httpx.AsyncClient(
			timeout=httpx.Timeout(connect=15.0, read=180.0, write=15.0, pool=15.0)
		) as _dt_client:
			async with _dt_client.stream(
				"POST",
				f"{base_url}/v1/chat/completions",
				headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
				json=req_body,
			) as resp:
				resp.raise_for_status()
				async for line in resp.aiter_lines():
					if not line.startswith("data: "):
						continue
					data = line[6:].strip()
					if data == "[DONE]":
						break
					try:
						obj = json.loads(data)
						delta = obj["choices"][0]["delta"]
						rc = delta.get("reasoning_content") or ""
						ct = delta.get("content") or ""
						if rc:
							yield ("reasoning", rc)
						if ct:
							yield ("content", ct)
					except Exception:
						pass
	except Exception as exc:
		yield ("error", f"[深度推理失败: {exc}]")


_CHATBOX_TOOL_DESC_RE = re.compile(
	r"\bUse these tools[^\n]*\n+"           # "Use these tools to search..."
	r"(?:##\s*\w+\s*\n+[^\n#]*\n*)+",       # ## web_search / ## parse_link 段落
	re.IGNORECASE,
)

def _strip_client_tool_descriptions(content: str) -> str:
	"""去除 Chatbox 等客户端注入到 system prompt 里的工具描述段落。

	当网关已自行执行搜索并注入结果时，需清除这些描述，
	防止模型仍然尝试输出工具调用占位文本。
	"""
	return _CHATBOX_TOOL_DESC_RE.sub("", content).strip()


def _inject_no_tool_hint(messages: List[Dict]) -> List[Dict]:
	"""当本轮未运行任何网关工具时，向 system 消息追加一条指令。

	目的：阻止模型看到 system prompt 里的工具描述后，自行输出
	"正在搜索…""搜索已完成"等虚假过渡文字。
	模型应直接基于自身知识回答，不要模拟工具调用过程。
	"""
	hint = (
		"\n\n[网关提示] 本次请求网关未执行联网搜索或其他外部工具。"
		"请直接基于你的已有知识回答，不要输出任何搜索过渡说明文字。"
	)
	messages = list(messages)
	for i, m in enumerate(messages):
		if m.get("role") == "system":
			existing = m.get("content", "")
			if isinstance(existing, str):
				messages[i] = {**m, "content": existing + hint}
				return messages
	messages.insert(0, {"role": "system", "content": hint.lstrip()})
	return messages


def prepare_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
	"""统一处理工具前置执行 + 消息注入，严格遵循各模型商的原生对话结构。"""
	messages = payload.get("messages", [])
	if not isinstance(messages, list):
		raise HTTPException(status_code=400, detail="messages must be a list")

	# 取出最后一条用户消息，用于工具意图检测（兼容数组格式 content）
	last_user_msg = ""
	for m in reversed(messages):
		if m.get("role") == "user":
			c = m.get("content")
			if isinstance(c, str):
				last_user_msg = c
			elif isinstance(c, list):
				last_user_msg = " ".join(
					p.get("text", "") for p in c
					if isinstance(p, dict) and p.get("type") == "text"
				)
			break

	# 始终使用服务端配置的上游模型，忽略客户端传来的 model 字段。
	# 对下游只暴露一个虚拟模型名（GATEWAY_MODEL_NAME），实际路由到 GATEWAY_DEFAULT_MODEL。
	upstream_model = os.getenv("GATEWAY_DEFAULT_MODEL", "deepseek-chat")
	model_name = upstream_model.lower()
	payload = {**payload, "model": upstream_model}
	supports_tool_call = not any(k in model_name for k in _NO_TOOL_CALL_MODELS)

	if supports_tool_call:
		# ── 支持 function calling 的模型：下发工具定义，让模型自己决定是否调用 ──
		# 不预执行搜索（避免重复搜索），模型将通过 tool_calls 按需触发
		if "tools" not in payload:
			available_tools = [t for t in GATEWAY_TOOLS if not (
				t["function"]["name"] == "web_search" and external_invoke_tool is None
			)]
			if available_tools:
				# 判断是否需要强制触发 deep_think
				force_deep_think = (
					_needs_deep_think(last_user_msg)                # 用户明确要求
					or _estimate_tokens(messages) >= 5000           # 上下文 token 超限
				)
				has_deep_think = any(t["function"]["name"] == "deep_think" for t in available_tools)
				if force_deep_think and has_deep_think:
					tool_choice: Any = {"type": "function", "function": {"name": "deep_think"}}
				else:
					tool_choice = "auto"
				payload = {**payload, "tools": available_tools, "tool_choice": tool_choice}
		# 显式 use_rag 时仍然预执行 RAG（RAG 没有 function calling 等价物）
		if bool(payload.get("use_rag")):
			tool_results = pre_execute_tools(payload, last_user_msg)
			rag_results = [r for r in tool_results if r["name"] == "memory_search"]
			if rag_results:
				messages = _inject_context_to_system(messages, rag_results)
	else:
		# ── 降级路径：网关不主动注入 GATEWAY_TOOLS（防止模型"演"工具而非真正调用）──
		# • 若 client（Chatbox）自带 tools（KB Function Calling）→ 保留，deepseek-chat 原生支持 FC
		# • 若 client 没有 tools → 网关自己预执行（web_search / RAG），结果注入 system
		_client_sent_tools = bool(payload.get("tools"))
		if not _client_sent_tools:
			# 只在无 client tools 时剥除（防止后续误传），有 client tools 时保留
			payload = {k: v for k, v in payload.items() if k not in ("tools", "tool_choice")}
		tool_results = pre_execute_tools(payload, last_user_msg)
		try:
			import logging
			logging.warning("[pre_execute] model=%s last_user_msg=%r results=%s",
							model_name, last_user_msg[:80],
							[r['name'] for r in tool_results])
		except Exception:
			pass
		if tool_results:
			messages = _inject_context_to_system(messages, tool_results)
		else:
			# 当 system 含 Chatbox KB Prompt Engineering 描述（## query_knowledge_base 等）时
			# 不注入 hint——模型需要按 system 描述输出 KB query JSON 让 Chatbox 本地拦截执行。
			_sys_for_pp = " ".join(
				(m.get("content") or "") if isinstance(m.get("content"), str)
				else " ".join(p.get("text", "") for p in (m.get("content") or [])
					if isinstance(p, dict))
				for m in messages if m.get("role") == "system"
			)
			if "query_knowledge_base" not in _sys_for_pp:
				messages = _inject_no_tool_hint(messages)

	# —— deepseek-reasoner 多轮兼容 ——
	# 官方文档：https://api-docs.deepseek.com/guides/reasoning_model
	# 1. 输入消息中若含 reasoning_content 字段 → API 返回 400，必须剥除
	# 2. 多轮对话 assistant 消息只需 {role, content}，不传 reasoning_content
	# 3. tool_calls / tool 消息需合并为纯文本（不支持 function calling）
	# 4. 不支持的参数（temperature, top_p, presence_penalty, frequency_penalty）需从 payload 剥除
	if "reasoner" in model_name:
		# 调试：写入日志，分析 Chatbox 实际发来的消息结构
		try:
			import logging, json as _json
			_dbg = [(m.get("role"), list(m.keys())) for m in messages]
			logging.warning("[reasoner-clean] incoming message keys: %s", _dbg)
			with open("/tmp/ai_memory_reasoner_debug.json", "w", encoding="utf-8") as _f:
				_json.dump(messages, _f, ensure_ascii=False, indent=2, default=str)
		except Exception:
			pass

		# 剥除 reasoner 不支持的顶层参数
		_UNSUPPORTED = {"temperature", "top_p", "presence_penalty", "frequency_penalty",
						"logprobs", "top_logprobs"}
		payload = {k: v for k, v in payload.items() if k not in _UNSUPPORTED}

		cleaned: List[Dict] = []
		i = 0
		while i < len(messages):
			m = messages[i]
			role = m.get("role")
			has_tool_calls = "tool_calls" in m

			if role == "assistant" and has_tool_calls:
				# 含 tool_calls 的 assistant 消息：将工具结果合并为纯文本 content
				tool_texts: List[str] = []
				j = i + 1
				while j < len(messages) and messages[j].get("role") == "tool":
					tool_texts.append(messages[j].get("content") or "")
					j += 1
				own_content = (m.get("content") or "").strip()
				merged_parts = ([own_content] if own_content else []) + tool_texts
				merged_content = "\n\n".join(merged_parts).strip() or "[工具调用结果已省略]"
				# 只保留 role + content，不含 reasoning_content（含则 400）
				cleaned.append({"role": "assistant", "content": merged_content})
				i = j
			elif role == "tool":
				# 孤立 tool 消息（无对应 assistant），跳过
				i += 1
			elif role == "assistant":
				# 普通 assistant 消息：只保留 role + content，剥除 reasoning_content / tool_calls
				cleaned.append({"role": "assistant", "content": m.get("content") or ""})
				i += 1
			else:
				# system / user：原样保留（content 可以是 str 或 list，均不动）
				cleaned.append(m)
				i += 1
		messages = cleaned

		# 调试：写入清洗后的消息，供对比分析
		try:
			import json as _json2
			with open("/tmp/ai_memory_reasoner_debug_cleaned.json", "w", encoding="utf-8") as _f2:
				_json2.dump({
					"model": model_name,
					"has_tools_in_payload": "tools" in payload,
					"payload_keys": list(payload.keys()),
					"msg_count_before": len(payload.get("messages", [])),
					"msg_count_after": len(messages),
					"cleaned_messages": messages,
				}, _f2, ensure_ascii=False, indent=2, default=str)
		except Exception:
			pass

	return {**payload, "messages": messages}


async def forward_to_model(body: Dict[str, Any]) -> Dict[str, Any]:
	"""将 ChatCompletion 请求代理到上游模型。

	TODO：实现真实的 HTTP 调用与认证。当前仅抛出异常，提醒你补全。
	预期行为：
	- POST 到 f"{UPSTREAM_BASE_URL}/v1/chat/completions"
	- 头部携带 Authorization: Bearer <API key>
	- Body 直接用 `body`（已兼容 OpenAI 格式）
	- 返回上游 JSON（若格式略有不同，可在此适配）。
	"""

	# 允许通过环境变量覆盖常量配置
	base_url = os.getenv("UPSTREAM_BASE_URL", UPSTREAM_BASE_URL).rstrip("/")
	api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or UPSTREAM_API_KEY
	if not api_key:
		raise HTTPException(status_code=500, detail="未配置上游 API Key（DEEPSEEK_API_KEY/OPENAI_API_KEY 或常量 UPSTREAM_API_KEY）")

	url = f"{base_url}/v1/chat/completions"
	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
	}

	# 默认模型名（如未指定）
	body = dict(body)
	body.setdefault("model", "deepseek-chat")

	client: httpx.AsyncClient = app.state.http_client
	resp = await client.post(url, headers=headers, json=body)
	resp.raise_for_status()
	return resp.json()


def _filter_sse_chunk(chunk: bytes) -> bytes:
	"""
	过滤单个 SSE chunk：将 delta.content / delta.reasoning_content 中的 DSML 标记清除。
	若 chunk 不含 DSML 则原样返回（零开销快路径）。
	"""
	raw = chunk.decode("utf-8", errors="ignore")
	if '\uff5cDSML\uff5c' not in raw:
		return chunk

	out_lines: List[str] = []
	for line in raw.splitlines(keepends=True):
		if not line.startswith("data: "):
			out_lines.append(line)
			continue
		payload = line[6:].strip()
		if payload in ("[DONE]", ""):
			out_lines.append(line)
			continue
		try:
			data = json.loads(payload)
			delta = (data.get("choices") or [{}])[0].get("delta", {})
			changed = False
			for key in ("content", "reasoning_content"):
				if isinstance(delta.get(key), str) and '\uff5cDSML\uff5c' in delta[key]:
					delta[key] = _strip_dsml(delta[key])
					changed = True
			if changed:
				data["choices"][0]["delta"] = delta
				out_lines.append("data: " + json.dumps(data, ensure_ascii=False) + "\n")
			else:
				out_lines.append(line)
		except Exception:
			out_lines.append(line)
	return "".join(out_lines).encode("utf-8")


def _clean_chunk_for_client(chunk: bytes, passthrough_tool_calls: bool = False) -> bytes:
	"""
	清洗发往客户端的 SSE chunk。
	- passthrough_tool_calls=True：透传 tool_calls（FC Round 1 时 Chatbox 需要收到以执行工具）
	- passthrough_tool_calls=False（默认）：过滤掉 tool_calls（网关自己处理工具时）
	始终:
	  1. 去除 data: [DONE]（由网关统一在最终轮发出）
	  2. 清除 DSML 标记
	"""
	raw = chunk.decode("utf-8", errors="ignore")
	out_lines: List[str] = []
	for line in raw.splitlines(keepends=True):
		if not line.startswith("data: "):
			out_lines.append(line)
			continue
		payload_str = line[6:].strip()
		if payload_str == "[DONE]":
			continue  # 由网关自己发
		if not payload_str:
			out_lines.append(line)
			continue
		try:
			data = json.loads(payload_str)
			choice = (data.get("choices") or [{}])[0]
			delta = choice.get("delta", {})

			# 非透传模式：过滤网关自己处理的 tool_calls
			if not passthrough_tool_calls:
				if "tool_calls" in delta:
					continue
				if choice.get("finish_reason") == "tool_calls":
					continue

			# 清洗 DSML
			changed = False
			for key in ("content", "reasoning_content"):
				if isinstance(delta.get(key), str) and '\uff5cDSML\uff5c' in delta[key]:
					delta[key] = _strip_dsml(delta[key])
					changed = True
			if changed:
				data["choices"][0]["delta"] = delta
				out_lines.append("data: " + json.dumps(data, ensure_ascii=False) + "\n")
			else:
				out_lines.append(line)
		except Exception:
			out_lines.append(line)
	result = "".join(out_lines)
	return result.encode("utf-8") if result.strip() else b""


def _parse_sse_tool_calls(raw_bytes: bytes) -> tuple[Optional[str], List[Dict]]:
	"""
	解析 SSE 字节流，返回 (finish_reason, tool_calls_list)。
	tool_calls_list 中每项为 {id, type, function: {name, arguments}}（arguments 已拼接完整）。
	"""
	finish_reason: Optional[str] = None
	tc_acc: Dict[int, Dict] = {}  # index → partial tool call

	for line in raw_bytes.decode("utf-8", errors="ignore").splitlines():
		line = line.strip()
		if not line.startswith("data: ") or line == "data: [DONE]":
			continue
		try:
			data = json.loads(line[6:])
			choice = (data.get("choices") or [{}])[0]
			if choice.get("finish_reason"):
				finish_reason = choice["finish_reason"]
			for tc in choice.get("delta", {}).get("tool_calls") or []:
				idx = tc.get("index", 0)
				if idx not in tc_acc:
					tc_acc[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
				if tc.get("id"):
					tc_acc[idx]["id"] = tc["id"]
				if tc.get("function", {}).get("name"):
					tc_acc[idx]["function"]["name"] += tc["function"]["name"]
				if tc.get("function", {}).get("arguments"):
					tc_acc[idx]["function"]["arguments"] += tc["function"]["arguments"]
		except Exception:
			pass

	return finish_reason, [tc_acc[i] for i in sorted(tc_acc)]


def _sse_progress(text: str) -> bytes:
	"""生成一个可见的进度提示 SSE delta chunk，Chatbox 会直接显示其内容。
	为避免 uvicorn 缓冲积累小包，前缀 SSE 注释行填充到 >1KB，确保每个块都立即冲刷发出。"""
	chunk = ("data: " + json.dumps({
		"id": "gw-progress",
		"choices": [{"delta": {"content": text}, "index": 0, "finish_reason": None}]
	}, ensure_ascii=False) + "\n\n")
	# SSE 注释行（客户端完全忽略），填充到 1200 字节
	padding = ": " + " " * max(0, 1200 - len(chunk.encode("utf-8"))) + "\n\n"
	return (padding + chunk).encode("utf-8")


async def _execute_chat_quick(prompt: str, api_key: str, base_url: str) -> str:
	"""用上游 Chat 模型（非 streaming）快速处理单轮问题，返回 content 字符串。
	使用独立短超时 client，避免与 shared pool 冲突。"""
	body = {
		"model": os.getenv("GATEWAY_DEFAULT_MODEL", "deepseek-chat"),
		"stream": False,
		"messages": [{"role": "user", "content": prompt}],
	}
	try:
		async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as _c:
			resp = await _c.post(
				f"{base_url.rstrip('/')}/v1/chat/completions",
				headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
				json=body,
			)
			resp.raise_for_status()
			return resp.json()["choices"][0]["message"].get("content") or ""
	except Exception as exc:
		logging.warning("[chat_quick] failed: %s", exc)
		return ""


async def forward_to_model_stream(body: Dict[str, Any]):
	"""以流式方式把请求转发到上游，支持多轮工具调用循环（含 deep_think）。"""
	base_url = os.getenv("UPSTREAM_BASE_URL", UPSTREAM_BASE_URL).rstrip("/")
	api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or UPSTREAM_API_KEY
	if not api_key:
		raise HTTPException(status_code=500, detail="未配置上游 API Key（DEEPSEEK_API_KEY/OPENAI_API_KEY 或常量 UPSTREAM_API_KEY）")

	url = f"{base_url}/v1/chat/completions"
	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
	}
	body = dict(body)
	body["stream"] = True
	body.setdefault("model", "deepseek-chat")

	client: httpx.AsyncClient = app.state.http_client

	async def _gen():
		# 立即发送一个 SSE 注释包，触发 HTTP 响应帧建立，防止首包被缓冲
		# \u7b2c\u4e00\u4e2a\u5305\u5fc5\u987b\u542b role:assistant\uff0c\u5426\u5219 Chatbox \u4e0d\u4f1a\u5f00\u59cb\u6e32\u67d3\u540e\u7eed content chunk
		_init_chunk = ("data: " + json.dumps({
			"id": "gw-0", "object": "chat.completion.chunk",
			"created": int(time.time()), "model": body.get("model", "deepseek-chat"),
			"choices": [{"delta": {"role": "assistant"}, "index": 0, "finish_reason": None}]
		}, ensure_ascii=False) + "\n\n").encode("utf-8")
		logging.warning("[downstream/init] sending init chunk")
		yield _init_chunk
		messages = list(body.get("messages", []))

		# 取最后一条用户消息
		last_user_msg = ""
		for _m in reversed(messages):
			if _m.get("role") == "user":
				_c = _m.get("content", "")
				if isinstance(_c, str):
					last_user_msg = _c
				elif isinstance(_c, list):
					last_user_msg = " ".join(
						p.get("text", "") for p in _c
						if isinstance(p, dict) and p.get("type") == "text"
					)
				break

		_dt_enabled = os.getenv("GATEWAY_TOOL_DEEP_THINK", "false").strip().lower() == "true"
		_ws_enabled = os.getenv("GATEWAY_TOOL_WEB_SEARCH", "false").strip().lower() == "true"

		# ── DEBUG: 完整请求信息 ────────────────────────────────────────────
		_incoming_tools = [t.get("function", {}).get("name") for t in (body.get("tools") or [])]
		logging.warning("[request] model=%s dt=%r ws=%r incoming_tools=%r last_user_msg=%r",
			body.get("model"), _dt_enabled, _ws_enabled, _incoming_tools, last_user_msg[:120])
		# 打印完整 messages 结构（便于排查 Chatbox 注入的 system/user 内容）
		for _di, _dm in enumerate(messages):
			_drole = _dm.get("role", "?")
			_dc = _dm.get("content") or ""
			if isinstance(_dc, list):
				_dc = " | ".join(p.get("text", "")[:80] for p in _dc if isinstance(p, dict))
			_extra = {}
			if _dm.get("tool_calls"):
				# assistant 消息里的 tool_calls（FC Round 1 模型回复）
				_extra["tool_calls"] = [
					{"id": tc.get("id"), "name": tc.get("function", {}).get("name"),
					 "args": tc.get("function", {}).get("arguments", "")[:80]}
					for tc in _dm["tool_calls"]
				]
			if _dm.get("tool_call_id"):
				# tool 消息里的关联 id 和工具名
				_extra["tool_call_id"] = _dm["tool_call_id"]
				_extra["name"] = _dm.get("name")
			logging.warning("[request/msg#%d] role=%s content=%r extra=%r",
				_di, _drole, str(_dc)[:300], _extra)

		# ── Chatbox Prompt Engineering 搜索意图检测 ──────────────────────

		# ── Chatbox Prompt Engineering 搜索意图检测 ──────────────────────
		# Chatbox 自定义 API 时走两步 Prompt Engineering 搜索：
		#   请求① system 含 constructSearchAction prompt，期望模型回 {"action":"search","query":"..."}
		#   请求② Chatbox 把搜索结果注入 user 消息后发来，网关走完整 Steps 1-4
		# 网关在请求① 时：先跑 Step1 提取优化关键词，再返回 JSON 让 Chatbox 本地执行搜索。
		# content 字段可能是 str，也可能是 Chatbox 发来的 array 格式：
		#   [{"type":"text","text":"..."}]
		# 统一提取为字符串，供后续 in 子字符串检查使用
		def _msg_text(c) -> str:
			if isinstance(c, str):
				return c
			if isinstance(c, list):
				return " ".join(
					p.get("text", "") for p in c
					if isinstance(p, dict) and p.get("type") == "text"
				)
			return str(c) if c else ""

		_chatbox_search_prompt_markers = ['"action"', '"search"', '"proceed"', 'JSON schema']
		_is_chatbox_search_intent = False
		for _sm in messages:
			if _sm.get("role") == "system":
				_sys_text = _msg_text(_sm.get("content"))
				_marker_hits = {m: (m in _sys_text) for m in _chatbox_search_prompt_markers}
				_matched = all(_marker_hits.values())
				logging.warning("[chatbox/intent_check] system_len=%d markers=%s matched=%r sys_head=%r",
					len(_sys_text), _marker_hits, _matched, _sys_text[:300])
				if _matched:
					_is_chatbox_search_intent = True
					break
		logging.warning("[chatbox/intent_check] result=%r last_user_msg=%r",
			_is_chatbox_search_intent, last_user_msg[:120])

		if _is_chatbox_search_intent and last_user_msg:
			# Step1：提取优化搜索关键词（不走 Step2-4）
			_t0 = time.monotonic()
			_kw_result = await _execute_chat_quick(
				"[搜索关键词提取]请根据以下用户问题，给出最适合搜索引擎的简洁关键词"
				"（中英文均可，直接输出关键词或短语，不需要解释）：\n\n" + last_user_msg,
				api_key, base_url,
			)
			# 取第一行非空内容作为关键词，失败时退回原始用户消息
			_search_kw = next(
				(ln.strip() for ln in _kw_result.splitlines() if ln.strip()),
				last_user_msg
			)[:200]
			logging.warning("[chatbox/search_intent] Step1 elapsed=%.2fs kw_raw=%r keyword=%r",
				time.monotonic() - _t0, _kw_result[:100], _search_kw)
			# 检测是 combined search prompt（同时有知识库+联网）还是单纯 web/kb 搜索。
			# Chatbox 的 constructCombinedSearchAction 枚举包含 "search_web"；
			# contructSearchAction / constructKnowledgeBaseSearchAction 枚举中只有 "search"。
			_is_combined_prompt = any("search_web" in _msg_text(_sm.get("content"))
				for _sm in messages if _sm.get("role") == "system")
			_action_value = "search_web" if _is_combined_prompt else "search"
			logging.warning("[chatbox/search_intent] combined=%r action=%r", _is_combined_prompt, _action_value)
			_search_json = json.dumps({"action": _action_value, "query": _search_kw}, ensure_ascii=False)
			_si_c1 = ("data: " + json.dumps({
				"id": "gw-si", "object": "chat.completion.chunk",
				"created": int(time.time()), "model": body.get("model", "deepseek-chat"),
				"choices": [{"delta": {"role": "assistant", "content": _search_json},
					"index": 0, "finish_reason": None}]
			}, ensure_ascii=False) + "\n\n").encode("utf-8")
			logging.warning("[downstream/intent_content] %r", _si_c1[:300])
			yield _si_c1
			_si_c2 = ("data: " + json.dumps({
				"id": "gw-si", "object": "chat.completion.chunk",
				"created": int(time.time()), "model": body.get("model", "deepseek-chat"),
				"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]
			}, ensure_ascii=False) + "\n\n").encode("utf-8")
			logging.warning("[downstream/intent_stop] %r", _si_c2[:200])
			yield _si_c2
			logging.warning("[downstream/intent_done]")
			yield b"data: [DONE]\n\n"
			return

		# 步骤执行状态标志：每步以上一步的 _sN_ran 为唯一前提，输出结束后重置
		_s1_ran = False   # Step1: 问题理解/关键词提取
		_s2_ran = False   # Step2: 工具调用（搜索等）
		_s3_ran = False   # Step3: Reasoner 综合推理
		_s4_ran = False   # Step4: Chat 模型流式输出

		# 辅助变量
		_client_has_ws_tool = any(
			t.get("function", {}).get("name") == "web_search"
			for t in (body.get("tools") or [])
		)
		# 检测是否已有搜索结果：
		#   网关自身注入：system 消息含 [实时搜索结果]
		#   Chatbox 注入：user 消息含 [webpage N begin]（constructMessagesWithSearchResults 格式）
		# 注意：content 可能是 str 或 array，统一用 _msg_text() 提取
		_already_has_search = (
			any("[实时搜索结果]" in _msg_text(m.get("content")) for m in messages if m.get("role") == "system")
			or any("[webpage " in _msg_text(m.get("content")) for m in messages if m.get("role") == "user")
		)
		# Chatbox 已携带搜索结果时，提取其内容供网关补充搜索时合并上下文
		_chatbox_search_result = ""
		if _already_has_search:
			for _m in reversed(messages):
				if _m.get("role") == "user" and "[webpage " in _msg_text(_m.get("content")):
					_chatbox_search_result = _msg_text(_m.get("content"))
					logging.warning("[chatbox/search_result] found in user msg, len=%d", len(_chatbox_search_result))
					break

		logging.warning("[detect] already_has_search=%r chatbox_result_len=%d client_tool=%r",
			_already_has_search, len(_chatbox_search_result), _client_has_ws_tool)

		# ── Chatbox KB Function Calling 检测 ────────────────────────────
		# Chatbox 勾选「工具使用」后走真正的 Function Calling，共两个 Round：
		#
		#   Round 1（无 role=tool）：完全透传给上游模型
		#     → 模型一次发出所有 tool_calls（KB + web_search 等）
		#     → Chatbox 本地执行全部工具，收集结果后发出 Round 2
		#
		#   Round 2（有 role=tool）：网关接管
		#     → 从 role=tool 消息中提取 KB 结果和 web_search 结果
		#     → 走 Step1/Step3/Step4 pipeline 生成最终回答
		#
		# 默认值（非 FC 路径时保持空）
		_kb_text = ""
		_ws_tool_result = ""

		_fc_client_tools = body.get("tools") or []
		_has_tool_results = any(m.get("role") == "tool" for m in messages)

		# Round 1：透传；Round 2：接管
		_fc_passthrough = bool(_fc_client_tools) and not _has_tool_results
		_fc_takeover    = bool(_fc_client_tools) and _has_tool_results

		logging.warning("[detect/fc] client_tools=%d has_tool_results=%r passthrough=%r takeover=%r",
			len(_fc_client_tools), _has_tool_results, _fc_passthrough, _fc_takeover)

		# ── FC Round 1：完全透传，tool_calls 不过滤 ──────────────────────
		if _fc_passthrough:
			_pt_body = {
				**body,
				"messages": _sanitize_messages(messages),
				"model": os.getenv("GATEWAY_DEFAULT_MODEL", "deepseek-chat"),
				"stream": True,
			}
			logging.warning("[fc/round1] passthrough with %d tools", len(_fc_client_tools))
			async with client.stream("POST", url, headers=headers, json=_pt_body) as _pt_resp:
				if _pt_resp.status_code >= 400:
					_pt_err = (await _pt_resp.aread()).decode("utf-8", errors="ignore")
					yield ("data: " + json.dumps({"error": {"message": _pt_err[:300], "type": "upstream_error", "code": _pt_resp.status_code}}, ensure_ascii=False) + "\n\n").encode()
				else:
					async for _pt_chunk in _pt_resp.aiter_bytes():
						# passthrough_tool_calls=True：tool_calls chunk 原样发给 Chatbox
						_filtered = _clean_chunk_for_client(_pt_chunk, passthrough_tool_calls=True)
						if _filtered:
							yield _filtered
			yield b"data: [DONE]\n\n"
			return

		# ── FC 接管：提取所有工具结果，净化消息列表 ────────────────────────
		# role=tool 消息和含 tool_calls 的 assistant 消息必须在进入 pipeline 前清除，
		# 否则 deepseek-chat 看到工具调用格式后会原样复制，形成死循环。
		# 工具内容（KB + web_search）在此提取，供 Step3/Step4 使用。
		if _fc_takeover:
			# 分别提取不同工具的结果
			for _m in messages:
				if _m.get("role") != "tool":
					continue
				# 通过 tool_call_id 找到对应的工具名
				_tc_id = _m.get("tool_call_id")
				_tool_name = ""
				for _am in messages:
					if _am.get("role") == "assistant" and _am.get("tool_calls"):
						for _tc in _am["tool_calls"]:
							if _tc.get("id") == _tc_id:
								_tool_name = _tc.get("function", {}).get("name", "")
				_content = _m.get("content") or ""
				if isinstance(_content, list):
					_content = " ".join(p.get("text", "") for p in _content if isinstance(p, dict))
				if _tool_name == "web_search":
					_ws_tool_result = _content
				elif _tool_name in ("query_knowledge_base", "") and _content:
					_kb_text = (_kb_text + "\n\n" + _content).strip() if _kb_text else _content

			messages, _stripped_kb = _strip_fc_exchange(messages)
			# _strip_fc_exchange 返回的是所有 tool 消息内容合并；若 _kb_text 为空则用它
			if not _kb_text and _stripped_kb:
				_kb_text = _stripped_kb

			if _kb_text:
				logging.warning("[fc/takeover] kb_text len=%d", len(_kb_text))
			if _ws_tool_result:
				logging.warning("[fc/takeover] ws_tool_result len=%d content=%r",
					len(_ws_tool_result), _ws_tool_result[:100])

		# ── Step 1: 问题理解（Round 2+ 执行）──────────────────────────────
		# 提取意图与关键词，结果注入 Step3 推理上下文
		step1_conclusion = ""
		if last_user_msg:
			_t0 = time.monotonic()
			_s1_prompt = (
				"[第一步:理解问题]请简要分析以下问题，列出回答需要的关键信息点"
				"（如果需要联网搜索，请给出建议的搜索关键词）：\n\n" + last_user_msg
			)
			logging.warning("[step1/input] prompt=%r", _s1_prompt[:500])
			step1_conclusion = await _execute_chat_quick(_s1_prompt, api_key, base_url)
			logging.warning("[step1/chat] elapsed=%.2fs conclusion=%r",
				time.monotonic() - _t0, step1_conclusion[:200])
		else:
			logging.warning("[step1/chat] skip: no user message")
		_s1_ran = True

		# ── Step 2: 工具调用（前提：_s1_ran）────────────────────────────
		# 搜索触发逻辑：
		#   A. _ws_enabled=True：网关主动补充搜索（搜索词 = Step1 提取的关键词，fallback 原始消息）
		#   B. _client_has_ws_tool=True 且未搜过且非 FC 接管：客户端声明工具但未搜，网关代为执行
		#   C. FC 接管且 web_search 已由 Chatbox 执行（_ws_tool_result 已填充）：直接使用，不重复搜
		# Step4 永远剥离 tools，搜索职责完全归 Step2
		ws_result = ""
		if _s1_ran:
			_need_search_gw    = _ws_enabled and bool(last_user_msg)
			# 非 FC 接管时：客户端声明有 web_search 工具但本次未搜 → 网关代为执行
			_need_search_proxy = _client_has_ws_tool and bool(last_user_msg) and not _already_has_search and not _has_tool_results
			# FC 接管但模型未调 web_search（_ws_tool_result 为空）→ 网关静默补搜（不发 tool_call chunk，避免死循环）
			_need_search_fc_fallback = _fc_takeover and not _ws_tool_result and bool(last_user_msg) and external_invoke_tool is not None

			# 搜索词：直接取用户原始消息截断（Step1 结论可能是模型套话，不可靠）
			_ws_search_query_raw = last_user_msg[:80]

			if _ws_tool_result:
				# FC 链中 Chatbox 已完成 web_search，直接使用其结果
				ws_result = _ws_tool_result
				logging.warning("[step2/web_search] using chatbox fc result, len=%d", len(ws_result))
			elif _need_search_gw or _need_search_proxy:
				_ws_chunk = ("data: " + json.dumps({
					"id": "gw-ws", "object": "chat.completion.chunk",
					"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_gw_ws",
						"type": "function", "function": {"name": "web_search",
						"arguments": json.dumps({"query": _ws_search_query_raw})}}]},
						"index": 0, "finish_reason": None}]
				}, ensure_ascii=False) + "\n\n").encode("utf-8")
				logging.warning("[downstream/step2_tool_call] %r", _ws_chunk[:300])
				yield _ws_chunk
				_t0 = time.monotonic()
				ws_result = await _execute_gateway_tool("web_search", {"query": _ws_search_query_raw})
				logging.warning("[step2/web_search] done elapsed=%.2fs result_len=%d gw=%r proxy=%r query=%r",
					time.monotonic() - _t0, len(ws_result),
					_need_search_gw, _need_search_proxy, _ws_search_query_raw[:60])
			elif _need_search_fc_fallback:
				# FC 接管：模型未调 web_search，网关静默补搜（不向 Chatbox 发 tool_call chunk）
				_t0 = time.monotonic()
				ws_result = await _execute_gateway_tool("web_search", {"query": _ws_search_query_raw})
				logging.warning("[step2/web_search] fc_fallback silent search elapsed=%.2fs result_len=%d query=%r",
					time.monotonic() - _t0, len(ws_result), _ws_search_query_raw[:60])
			else:
				logging.warning("[step2/web_search] skip: ws=%r client_tool=%r already=%r has_msg=%r takeover=%r",
					_ws_enabled, _client_has_ws_tool, _already_has_search, bool(last_user_msg), _fc_takeover)
			_s2_ran = True

		# ── Step 3: Reasoner 推理（前提：_s2_ran，仅 dt_enabled 时执行）─────────
		# 无 Reasoner 时直接跳过，由 Step4 一次性处理（减少一次上游调用）。
		# 有 Reasoner 时：
		#   - 流式输出 reasoning_content 给客户端（实时，不阻塞 Step4）
		#   - 同步收集完整 reasoning_content，推理结束后立即启动 Step4
		#   - 按 DeepSeek 官方建议：将 reasoning_content 以 <think>…</think> 块
		#     注入上下文传给 Chat 模型，保留完整推理链
		step3_conclusion = ""     # Reasoner 输出的 content（通常为空或简短摘要）
		_dt_reasoning_text = ""   # Reasoner 输出的 reasoning_content（完整思维链）
		if _s2_ran and last_user_msg and _dt_enabled:
			_dt_prompt = f"用户问题：{last_user_msg}\n\n"
			if _kb_text:
				# 截断 KB 避免 reasoner 超时（取前 3000 字已足够推理）
				_kb_for_dt = _kb_text[:3000] + (f"…[共{len(_kb_text)}字，已截断]") if len(_kb_text) > 3000 else _kb_text
				_dt_prompt += f"[知识库查询结果]\n{_kb_for_dt}\n\n"
			if ws_result:
				_dt_prompt += f"[联网搜索结果]\n{ws_result}\n\n"
			_dt_prompt += "请基于以上信息进行深度推理，分析关键论点和论据。只输出推理过程，不需要给出完整回答正文。"
			_t0 = time.monotonic()
			yield ("data: " + json.dumps({
				"id": "gw-dt-s",
				"choices": [{"delta": {"reasoning_content": ""}, "index": 0, "finish_reason": None}]
			}, ensure_ascii=False) + "\n\n").encode("utf-8")
			_reasoning_parts: List[str] = []
			_dt_parts: List[str] = []
			async for _dt_type, _dt_text in _stream_deep_think(_dt_prompt):
				if _dt_type == "reasoning":
					# 实时透传 reasoning_content 给客户端
					yield ("data: " + json.dumps({
						"id": "gw-dt",
						"choices": [{"delta": {"reasoning_content": _dt_text}, "index": 0, "finish_reason": None}]
					}, ensure_ascii=False) + "\n\n").encode("utf-8")
					_reasoning_parts.append(_dt_text)
				elif _dt_type == "content":
					_dt_parts.append(_dt_text)
			# 推理完毕，立即进入 Step4（不引入额外延迟）
			_dt_reasoning_text = "".join(_reasoning_parts)   # 完整思维链
			step3_conclusion   = "".join(_dt_parts)          # Reasoner 自身的 content 输出
			logging.warning("[step3/reasoner] done elapsed=%.2fs reasoning_len=%d conclusion_len=%d",
				time.monotonic() - _t0, len(_dt_reasoning_text), len(step3_conclusion))
			_s3_ran = True
		elif _s2_ran:
			# 无 Reasoner：跳过 Step3，Step4 一次性处理 Step3+4
			logging.warning("[step3] skip: dt_enabled=%r, Step4 handles all", _dt_enabled)
			_s3_ran = True

		# ── Step 4: 最终回答输出（前提：_s3_ran）────────────────────────────
		# 优先策略：若 Reasoner 已在 step3_conclusion 中输出完整答案，直接流式转发，
		#   跳过对 chat 模型的额外调用（节省 1 次 API + 约 20-40s）。
		# 兜底策略：Reasoner 未输出结论（或未启用 Reasoner），注入所有上下文后调 chat 模型。
		if _s3_ran:
			if step3_conclusion:
				# ── 直接流式输出 Reasoner 结论，跳过 Step4 chat 调用 ──
				logging.warning("[step4/skip] reasoner already answered, conclusion_len=%d", len(step3_conclusion))
				_chunk_size = 80
				for _ci in range(0, len(step3_conclusion), _chunk_size):
					yield ("data: " + json.dumps({
						"id": "gw-s3c", "object": "chat.completion.chunk",
						"choices": [{"delta": {"content": step3_conclusion[_ci:_ci+_chunk_size]},
							"index": 0, "finish_reason": None}]
					}, ensure_ascii=False) + "\n\n").encode("utf-8")
				yield ("data: " + json.dumps({
					"id": "gw-s3c", "object": "chat.completion.chunk",
					"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]
				}, ensure_ascii=False) + "\n\n").encode("utf-8")
				_s4_ran = True
			else:
				# ── Reasoner 无结论 或 未启用 Reasoner：调 chat 模型生成最终回答 ──
				context_parts: List[str] = []
				if _kb_text:
					context_parts.append(f"[知识库查询结果]\n{_kb_text}")
				if ws_result:
					context_parts.append(f"[联网搜索结果]\n{ws_result}")
				if _dt_reasoning_text:
					# Reasoner 有推理过程但无结论：注入 <think> 块，让 chat 模型基于推理写答案
					_think_block = f"<think>\n{_dt_reasoning_text}\n</think>"
					context_parts.append(f"[Reasoner 推理过程]\n{_think_block}")
				elif step1_conclusion:
					context_parts.append(f"[问题分析]\n{step1_conclusion}")

				if context_parts:
					messages = _inject_context_to_system(messages, "\n\n".join(context_parts))
				else:
					messages = _inject_no_tool_hint(messages)

				final_body = {
					**body,
					"messages": _sanitize_messages(messages),
					"model": os.getenv("GATEWAY_DEFAULT_MODEL", "deepseek-chat"),
					"stream": True,
				}
				final_body.pop("tools", None)
				final_body.pop("tool_choice", None)

				_s4_ran = True
				logging.warning("[step4/chat] s1=%r s2=%r s3=%r ws=%r dt=%r msgs=%d",
					_s1_ran, _s2_ran, _s3_ran, bool(ws_result), _dt_enabled,
					len(final_body["messages"]))

				async with client.stream("POST", url, headers=headers, json=final_body) as resp:
					if resp.status_code >= 400:
						err_bytes = await resp.aread()
						err = err_bytes.decode("utf-8", errors="ignore")
						if err.strip().startswith("<"):
							err = f"HTTP {resp.status_code} from upstream (HTML response truncated)"
						yield ("data: " + json.dumps({"error": {"message": err[:300], "type": "upstream_error", "code": resp.status_code}}, ensure_ascii=False) + "\n\n").encode()
						yield b"data: [DONE]\n\n"
						_s1_ran = _s2_ran = _s3_ran = _s4_ran = False
						return
					async for chunk in resp.aiter_bytes():
						filtered = _clean_chunk_for_client(chunk)
						if filtered:
							yield filtered
		yield b"data: [DONE]\n\n"
		# 输出结束，重置步骤状态标志
		_s1_ran = _s2_ran = _s3_ran = _s4_ran = False
	return _gen



@app.get("/v1/config")
async def config_get() -> JSONResponse:
	"""返回当前运行时配置（API Key 脱敏显示）。"""
	base_url = os.getenv("UPSTREAM_BASE_URL", UPSTREAM_BASE_URL)
	raw_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or UPSTREAM_API_KEY
	masked = (raw_key[:6] + "****" + raw_key[-4:]) if len(raw_key) > 10 else ("****" if raw_key else "")
	return JSONResponse(content={
		"base_url":        base_url,
		"api_key_masked":  masked,
		"model_name":      os.getenv("GATEWAY_MODEL_NAME",      _GATEWAY_MODEL_NAME_DEFAULT),
		"default_model":   os.getenv("GATEWAY_DEFAULT_MODEL",   "deepseek-chat"),
		"tool_web_search": os.getenv("GATEWAY_TOOL_WEB_SEARCH", "auto"),
		"tool_rag":        os.getenv("GATEWAY_TOOL_RAG",        "false").strip().lower() == "true",
	})


@app.post("/v1/config")
async def config_save(payload: Dict[str, Any]) -> JSONResponse:
	"""将配置写入 .env 文件并热更新环境变量。"""
	env_path = os.path.join(os.path.dirname(__file__), ".env")
	base_url:      str = payload.get("base_url",        "").strip()
	api_key:       str = payload.get("api_key",         "").strip()
	model_name:    str = payload.get("model_name",      "").strip()
	default_model: str = payload.get("default_model",   "").strip()
	tool_ws:       str = payload.get("tool_web_search", "").strip().lower()
	tool_rag:      str = str(payload.get("tool_rag", "")).strip().lower()

	# 读取现有 .env（保留其他配置项）
	lines: list[str] = []
	if os.path.exists(env_path):
		with open(env_path, "r", encoding="utf-8") as f:
			lines = f.readlines()

	def _set_key(kv_lines: list[str], key: str, value: str) -> list[str]:
		"""更新或追加一个 KEY=VALUE 行。"""
		prefix = key + "="
		replaced = False
		result = []
		for ln in kv_lines:
			if ln.startswith(prefix):
				result.append(f"{key}={value}\n")
				replaced = True
			else:
				result.append(ln)
		if not replaced:
			result.append(f"{key}={value}\n")
		return result

	if base_url:
		lines = _set_key(lines, "UPSTREAM_BASE_URL",    base_url)
		os.environ["UPSTREAM_BASE_URL"] = base_url
	if api_key:
		lines = _set_key(lines, "DEEPSEEK_API_KEY",     api_key)
		os.environ["DEEPSEEK_API_KEY"]  = api_key
	if model_name:
		lines = _set_key(lines, "GATEWAY_MODEL_NAME",    model_name)
		os.environ["GATEWAY_MODEL_NAME"] = model_name
	if default_model:
		# ── 拒绝内部专用模型 ──
		if default_model in _INTERNAL_ONLY_MODELS:
			raise HTTPException(
				status_code=400,
				detail=(
					f"模型 '{default_model}' 已作为内置深度思考的后端，"
					"无需单独选择；选择 deepseek-chat 即可自动获得深度推理能力。"
				),
			)
		# ── 检查模型是否存在于上游 ──
		_chk_url = (base_url or os.getenv("UPSTREAM_BASE_URL", UPSTREAM_BASE_URL)).rstrip("/")
		_chk_key = (api_key or os.getenv("DEEPSEEK_API_KEY") or
					os.getenv("OPENAI_API_KEY") or UPSTREAM_API_KEY)
		if _chk_key:
			try:
				_cli: httpx.AsyncClient = app.state.http_client
				_r = await _cli.get(
					f"{_chk_url}/v1/models",
					headers={"Authorization": f"Bearer {_chk_key}"},
					timeout=8.0,
				)
				if _r.status_code == 200:
					_ids = {m.get("id") for m in _r.json().get("data", [])}
					if _ids and default_model not in _ids:
						_avail = ", ".join(sorted(_ids - _INTERNAL_ONLY_MODELS))
						raise HTTPException(
							status_code=400,
							detail=f"模型 '{default_model}' 在上游不存在，可用模型：{_avail}",
						)
			except HTTPException:
				raise
			except Exception:
				pass  # 网络异常时跳过校验，不阻断保存
		lines = _set_key(lines, "GATEWAY_DEFAULT_MODEL", default_model)
		os.environ["GATEWAY_DEFAULT_MODEL"] = default_model
	if tool_ws in ("auto", "true", "false"):
		lines = _set_key(lines, "GATEWAY_TOOL_WEB_SEARCH", tool_ws)
		os.environ["GATEWAY_TOOL_WEB_SEARCH"] = tool_ws
	if tool_rag in ("true", "false"):
		rag_val = "true" if tool_rag == "true" else "false"
		lines = _set_key(lines, "GATEWAY_TOOL_RAG", rag_val)
		os.environ["GATEWAY_TOOL_RAG"] = rag_val

	with open(env_path, "w", encoding="utf-8") as f:
		f.writelines(lines)

	return JSONResponse(content={"status": "ok", "message": "配置已更新"})


@app.post("/v1/rag/add")
async def rag_add(payload: Dict[str, Any]) -> JSONResponse:
    """向 RAG 向量库中添加文档片段。

    Body 字段：
      text   (str, 必填) 文档内容
      title  (str, 可选) 标题
      source (str, 可选) 来源标识
    """
    if external_retrieve_context is None:
        raise HTTPException(status_code=501, detail="rag_adapter 未加载")
    try:
        from rag_adapter import add_document
        text = payload.get("text", "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text 字段不能为空")
        add_document(text, title=payload.get("title", ""), source=payload.get("source", ""))
        return JSONResponse(content={"status": "ok", "message": "文档已添加"})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v1/chat/completions")
async def chat_completions(payload: Dict[str, Any]) -> JSONResponse:
	"""OpenAI 兼容聊天接口（核心）：只做两件事

	1) 在必要时调用外部工具/RAG，并把结果作为 system 前缀注入到 `messages`。
	2) 将请求（可选流式）转发给上游模型，并把响应回给客户端。
	"""

	# ── 请求序号 + 时间标记 ──
	app.state.request_count += 1
	_req_no = app.state.request_count
	_req_ts = __import__('datetime').datetime.now().strftime('%H:%M:%S')
	logging.warning("\n" + "=" * 60)
	logging.warning("[REQUEST #%d  %s]", _req_no, _req_ts)
	logging.warning("=" * 60)

	# ── 原始请求日志（供调试 FC 结构）──
	def _summarize_payload(p: Dict[str, Any]) -> str:
		import copy, json
		s = copy.deepcopy(p)
		for m in s.get("messages", []):
			c = m.get("content")
			if isinstance(c, str) and len(c) > 200:
				m["content"] = c[:200] + f"…[+{len(c)-200}]"
			if m.get("role") == "tool":
				tc = m.get("content")
				if isinstance(tc, str) and len(tc) > 300:
					m["content"] = tc[:300] + f"…[+{len(tc)-300}]"
		return json.dumps(s, ensure_ascii=False, indent=2)
	logging.warning("[RAW_REQUEST]\n%s", _summarize_payload(payload))

	payload = prepare_payload(payload)

	# 支持流式与非流式两种模式
	want_stream = bool(payload.get("stream"))
	try:
		if want_stream:
			gen_factory = await forward_to_model_stream(payload)
			return StreamingResponse(gen_factory(), media_type="text/event-stream", headers={
				"Cache-Control": "no-cache",
				"Connection": "keep-alive",
				"X-Accel-Buffering": "no",
			})
		else:
			data = await forward_to_model(payload)
			return JSONResponse(content=data)
	except httpx.HTTPError as exc:
		raise HTTPException(status_code=502, detail=f"upstream error: {exc}")


@app.post("/v1/chat/stream")
async def chat_stream(payload: Dict[str, Any]) -> StreamingResponse:
	"""独立流式接口：无论请求是否传 stream，都强制以 SSE 流式下发。"""
	payload = prepare_payload(payload)
	payload["stream"] = True

	try:
		gen_factory = await forward_to_model_stream(payload)
		return StreamingResponse(gen_factory(), media_type="text/event-stream", headers={
			"Cache-Control": "no-cache",
			"Connection": "keep-alive",
			"X-Accel-Buffering": "no",
		})
	except httpx.HTTPError as exc:
		raise HTTPException(status_code=502, detail=f"upstream error: {exc}")


if __name__ == "__main__":
	import uvicorn

	uvicorn.run("APIAgent:app", host="0.0.0.0", port=8000, reload=True)
