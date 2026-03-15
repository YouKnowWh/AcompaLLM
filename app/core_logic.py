"""
AcompaLLM Core Logic — 统一共享函数
"""

import json
import re
import datetime
from typing import Any, Dict, List, Optional

# ─── 触发关键词列表 ────────────────────────────────────────────────────────────
# 触发联网搜索的关键词
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

# 触发深度思考的关键词
_DEEP_THINK_TRIGGERS = [
    "深度思考", "深思", "仔细想", "认真想", "仔细分析", "深入分析",
    "用reasoner", "启用reasoner", "开启reasoner", "深度推理", "用r1", "启用r1",
    "think harder", "think deeply", "use reasoner", "deep think",
]

# DSML标记清除正则表达式
_DSML_RE = re.compile(r'‖DSML‖', re.DOTALL)

# ─── 判断函数 ──────────────────────────────────────────────────────────────────
def _needs_search(text: str) -> bool:
    """判断用户消息是否需要联网搜索。"""
    t = text.lower()
    return any(kw in t for kw in _SEARCH_TRIGGERS)


def _needs_deep_think(text: str) -> bool:
    """判断用户是否明确要求深度推理。"""
    t = text.lower()
    return any(kw in t for kw in _DEEP_THINK_TRIGGERS)


# ─── 格式化函数 ─────────────────────────────────────────────────────────────────
def _strip_dsml(text: str) -> str:
    """清除 DeepSeek 内部 DSML 标记。"""
    if '‖DSML‖' not in text:
        return text
    text = _DSML_RE.sub('', text)
    return '\n'.join(ln for ln in text.splitlines() if '‖DSML‖' not in ln).strip()


def _format_search_results(results: List[Dict]) -> str:
    """格式化搜索结果。"""
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


# ─── 辅助函数 ──────────────────────────────────────────────────────────────────
def _msg_text(content: Any) -> str:
    """统一提取消息内容为字符串，支持 str 和 Chatbox 的 array 格式。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content) if content else ""
