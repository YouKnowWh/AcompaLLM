"""
tools_adapter.py — 外部工具适配器
==================================
职责：提供 invoke_tool(tool_name, args) 统一入口，APIAgent 自动发现并调用。

内置工具：
  - web_search : 使用 DuckDuckGo 搜索网页（需 duckduckgo-search）
  - calculator  : 安全数学表达式求值

扩展方法：在 TOOL_REGISTRY 中注册新函数即可，无需修改 APIAgent.py。
"""

from __future__ import annotations

import ast
import operator
from typing import Any, Dict

# ──────────────────────────────────────────────────────────────
# 内置工具实现
# ──────────────────────────────────────────────────────────────

def _web_search_ddg(args: Dict[str, Any]) -> Any:
    """DuckDuckGo 文本搜索，返回前 N 条摘要。"""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return {"error": "ddgs 未安装，请运行: pip install ddgs"}

    query: str = args.get("query", "")
    max_results: int = int(args.get("max_results", 5))

    if not query:
        return {"error": "缺少参数 query"}

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
                "url": r.get("href", ""),
            })

    return results if results else {"message": "未找到结果"}


def _web_search_tavily(args: Dict[str, Any]) -> Any:
    """Tavily Search API，返回前 N 条结果。需配置 API Key。"""
    import httpx

    api_key: str = args.get("api_key", "").strip()
    if not api_key:
        return {"error": "Tavily API Key 未配置，请在 设置 > 工具 中填写"}

    query: str = args.get("query", "")
    max_results: int = int(args.get("max_results", 5))

    if not query:
        return {"error": "缺少参数 query"}

    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": max_results},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        results = [
            {
                "title":   r.get("title", ""),
                "snippet": r.get("content", ""),
                "url":     r.get("url", ""),
            }
            for r in data.get("results", [])
        ]
        return results if results else {"message": "未找到结果"}
    except Exception as exc:
        return {"error": f"Tavily 搜索失败: {exc}"}


def _web_search_bing(args: Dict[str, Any]) -> Any:
    """Bing Web Search API（Azure Cognitive Services）。"""
    import httpx
    api_key: str = args.get("api_key", "").strip()
    if not api_key:
        return {"error": "Bing API Key 未配置，请在 设置 > 工具 中填写"}
    query: str = args.get("query", "")
    max_results: int = int(args.get("max_results", 5))
    if not query:
        return {"error": "缺少参数 query"}
    try:
        resp = httpx.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": api_key},
            params={"q": query, "count": max_results, "mkt": "zh-CN"},
            timeout=15.0,
        )
        resp.raise_for_status()
        items = resp.json().get("webPages", {}).get("value", [])
        return [{"title": r.get("name", ""), "snippet": r.get("snippet", ""), "url": r.get("url", "")} for r in items] or {"message": "未找到结果"}
    except Exception as exc:
        return {"error": f"Bing 搜索失败: {exc}"}


def _web_search_brave(args: Dict[str, Any]) -> Any:
    """Brave Search API。"""
    import httpx
    api_key: str = args.get("api_key", "").strip()
    if not api_key:
        return {"error": "Brave Search API Key 未配置，请在 设置 > 工具 中填写"}
    query: str = args.get("query", "")
    max_results: int = int(args.get("max_results", 5))
    if not query:
        return {"error": "缺少参数 query"}
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            params={"q": query, "count": max_results},
            timeout=15.0,
        )
        resp.raise_for_status()
        items = resp.json().get("web", {}).get("results", [])
        return [{"title": r.get("title", ""), "snippet": r.get("description", ""), "url": r.get("url", "")} for r in items] or {"message": "未找到结果"}
    except Exception as exc:
        return {"error": f"Brave 搜索失败: {exc}"}


