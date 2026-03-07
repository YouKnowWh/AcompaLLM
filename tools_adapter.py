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

def _web_search(args: Dict[str, Any]) -> Any:
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


# ──────────────────────────────────────────────────────────────
# 工具注册表（在此添加新工具）
# ──────────────────────────────────────────────────────────────
TOOL_REGISTRY: Dict[str, Any] = {
    "web_search": _web_search,
    "calculator": _calculator,
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
