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


# ──────────────────────────────────────────────────────────────
# Level 3：Function Calling 自主检索
# ──────────────────────────────────────────────────────────────

_SEARCH_KB_TOOL_DESC = (
    "从指定知识库中语义检索与问题相关的内容片段。每次只查一个知识库。"
)

_SEARCH_AGENT_SYSTEM = """\
你是一个文档检索助手。你有 search_kb 工具，可以从绑定的知识库中查找内容。
原则：
- 尽量用最精准的查询词一次成功；每轮有延迟成本，严格控制轮数
- 第一轮检索后立即评估：命中内容已能回答用户问题时，立即停止，直接回复“检索完毕”
- 只在结果明显不完整或与问题方向不符时才进行追加检索
- 不同知识库只在问题明确涉及多个领域时才分别检索
当你认为已经收集到足够的信息，或无法找到更多相关内容时，直接回复"检索完毕"，不要作答用户的问题。"""


def _query_similar(q1: str, q2: str, threshold: float = 0.55) -> bool:
    """计算两个查询词的字符级 Jaccard 相似度，超过阈値则认为重复。"""
    s1 = set(q1.strip().lower())
    s2 = set(q2.strip().lower())
    if not s1 or not s2:
        return q1.strip() == q2.strip()
    return len(s1 & s2) / len(s1 | s2) >= threshold


