"""
rag_adapter.py — 向量检索适配器（桥接层）
==========================================
本文件已升级为对 kb/ 模块的薄桥接层。
  - 底层存储：ChromaDB（替代原 FAISS）
  - 记忆库片段统一存入名为"记忆库"的集合
  - 公开接口保持不变，client_core.py 无需修改

旧接口：
  add_document(text, title, source)   — 添加单条记忆片段
  retrieve_context(query, top_k)      — 全库语义检索
  clear_index()                       — 清空记忆（保留知识库集合）
"""

from __future__ import annotations

from typing import Any, Dict, List

# 记忆库专用集合名（与用户知识库集合隔离）
_MEMORY_COLLECTION = "记忆库"


# ──────────────────────────────────────────────────────────────
# 公开 API（接口签名与旧版完全一致）
# ──────────────────────────────────────────────────────────────

def add_document(text: str, title: str = "", source: str = "") -> None:
    """将一段文本存入记忆库集合。

    Args:
        text:   文本内容
        title:  可读标题（可选）
        source: 来源标识（可选）
    """
    from kb import store
    store.add_chunks(
        _MEMORY_COLLECTION,
        [text],
        source=source,
        extra_meta={"title": title or source or "snippet"},
    )


def retrieve_context(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """全库语义检索，返回与 query 最相关的 top_k 条片段。

    Returns:
        片段列表，每项包含 title / body / source / score 字段。
        （score 为 ChromaDB 余弦距离，越小越相关）
    """
    from kb import store
    hits = store.search_all(query, top_k=top_k)
    # 格式适配：distance → score，补充 title 字段
    results: List[Dict[str, Any]] = []
    for h in hits:
        results.append({
            "title": h.get("title") or h.get("source") or h.get("kb_name") or "片段",
            "body":  h.get("body", ""),
            "source": h.get("source", ""),
            "score": h.get("distance", 0.0),
        })
    return results


def clear_index() -> None:
    """清空记忆库集合（不影响用户知识库集合）。"""
    from kb import store
    store.delete_collection(_MEMORY_COLLECTION)
    print("[RAG] 记忆库已清空")