def _web_search_serp(args: Dict[str, Any]) -> Any:
    """SerpAPI（Google 搜索）。"""
    import httpx
    api_key: str = args.get("api_key", "").strip()
    if not api_key:
        return {"error": "SerpAPI Key 未配置，请在 设置 > 工具 中填写"}
    query: str = args.get("query", "")
    max_results: int = int(args.get("max_results", 5))
    if not query:
        return {"error": "缺少参数 query"}
    try:
        resp = httpx.get(
            "https://serpapi.com/search",
            params={"api_key": api_key, "q": query, "engine": "google", "num": max_results, "hl": "zh-cn"},
            timeout=15.0,
        )
        resp.raise_for_status()
        items = resp.json().get("organic_results", [])
        return [{"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("link", "")} for r in items] or {"message": "未找到结果"}
    except Exception as exc:
        return {"error": f"SerpAPI 搜索失败: {exc}"}


def _web_search(args: Dict[str, Any]) -> Any:
    """联网搜索统一入口，根据 engine 参数路由到具体实现。"""
    engine = args.get("engine", "ddg")
    if engine == "tavily":
        return _web_search_tavily(args)
    if engine == "bing":
        return _web_search_bing(args)
    if engine == "brave":
        return _web_search_brave(args)
    if engine == "serp":
        return _web_search_serp(args)
    return _web_search_ddg(args)


# 安全求值：仅允许四则运算和幂运算
_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


def _calculator(args: Dict[str, Any]) -> Any:
    """安全数学表达式求值，例如 {'expression': '(3+5)*2'}。"""
    expr: str = args.get("expression", "").strip()
    if not expr:
        return {"error": "缺少参数 expression"}
    try:
        tree = ast.parse(expr, mode="eval")
        result = _safe_eval(tree.body)
        return {"expression": expr, "result": result}
    except Exception as exc:
        return {"error": f"计算失败: {exc}"}


def _memory_search(args: Dict[str, Any]) -> Any:
    """记忆库检索工具，搜索历史对话记忆。
    
    Args:
        args: {"query": str, "person": str, "top_k": int}
    
    Returns:
        List[Dict] 记忆检索结果，或 {"error": str}
    """
    try:
        import memory as _mem_module
    except ImportError:
        return {"error": "记忆库模块未安装"}
    
    query = args.get("query", "").strip()
    person = args.get("person", "").strip()
    top_k = int(args.get("top_k", 5))
    
    if not query:
        return {"error": "缺少参数 query"}
    if not person:
        return {"error": "缺少参数 person（记忆人名称）"}
    
    try:
        # 简单的LLM调用函数（工具内部不依赖外部LLM）
        def _simple_llm(msgs):
            # 这里返回原始query，跳过改写（或由调用方提供真实LLM）
            return query
        
        hits = _mem_module.search(person, query, top_k=top_k, llm_call=_simple_llm)
        return hits if hits else []
    except Exception as exc:
        return {"error": f"记忆检索失败: {exc}"}


# ──────────────────────────────────────────────────────────────
# 工具注册表（在此添加新工具）
# ──────────────────────────────────────────────────────────────
TOOL_REGISTRY: Dict[str, Any] = {
    "web_search": _web_search,
    "calculator": _calculator,
    "memory_search": _memory_search,
}


# ──────────────────────────────────────────────────────────────
# 公开 API（APIAgent 调用此函数）
# ──────────────────────────────────────────────────────────────

def invoke_tool(tool_name: str, args: Dict[str, Any]) -> Any:
    """统一工具调用入口。

    Args:
        tool_name: 工具名称，对应 TOOL_REGISTRY 的键。
        args:      工具参数字典。

    Returns:
        工具执行结果，类型由具体工具决定。
        若工具不存在则返回错误描述。
    """
    handler = TOOL_REGISTRY.get(tool_name)
    if handler is None:
        available = ", ".join(TOOL_REGISTRY.keys())
        return {"error": f"未知工具 '{tool_name}'，可用工具: {available}"}
    return handler(args)