def level3_search(
    user_text: str,
    tool_chat: Callable[[List[Dict], List[Dict]], Dict],
    kb_names: List[str],
    top_k: int = 5,
    max_iters: int = 5,
    char_budget: int = 32000,
    on_round: Optional[Callable[[Dict], None]] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Level 3 Agentic RAG：LLM 通过 Function Calling 自主决定检索策略。

    Args:
        user_text:  用户原始问题
        tool_chat:  Callable[[messages, tools], dict]，由调用方传入
                    （UpstreamClient.tool_chat 的业务闭包）
        kb_names:   当前对话绑定的知识库名称列表
        top_k:      每次 search_kb 返回的最大条数
        max_iters:  消息轮次上限（含所有工具调用，由调用方传入 MAX_KB_TOOL_ITERS）
        char_budget: 最终注入 context 的字符总量上限，按 distance 升序截断

    Returns:
        (hits, tool_events)
        hits:        去重 + 截断后的检索结果列表
        tool_events: 每次 search_kb 调用的元数据，供前端展示
                     格式：{"kb_name", "query", "hit_count", "chunks": ["道德经#3", ...]}
    """
    if not kb_names:
        return [], []

    tool_schema = _build_search_kb_schema(kb_names)
    messages: List[Dict] = [
        {"role": "system", "content": _SEARCH_AGENT_SYSTEM},
        {"role": "user",   "content": user_text},
    ]

    all_hits:    List[Dict[str, Any]] = []
    seen_bodies: set                  = set()
    tool_events: List[Dict[str, Any]] = []
    used_queries: List[str]           = []   # 记录已执行的查询词，防止重复

    for iteration in range(max_iters):
        result = tool_chat(messages, [tool_schema])

        if "error" in result:
            print(f"[Level3RAG] 第 {iteration + 1} 轮 tool_chat 失败: {result['error']}")
            break

        if "content" in result:
            # 模型主动停止调用工具，检索结束
            print(f"[Level3RAG] 模型完成检索，共 {iteration + 1} 轮")
            break

        tool_calls = result.get("tool_calls") or []
        if not tool_calls:
            break

        # 将模型的 assistant 消息追加到历史
        messages.append({
            "role":       "assistant",
            "content":    None,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            tc_id   = tc.get("id", "")
            fn      = tc.get("function", {})
            fn_name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except Exception:
                args = {}

            if fn_name != "search_kb":
                messages.append({"role": "tool", "tool_call_id": tc_id,
                                  "content": "未知工具名，请只使用 search_kb。"})
                continue

            kb_name = args.get("kb_name", "")
            query   = args.get("query", "").strip()

            if kb_name not in kb_names:
                messages.append({"role": "tool", "tool_call_id": tc_id,
                                  "content": f"知识库 '{kb_name}' 不在绑定列表中，可用: {kb_names}"})
                continue

            # 相似查询去重：阻止模型反复用相似词检索
            similar_prev = next(
                (uq for uq in used_queries if _query_similar(query, uq)), None
            )
            if similar_prev:
                messages.append({"role": "tool", "tool_call_id": tc_id,
                                  "content": (
                                      f"查询'{query}'与之前的查询'{similar_prev}'高度相似，"
                                      "已跳过重复检索。请换一个角度继续，或回复'检索完毕'。"
                                  )})
                continue
            used_queries.append(query)

            print(f"[Level3RAG] 第 {iteration + 1} 轮 → search_kb({kb_name!r}, {query[:60]!r})")
            new_hits = _do_search(query, [kb_name], top_k)

            unique_new: List[Dict[str, Any]] = []
            for h in new_hits:
                key = h["body"][:100]
                if key not in seen_bodies:
                    seen_bodies.add(key)
                    unique_new.append(h)
            all_hits.extend(unique_new)

            tool_events.append({
                "kb_name":   kb_name,
                "query":     query,
                "hit_count": len(unique_new),
                "chunks":    [f"{h['kb_name']}#{h['chunk_index']}" for h in unique_new],
                "hits":      [{"kb_name": h["kb_name"],
                               "source": h.get("source", ""),
                               "chunk_index": h["chunk_index"],
                               "body": h["body"]}
                              for h in unique_new],
            })
            # 实时回调，让调用方可在当前轮完成就立即展示结果
            if on_round is not None:
                on_round(tool_events[-1])

            # 给模型的简短摘要（不返回全文，节省 tool loop 内的 tokens）
            if unique_new:
                lines = [f"找到 {len(unique_new)} 条结果："]
                for h in unique_new[:3]:
                    lines.append(
                        f"  [{h['kb_name']} 第{h['chunk_index']}块] {h['body'][:80]}…"
                    )
                if len(unique_new) > 3:
                    lines.append(f"  …（另有 {len(unique_new) - 3} 条）")
                tool_result = "\n".join(lines)
            else:
                tool_result = "本次检索未找到相关内容。"

            messages.append({
                "role":         "tool",
                "tool_call_id": tc_id,
                "content":      tool_result,
            })

    # 按 distance 升序，按 char_budget 截断低分结果
    all_hits.sort(key=lambda x: x["distance"])
    kept: List[Dict[str, Any]] = []
    total_chars = 0
    for h in all_hits:
        ln = len(h["body"])
        if total_chars + ln > char_budget:
            break
        kept.append(h)
        total_chars += ln

    return kept, tool_events


def format_kb_hits_for_context(hits: List[Dict[str, Any]]) -> str:
    """
    将检索命中结果格式化为带信源标注的 C1 注入文本。
    每条明确标注知识库名称、块序号、文件来源，供 LLM 在回答时引用。
    """
    if not hits:
        return ""
    lines = [
        "[知识库检索结果]",
        "【重要】以下内容来自用户知识库，可信度最高。请优先依据知识库内容作答；知识库无相关内容时可参考联网检索；最后才依赖自身训练知识。不得凭空捏造、过度推演。",
        "---",
    ]
    for h in hits:
        kb   = h.get("kb_name", "未知知识库")
        cidx = h.get("chunk_index", "?")
        src  = h.get("source", "")
        body = h.get("body", "")
        src_note = f"，来自 {src}" if src else ""
        lines.append(f"来源：{kb}（第 {cidx} 块{src_note}）")
        lines.append(f"内容：{body}")
        lines.append("---")
    return "\n".join(lines)


def _build_search_kb_schema(kb_names: List[str]) -> Dict[str, Any]:
    """动态构造 search_kb 工具的 JSON Schema，kb_name 枚举由调用时的绑定列表决定。"""
    return {
        "type": "function",
        "function": {
            "name":        "search_kb",
            "description": _SEARCH_KB_TOOL_DESC,
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_name": {
                        "type":        "string",
                        "enum":        kb_names,
                        "description": "要查询的知识库名称，必须是绑定列表中的一个",
                    },
                    "query": {
                        "type":        "string",
                        "description": "用于向量检索的查询语句，应简洁精准",
                    },
                },
                "required":             ["kb_name", "query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }
