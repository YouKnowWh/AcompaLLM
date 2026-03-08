"""
kb/store.py — ChromaDB 存储层
================================
职责：
  - 管理 ChromaDB 持久化客户端（./data/chroma/）
  - 每个"知识库"对应一个独立 Collection
  - Collection 内部 ID 用文件名 hash，展示名存在元数据中
  - 提供 add / search / delete_collection / list_collections 操作

Collection 命名规则：
  - 用户可读名（如"道德经"）存储在 Collection 的 metadata["display_name"]
  - ChromaDB 内部 collection name 使用 "kb_" + sha1(display_name)[:12]
    （ChromaDB 仅允许 [a-zA-Z0-9_-]，长度 3-63）
"""

from __future__ import annotations

import datetime
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings

# ChromaDB 持久化目录
_CHROMA_DIR = Path(os.getenv("CHROMA_DIR", "./data/chroma"))

# 嵌入模型名（可通过环境变量覆盖）
_EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")

# ──────────────────────────────────────────────────────────────
# 懒加载：ChromaDB 客户端 & 嵌入函数
# ──────────────────────────────────────────────────────────────
_client: Optional[chromadb.PersistentClient] = None
_embed_fns: Dict[str, Any] = {}  # 按模型名缓存，支持多模型


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(_CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _get_embed_fn(model: str = None):
    """返回 ChromaDB 兼容的嵌入函数（sentence-transformers），按模型名缓存。"""
    global _embed_fns
    m = model or _EMBED_MODEL
    if m not in _embed_fns:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        print(f"[KB] 加载嵌入模型: {m}")
        _embed_fns[m] = SentenceTransformerEmbeddingFunction(
            model_name=m,
            normalize_embeddings=True,
        )
    return _embed_fns[m]


# ──────────────────────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────────────────────

def _col_id(display_name: str) -> str:
    """将用户可读名转换为合法的 ChromaDB collection name。"""
    h = hashlib.sha1(display_name.encode("utf-8")).hexdigest()[:12]
    return f"kb_{h}"


def _get_or_create_collection(display_name: str, embed_model: str = None) -> chromadb.Collection:
    client = _get_client()
    col_name = _col_id(display_name)
    # 若集合已存在，优先沿用其原始 embed_model（保证向量空间一致）
    try:
        existing = client.get_collection(col_name)
        model = (existing.metadata or {}).get("embed_model") or embed_model or _EMBED_MODEL
    except Exception:
        model = embed_model or _EMBED_MODEL
    return client.get_or_create_collection(
        name=col_name,
        embedding_function=_get_embed_fn(model),
        metadata={"display_name": display_name, "embed_model": model},
    )


# ──────────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────────

def add_chunks(
    display_name: str,
    chunks: List[str],
    source: str = "",
    extra_meta: Optional[Dict[str, Any]] = None,
    embed_model: str = None,
) -> int:
    """
    将文本块列表写入指定知识库集合。

    Args:
        display_name: 用户可读知识库名（如"道德经"）
        chunks:       已分好的文本块列表
        source:       来源标识（文件路径或 URL）
        extra_meta:   附加元数据，会合并到每个块的 metadata

    Returns:
        成功写入的块数量
    """
    if not chunks:
        return 0

    col = _get_or_create_collection(display_name, embed_model)
    base_meta = {"source": source, "kb_name": display_name,
                 "added_at": datetime.date.today().isoformat()}
    if extra_meta:
        base_meta.update(extra_meta)

    # 为本次导入生成一组 ID（source hash + 块序号）
    src_hash = hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
    ids = [f"{src_hash}_{i:05d}" for i in range(len(chunks))]
    metas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]

    # ChromaDB add 支持批量，但大集合下分批防止内存峰值
    batch = 500
    for start in range(0, len(chunks), batch):
        col.add(
            documents=chunks[start: start + batch],
            ids=ids[start: start + batch],
            metadatas=metas[start: start + batch],
        )
        print(f"[KB] {display_name}: 已写入 {min(start+batch, len(chunks))}/{len(chunks)} 块")

    return len(chunks)


