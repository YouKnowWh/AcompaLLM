"""
kb/__init__.py — 知识库模块公开接口
=====================================
外部代码只需 import kb，无需关心内部模块结构。

主要接口：
  kb.ingest_file(path, name=None)          — 导入单个文件
  kb.ingest_folder(folder, recursive=True) — 批量导入文件夹
  kb.ingest_url(url, name)                 — 导入网页
  kb.search(query, names=None, top_k=5)    — 普通向量检索
  kb.agentic_search(query, llm_call, ...)  — Agentic RAG Level 2 检索
  kb.list_collections()                    — 列出所有知识库
  kb.delete_collection(name)               — 删除知识库
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from kb.chunker import chunk_text
from kb.parser import default_name, parse_file, parse_url
from kb import store as _store
from kb.agent import agentic_search as _agentic_search

# 支持的文件扩展名
_SUPPORTED_EXT = {".txt", ".md", ".pdf", ".docx"}


# ──────────────────────────────────────────────────────────────
# 数据摄入
# ──────────────────────────────────────────────────────────────

def ingest_file(
    path: Union[str, Path],
    name: Optional[str] = None,
    embed_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    解析并导入单个文件到知识库。

    Args:
        path: 文件路径
        name: 知识库名称；不传则使用文件名去后缀
        embed_model: 嵌入模型名；不传则使用集合已存储的模型或默认模型

    Returns:
        {"name": str, "chunks": int, "source": str, "added_at": str}
    """
    import datetime
    p = Path(path)
    kb_name = name or default_name(p)
    print(f"[KB] 导入文件: {p.name} → 集合: {kb_name}")

    text = parse_file(p)
    chunks = chunk_text(text)
    count = _store.add_chunks(kb_name, chunks, source=str(p), embed_model=embed_model or None)
    return {"name": kb_name, "chunks": count, "source": str(p),
            "added_at": datetime.date.today().isoformat()}


def ingest_folder(
    folder: Union[str, Path],
    name: Optional[str] = None,
    recursive: bool = True,
    embed_model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    批量导入文件夹内所有支持格式的文件。

    Args:
        folder:    文件夹路径
        name:      统一集合名；不传则每个文件用自己的文件名（各自独立集合）
        recursive: 是否递归子文件夹
        embed_model: 嵌入模型

    Returns:
        每个文件的导入结果列表
    """
    folder = Path(folder)
    pattern = "**/*" if recursive else "*"
    files = [f for f in folder.glob(pattern) if f.suffix.lower() in _SUPPORTED_EXT and f.is_file()]

    if not files:
        print(f"[KB] 文件夹 {folder} 中未找到支持的文件")
        return []

    results = []
    for f in files:
        try:
            r = ingest_file(f, name=name, embed_model=embed_model)
            results.append(r)
        except Exception as e:
            print(f"[KB] 跳过 {f.name}: {e}")
            results.append({"name": name or default_name(f), "chunks": 0, "source": str(f), "error": str(e)})

    return results


def ingest_url(url: str, name: str, embed_model: Optional[str] = None) -> Dict[str, Any]:
    """
    抓取网页并导入知识库。

    Args:
        url:  网页 URL
        name: 知识库名称（必填）
        embed_model: 嵌入模型

    Returns:
        {"name": str, "chunks": int, "source": str, "added_at": str}
    """
    import datetime
    print(f"[KB] 导入 URL: {url} → 集合: {name}")
    text = parse_url(url)
    chunks = chunk_text(text)
    count = _store.add_chunks(name, chunks, source=url, embed_model=embed_model or None)
    return {"name": name, "chunks": count, "source": url,
            "added_at": datetime.date.today().isoformat()}


# ──────────────────────────────────────────────────────────────
# 检索
# ──────────────────────────────────────────────────────────────

def search(
    query: str,
    names: Optional[List[str]] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    普通语义检索（不含 Agentic 改写/反思）。

    Args:
        query: 检索查询
        names: 指定集合列表；None 表示全库检索
        top_k: 返回条数

    Returns:
        结果列表，每项含 body / source / kb_name / distance
    """
    if names:
        hits: List[Dict[str, Any]] = []
        for n in names:
            hits.extend(_store.search(n, query, top_k=top_k))
        hits.sort(key=lambda x: x["distance"])
        return hits[:top_k]
    return _store.search_all(query, top_k=top_k)


def agentic_search(
    query: str,
    llm_call: Callable[[List[Dict[str, str]]], str],
    names: Optional[List[str]] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Agentic RAG Level 2 检索（查询改写 + 反思迭代）。

    Args:
        query:    用户原始问题
        llm_call: LLM 调用函数，接受 messages 列表，返回字符串
        names:    指定集合；None 为全库
        top_k:    每轮检索返回条数

    Returns:
        去重合并后的检索结果列表
    """
    return _agentic_search(query, llm_call, collection_names=names, top_k=top_k)


# ──────────────────────────────────────────────────────────────
# 管理接口
# ──────────────────────────────────────────────────────────────

def list_collections() -> List[Dict[str, Any]]:
    """列出所有知识库，返回 display_name / count 等信息。"""
    return _store.list_collections()


def delete_collection(name: str) -> bool:
    """删除指定知识库集合，返回是否成功。"""
    return _store.delete_collection(name)


def collection_exists(name: str) -> bool:
    """检查指定名称的知识库是否存在。"""
    return _store.collection_exists(name)


def list_sources(name: str) -> List[Dict[str, Any]]:
    """列出知识库内所有来源文件摘要（source / name / added_at / count）。"""
    return _store.list_sources(name)


def delete_source(name: str, source: str) -> int:
    """删除知识库内指定来源的全部块，返回删除数量。"""
    return _store.delete_source(name, source)

