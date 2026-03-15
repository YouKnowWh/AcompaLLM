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
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings

# ChromaDB 持久化目录
_CHROMA_DIR = Path(os.getenv("CHROMA_DIR", "./data/chroma"))

# 嵌入模型名（可通过环境变量覆盖）
_EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
_ONNX_CACHE_DIR = Path(os.getenv("ONNX_CACHE_DIR", "./data/onnx_cache"))

# ──────────────────────────────────────────────────────────────
# 懒加载：ChromaDB 客户端 & 嵌入函数
# ──────────────────────────────────────────────────────────────
_client: Optional[chromadb.PersistentClient] = None
_embed_fns: Dict[str, Any] = {}  # 按模型名缓存，支持多模型
_legacy_embed_fns: Dict[str, Any] = {}  # 老集合兼容：sentence_transformer
_onnx_lock = threading.Lock()


class _SafeSentenceTransformerEF:
    """CPU 安全兜底嵌入函数：规避部分环境下 meta tensor 迁移异常。"""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name
        self._model = None
        last_exc: Optional[Exception] = None

        # 优先显式关闭 low_cpu_mem/device_map 自动分配
        try:
            self._model = SentenceTransformer(
                model_name,
                device="cpu",
                model_kwargs={"low_cpu_mem_usage": False, "device_map": None},
            )
        except TypeError:
            # 兼容旧版 sentence-transformers（不支持 model_kwargs）
            self._model = SentenceTransformer(model_name, device="cpu")
        except Exception as exc:
            last_exc = exc

        if self._model is None:
            if last_exc:
                raise last_exc
            raise RuntimeError(f"加载嵌入模型失败: {model_name}")

    @staticmethod
    def name() -> str:
        return "safe_sentence_transformer"

    def get_config(self) -> Dict[str, Any]:
        return {"model_name": self._model_name}

    @staticmethod
    def build_from_config(config: Dict[str, Any]):
        model_from_cfg = (config or {}).get("model_name") or _EMBED_MODEL
        return _SafeSentenceTransformerEF(model_from_cfg)

    def embed_query(self, input):  # noqa: A002
        return self.__call__(input)

    def __call__(self, input):  # noqa: A002
        import numpy as np

        texts = [input] if isinstance(input, str) else list(input)
        if not texts:
            return []
        vecs = self._model.encode(
            texts,
            batch_size=64,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vecs).tolist()


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(_CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _onnx_cache_path(model_name: str) -> Path:
    """将 HF 模型名映射为本地 ONNX 缓存目录。"""
    safe = model_name.replace("/", "--")
    return _ONNX_CACHE_DIR / safe


def _try_onnx_embed_fn(model_name: str):
    """
    优先使用 ONNX Runtime 嵌入。
    - 首次：从 HF 导出并保存到 data/onnx_cache/<model>
    - 后续：直接从本地缓存加载，避免重复导出
    失败时返回 None，由调用方回退 PyTorch。
    """
    try:
        import numpy as np
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer

        cache_dir = _onnx_cache_path(model_name)
        cache_dir.mkdir(parents=True, exist_ok=True)
        onnx_file = cache_dir / "model.onnx"

        # 多线程并发保护：避免同一模型被重复导出
        with _onnx_lock:
            if onnx_file.exists():
                print(f"[KB] 加载嵌入模型 (ONNX 本地): {cache_dir}")
                tokenizer = AutoTokenizer.from_pretrained(str(cache_dir), local_files_only=True)
                ort_model = ORTModelForFeatureExtraction.from_pretrained(str(cache_dir), local_files_only=True)
            else:
                print(f"[KB] 首次导出 ONNX 模型: {model_name} -> {cache_dir}")
                tokenizer = AutoTokenizer.from_pretrained(model_name)
                ort_model = ORTModelForFeatureExtraction.from_pretrained(model_name, export=True)
                ort_model.save_pretrained(str(cache_dir))
                tokenizer.save_pretrained(str(cache_dir))
                print(f"[KB] ONNX 导出完成并已缓存: {cache_dir}")

        class _OnnxEF:
            """ChromaDB 兼容 embedding function。"""

            def __init__(self, tok, mdl, model_name_for_cfg: str):
                self._tok = tok
                self._mdl = mdl
                self._model_name = model_name_for_cfg

            @staticmethod
            def name() -> str:
                return "onnx_feature_extraction"

            def get_config(self) -> Dict[str, Any]:
                return {"model_name": self._model_name}

            @staticmethod
            def build_from_config(config: Dict[str, Any]):
                model_from_cfg = (config or {}).get("model_name") or _EMBED_MODEL
                ef = _try_onnx_embed_fn(model_from_cfg)
                if ef is None:
                    raise ValueError(f"无法根据配置构建 ONNX EmbeddingFunction: {model_from_cfg}")
                return ef

            def embed_query(self, input):  # noqa: A002
                # Chroma 在 query 路径会优先调用 embed_query
                return self.__call__(input)

            def __call__(self, input):  # noqa: A002
                texts = [input] if isinstance(input, str) else list(input)
                if not texts:
                    return []

                out: List[List[float]] = []
                batch_size = 64
                for i in range(0, len(texts), batch_size):
                    batch = texts[i: i + batch_size]
                    enc = self._tok(
                        batch,
                        padding=True,
                        truncation=True,
                        max_length=512,
                        return_tensors="np",
                    )
                    res = self._mdl(**enc)

                    h = res.last_hidden_state
                    cls = h[:, 0, :]  # BGE 推荐 CLS 池化
                    vecs = np.asarray(cls)

                    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    vecs = vecs / norms
                    out.extend(vecs.tolist())
                return out

        print(f"[KB] ONNX 嵌入模型加载完成: {model_name}")
        return _OnnxEF(tokenizer, ort_model, model_name)
    except Exception as exc:
        print(f"[KB] ONNX 加载失败，回退 PyTorch: {exc}")
        return None


def _get_embed_fn(model: str = None):
    """返回 ChromaDB 兼容的嵌入函数，按模型名缓存。优先 ONNX，回退 PyTorch。"""
    global _embed_fns
    m = model or _EMBED_MODEL
    if m not in _embed_fns:
        # 优先尝试 ONNX backend（需要 optimum[onnxruntime]）
        ef = _try_onnx_embed_fn(m)
        if ef is None:
            # 回退：经典 PyTorch（chromadb 内置封装）
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            print(f"[KB] 加载嵌入模型 (PyTorch): {m}")
            try:
                ef = SentenceTransformerEmbeddingFunction(
                    model_name=m,
                    normalize_embeddings=True,
                )
            except Exception as exc:
                msg = str(exc)
                if "meta tensor" in msg.lower() or "to_empty" in msg.lower():
                    print(f"[KB] PyTorch 嵌入加载触发 meta tensor 异常，切换 CPU 安全兜底: {exc}")
                    ef = _SafeSentenceTransformerEF(m)
                else:
                    raise
        _embed_fns[m] = ef
    return _embed_fns[m]


def _get_legacy_embed_fn(model: str = None):
    """返回 Chroma 内置 sentence_transformer 嵌入函数（用于旧集合兼容）。"""
    global _legacy_embed_fns
    m = model or _EMBED_MODEL
    if m not in _legacy_embed_fns:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        print(f"[KB] 加载嵌入模型 (Legacy sentence_transformer): {m}")
        try:
            _legacy_embed_fns[m] = SentenceTransformerEmbeddingFunction(
                model_name=m,
                normalize_embeddings=True,
            )
        except Exception as exc:
            msg = str(exc)
            if "meta tensor" in msg.lower() or "to_empty" in msg.lower():
                print(f"[KB] Legacy 嵌入加载触发 meta tensor 异常，切换 CPU 安全兜底: {exc}")
                _legacy_embed_fns[m] = _SafeSentenceTransformerEF(m)
            else:
                raise
    return _legacy_embed_fns[m]


def _is_embedding_conflict(exc: Exception) -> bool:
    msg = str(exc)
    return "Embedding function conflict" in msg or "embedding function already exists" in msg


def warmup(model: str = None) -> None:
    """预热嵌入模型，使首次导入无感延迟。应在后台线程调用。"""
    try:
        _get_embed_fn(model)
        print(f"[KB] 嵌入模型预热完成: {model or _EMBED_MODEL}")
    except Exception as exc:
        print(f"[KB] 嵌入模型预热失败: {exc}")


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
    col_exists = True
    try:
        existing = client.get_collection(col_name)
        model = (existing.metadata or {}).get("embed_model") or embed_model or _EMBED_MODEL
    except Exception:
        col_exists = False
        model = embed_model or _EMBED_MODEL

    ef = _get_embed_fn(model)
    try:
        return client.get_or_create_collection(
            name=col_name,
            embedding_function=ef,
            metadata={"display_name": display_name, "embed_model": model},
        )
    except Exception as exc:
        # 旧集合绑定了 sentence_transformer 时，自动回退 legacy EF
        if col_exists and _is_embedding_conflict(exc):
            print(f"[KB] 发现旧集合 embedding 配置，自动回退 legacy: {display_name}")
            return client.get_collection(
                col_name,
                embedding_function=_get_legacy_embed_fn(model),
            )
        raise


# ──────────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────────

def add_chunks(
    display_name: str,
    chunks: List[str],
    source: str = "",
    extra_meta: Optional[Dict[str, Any]] = None,
    embed_model: str = None,
    on_progress=None,  # Optional[Callable[[int, int], None]]
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
        done = min(start + batch, len(chunks))
        print(f"[KB] {display_name}: 已写入 {done}/{len(chunks)} 块")
        if on_progress:
            on_progress(done, len(chunks))

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
        try:
            col = client.get_collection(col_name, embedding_function=_get_embed_fn(embed_model))
        except Exception as exc:
            if _is_embedding_conflict(exc):
                print(f"[KB] 检索命中旧集合 embedding 配置，回退 legacy: {display_name}")
                col = client.get_collection(col_name, embedding_function=_get_legacy_embed_fn(embed_model))
            else:
                raise
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


def peek_chunks(display_name: str, source: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    获取指定来源的前 limit 个分块内容（用于预览），按 chunk_index 排序。
    返回 [{body, chunk_index}] 列表。
    """
    client = _get_client()
    col_name = _col_id(display_name)
    try:
        col = client.get_collection(col_name)
    except Exception:
        return []

    result = col.get(
        where={"source": source},
        include=["documents", "metadatas"],
    )
    docs  = result.get("documents") or []
    metas = result.get("metadatas") or []
    items = []
    for doc, meta in zip(docs, metas):
        items.append({
            "body": doc,
            "chunk_index": meta.get("chunk_index", 0),
        })
    items.sort(key=lambda x: x["chunk_index"])
    return items[:limit]


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

