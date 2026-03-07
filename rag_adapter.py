"""
rag_adapter.py — 向量检索适配器
================================
职责：
  1. 将文本片段编码为向量并存入 FAISS 索引（add_document）
  2. 根据查询检索最相关的片段（retrieve_context）

依赖：
  pip install sentence-transformers faiss-cpu numpy

环境变量（可在 .env 中配置）：
  EMBED_MODEL       嵌入模型名称，默认 BAAI/bge-small-zh-v1.5
  FAISS_INDEX_PATH  FAISS 索引文件路径，默认 ./data/faiss.index
  FAISS_META_PATH   元数据 JSON 文件路径，默认 ./data/faiss_meta.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

# ──────────────────────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────────────────────
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
INDEX_PATH: Path = Path(os.getenv("FAISS_INDEX_PATH", "./data/faiss.index"))
META_PATH: Path = Path(os.getenv("FAISS_META_PATH", "./data/faiss_meta.json"))

# ──────────────────────────────────────────────────────────────
# 懒加载全局对象（避免导入时就下载模型）
# ──────────────────────────────────────────────────────────────
_model = None
_index = None
_meta: List[Dict[str, Any]] = []


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        print(f"[RAG] 加载嵌入模型: {EMBED_MODEL}")
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def _get_index():
    """加载或新建 FAISS 索引，同步加载元数据。"""
    global _index, _meta
    if _index is not None:
        return _index

    import faiss

    if INDEX_PATH.exists() and META_PATH.exists():
        print(f"[RAG] 加载已有索引: {INDEX_PATH}")
        _index = faiss.read_index(str(INDEX_PATH))
        _meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    else:
        print("[RAG] 创建新索引")
        model = _get_model()
        dim = model.get_sentence_embedding_dimension()
        _index = faiss.IndexFlatL2(dim)
        _meta = []

    return _index


def _save():
    """将索引和元数据持久化到磁盘。"""
    import faiss

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(_index, str(INDEX_PATH))
    META_PATH.write_text(json.dumps(_meta, ensure_ascii=False, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────────

def add_document(text: str, title: str = "", source: str = "") -> None:
    """将一段文本向量化并存入 FAISS 索引。

    Args:
        text:   文本内容
        title:  可读标题（可选）
        source: 来源标识，如文件名或 URL（可选）
    """
    index = _get_index()
    model = _get_model()

    vec = model.encode([text], normalize_embeddings=True).astype("float32")
    index.add(vec)
    _meta.append({"title": title or source or "snippet", "body": text, "source": source})
    _save()
    print(f"[RAG] 已添加文档，当前共 {index.ntotal} 条")


def retrieve_context(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """检索与 query 最相关的 top_k 条片段。

    Returns:
        片段列表，每项包含 title / body / source / score 字段。
        若索引为空则返回空列表。
    """
    index = _get_index()
    if index.ntotal == 0:
        return []

    model = _get_model()
    q_vec = model.encode([query], normalize_embeddings=True).astype("float32")

    k = min(top_k, index.ntotal)
    distances, indices = index.search(q_vec, k)

    results: List[Dict[str, Any]] = []
    for dist, idx in zip(distances[0], indices[0]):
        if idx < 0:
            continue
        entry = dict(_meta[idx])
        entry["score"] = float(dist)
        results.append(entry)

    return results


def clear_index() -> None:
    """清空内存中的索引和元数据（不删除磁盘文件）。"""
    global _index, _meta
    _index = None
    _meta = []
    print("[RAG] 索引已清空（内存）")
