"""
kb/agent.py — Agentic RAG Level 2
====================================
流程：
  1. 查询改写  — 调用 LLM 将用户原始问题扩展为更利于检索的词条
  2. 首次检索  — ChromaDB 语义检索（可指定集合或跨全库）
  3. 反思评估  — LLM 判断检索结果是否足够回答问题
  4. 二次检索  — 若不足，用不同角度重写 Query 再检索（最多 2 轮）
  5. 返回最终上下文片段列表

对外入口：
  agentic_search(query, llm_call, collection_names=None, top_k=5)
    llm_call: Callable[[List[dict]], str]  — 调用方传入的 LLM 调用函数
              接受 OpenAI messages 格式，返回字符串
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from kb import store

# 最大迭代轮次（首次检索 + 最多 MAX_ITER-1 次二次检索）
MAX_ITER = 2

# ──────────────────────────────────────────────────────────────
# Prompt 模板
# ──────────────────────────────────────────────────────────────

_REWRITE_PROMPT = """\
你是一个检索优化助手。请将下面的用户问题改写为 2-4 个更有利于语义检索的关键短句，\
用换行分隔，不要解释，不要编号，只输出短句本身。

用户问题：{query}"""

_REFLECT_PROMPT = """\
你是一个检索质量评估助手。任务：判断以下检索结果是否足以回答用户的问题。

用户问题：
{query}

检索到的文段（共 {n} 条）：
{snippets}

请只回答 JSON，格式如下（不要加任何解释）：
{{"sufficient": true/false, "reason": "一句话说明", "next_query": "若不足，建议用什么新关键词再次检索（sufficient=true 时留空）"}}"""


# ──────────────────────────────────────────────────────────────
# 主函数
# ──────────────────────────────────────────────────────────────

def agentic_search(
    query: str,
    llm_call: Callable[[List[Dict[str, str]]], str],
    collection_names: Optional[List[str]] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Agentic RAG Level 2 检索。

    Args:
        query:            用户原始问题
        llm_call:         调用 LLM 的函数，接受 messages 列表，返回字符串
        collection_names: 指定检索哪些集合名；None 表示跨全库检索
        top_k:            每轮检索返回的最大条数

    Returns:
        去重合并后的检索结果列表（body / source / kb_name / distance）
    """
    all_hits: List[Dict[str, Any]] = []
    seen_bodies: set = set()

    # ── Step 1: 查询改写 ──────────────────────────────────────
    current_query = _rewrite_query(query, llm_call)

    for iteration in range(MAX_ITER):
        print(f"[AgenticRAG] 第 {iteration+1} 轮检索，Query: {current_query[:80]}")

        # ── Step 2: 检索 ──────────────────────────────────────
        hits = _do_search(current_query, collection_names, top_k)

        # 去重合并
        new_hits = []
        for h in hits:
            key = h["body"][:100]
            if key not in seen_bodies:
                seen_bodies.add(key)
                new_hits.append(h)
        all_hits.extend(new_hits)

        if not all_hits:
            break

        # ── Step 3: 反思评估（最后一轮不再评估）────────────────
        if iteration < MAX_ITER - 1:
            sufficient, next_query = _reflect(query, all_hits, llm_call)
            if sufficient or not next_query:
                print(f"[AgenticRAG] 评估通过，结束迭代")
                break
            print(f"[AgenticRAG] 评估不足，下一轮 Query: {next_query[:80]}")
            current_query = next_query

    return all_hits


# ──────────────────────────────────────────────────────────────
# 内部步骤
# ──────────────────────────────────────────────────────────────

def _rewrite_query(query: str, llm_call: Callable) -> str:
    """将原始问题改写为检索友好的短句（取第一行作为主检索词）。"""
    try:
        prompt = _REWRITE_PROMPT.format(query=query)
        result = llm_call([{"role": "user", "content": prompt}])
        lines = [ln.strip() for ln in result.strip().splitlines() if ln.strip()]
        # 返回所有短句拼接，ChromaDB 对多短句联合向量效果更好
        return " ".join(lines) if lines else query
    except Exception as e:
        print(f"[AgenticRAG] 查询改写失败，使用原始 Query: {e}")
        return query


def _do_search(
    query: str,
    collection_names: Optional[List[str]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """执行实际的向量检索。"""
    if collection_names:
        hits: List[Dict[str, Any]] = []
        for name in collection_names:
            hits.extend(store.search(name, query, top_k=top_k))
        hits.sort(key=lambda x: x["distance"])
        return hits[:top_k]
    else:
        return store.search_all(query, top_k=top_k)


def _reflect(
    original_query: str,
    hits: List[Dict[str, Any]],
    llm_call: Callable,
) -> tuple[bool, str]:
    """
    反思评估检索结果是否充分。

    Returns:
        (sufficient: bool, next_query: str)
    """
    snippets_text = "\n---\n".join(
        f"[{i+1}] {h['body'][:300]}" for i, h in enumerate(hits[:5])
    )
    prompt = _REFLECT_PROMPT.format(
        query=original_query,
        n=len(hits),
        snippets=snippets_text,
    )
    try:
        result = llm_call([{"role": "user", "content": prompt}])
        # 提取 JSON（防止 LLM 在 JSON 外加了解释文字）
        json_str = _extract_json(result)
        data = json.loads(json_str)
        sufficient = bool(data.get("sufficient", True))
        next_query = str(data.get("next_query", "")).strip()
        return sufficient, next_query
    except Exception as e:
        print(f"[AgenticRAG] 反思解析失败，默认视为充分: {e}")
        return True, ""


def _extract_json(text: str) -> str:
    """从 LLM 输出中提取第一个 JSON 对象。"""
    import re
    m = re.search(r'\{.*\}', text, re.DOTALL)
    return m.group(0) if m else text