def search(
    display_name: str,
    query: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    在指定集合中语义检索。

    Returns:
        列表，每项包含 body / source / chunk_index / distance 字段。
        集合不存在或为空时返回空列表。
    """
    client = _get_client()
    col_name = _col_id(display_name)

    try:
        col_info = client.get_collection(col_name)
        embed_model = (col_info.metadata or {}).get("embed_model", _EMBED_MODEL)
        col = client.get_collection(col_name, embedding_function=_get_embed_fn(embed_model))
    except Exception:
        return []

    if col.count() == 0:
        return []

    k = min(top_k, col.count())
    results = col.query(query_texts=[query], n_results=k)

    hits: List[Dict[str, Any]] = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metas, dists):
        hits.append({
            "body": doc,
            "source": meta.get("source", ""),
            "chunk_index": meta.get("chunk_index", -1),
            "kb_name": meta.get("kb_name", display_name),
            "distance": round(float(dist), 4),
        })

    return hits


def search_all(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    跨所有集合检索，结果按 distance 排序后返回 top_k 条。
    """
    collections = list_collections()
    all_hits: List[Dict[str, Any]] = []
    for col_info in collections:
        hits = search(col_info["display_name"], query, top_k=top_k)
        all_hits.extend(hits)
    all_hits.sort(key=lambda x: x["distance"])
    return all_hits[:top_k]


def delete_collection(display_name: str) -> bool:
    """删除指定集合，返回是否成功。"""
    client = _get_client()
    col_name = _col_id(display_name)
    try:
        client.delete_collection(col_name)
        return True
    except Exception:
        return False


def list_collections() -> List[Dict[str, Any]]:
    """
    列出所有知识库集合信息。

    Returns:
        列表，每项包含 display_name / col_id / count 字段。
    """
    client = _get_client()
    result = []
    for col in client.list_collections():
        meta = col.metadata or {}
        display = meta.get("display_name", col.name)
        embed_model = meta.get("embed_model", _EMBED_MODEL)
        result.append({
            "display_name": display,
            "col_id": col.name,
            "count": col.count(),
            "embed_model": embed_model,
        })
    return result


def collection_exists(display_name: str) -> bool:
    """检查指定名称的集合是否存在。"""
    client = _get_client()
    col_name = _col_id(display_name)
    try:
        client.get_collection(col_name)
        return True
    except Exception:
        return False


def list_sources(display_name: str) -> List[Dict[str, Any]]:
    """
    列出集合内所有来源文件的摘要信息（来源路径、显示名、添加日期、块数）。
    """
    client = _get_client()
    col_name = _col_id(display_name)
    try:
        col = client.get_collection(col_name)  # 元数据查询，无需嵌入函数
    except Exception:
        return []

    if col.count() == 0:
        return []

    result = col.get(include=["metadatas"])
    sources: Dict[str, Dict[str, Any]] = {}
    for meta in (result.get("metadatas") or []):
        src = meta.get("source", "")
        if src not in sources:
            # 显示名：URL 则保留完整，本地路径则取文件名
            disp = src if ("://" in src) else Path(src).name
            sources[src] = {
                "source": src,
                "name": disp,
                "added_at": meta.get("added_at", ""),
                "count": 0,
            }
        sources[src]["count"] += 1

    return list(sources.values())


def delete_source(display_name: str, source: str) -> int:
    """
    删除集合内指定来源的全部块。返回删除的块数量。
    """
    client = _get_client()
    col_name = _col_id(display_name)
    try:
        col = client.get_collection(col_name)  # 删除操作无需嵌入函数
    except Exception:
        return 0

    result = col.get(where={"source": source}, include=["metadatas"])
    ids = result.get("ids") or []
    if ids:
        col.delete(ids=ids)
    return len(ids)

