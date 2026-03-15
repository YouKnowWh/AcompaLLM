"""memory/__init__.py — 对话记忆向量库 v2
==========================================
改进（v2）：
  - 合并存储：每轮 user+AI 合为一个文档，附带 "[用户]…\n[AI]…" 标记
  - 同话题块合并：连续 ≤ _MAX_BLOCK_ROUNDS 轮同话题自动合并为一个大块
  - 异步话题提取：LLM 后台提取话题标签，不阻塞主流程（fallback = "通用"）
  - 搜索：可选单次 LLM query 改写 + 向量检索，不再迭代
  - 自动迁移：warmup() 检测 v1 碎片格式并后台迁移为 v2

公开 API:
  list_persons()                                          → List[Dict]
  create_person(name: str)                                → bool
  delete_person(name: str)                                → bool
  add_round(person, user_text, ai_text, *,
            conv_id="", round_id="", llm_call=None)       → bool
    delete_round_entries(person, *, conv_id="", user_msg_id="",
                                            ai_msg_id="", user_text="", ai_text="") → int
  search(person, query, top_k=5, llm_call=None)           → List[Dict]
  migrate_v1(person)                                      → int
  migrate_all_v1()                                        → Dict[str, int]
  warmup()                                                → None
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

_BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MEMORY_DIR = os.path.join(_BASE_DIR, "data", "memory")
os.makedirs(_MEMORY_DIR, exist_ok=True)

_EMBED_MODEL       = "BAAI/bge-small-zh-v1.5"
_COL_PREFIX        = "mem_"
_MAX_BLOCK_ROUNDS  = 3     # 同话题最多合并轮次
_USER_LIMIT        = 400   # 每轮用户文本截断字符
_AI_LIMIT          = 800   # 每轮 AI 文本截断字符
_STATE_FILE        = os.path.join(_MEMORY_DIR, "_session_state.json")

# ── 懒加载 ChromaDB 客户端 / 嵌入函数 ────────────────────────────────────────
_client: Optional[Any] = None


def _get_client():
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.PersistentClient(path=_MEMORY_DIR)
    return _client


def _get_ef():
    """复用 kb/store 的嵌入函数缓存，避免重复加载同一模型。"""
    from kb.store import _get_embed_fn
    return _get_embed_fn(_EMBED_MODEL)


# ── 集合操作 ──────────────────────────────────────────────────────────────────

def _col_name(person: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", person.strip())
    col  = (_COL_PREFIX + safe)[:63]
    while len(col) < 3:
        col += "_"
    return col


def _get_collection(person: str):
    cli = _get_client()
    name = _col_name(person)
    ef = _get_ef()

    # 先尝试按当前 embedding function 获取/创建
    try:
        return cli.get_or_create_collection(name, embedding_function=ef)
    except Exception as exc:
        msg = str(exc)
        if "Embedding function conflict" not in msg and "embedding function already exists" not in msg:
            raise

    # 旧集合兼容：回退到 sentence_transformer EF
    from kb.store import _get_legacy_embed_fn
    print(f"[Memory] 检测到旧集合 embedding 配置，回退 legacy: {person}")
    return cli.get_collection(name, embedding_function=_get_legacy_embed_fn(_EMBED_MODEL))


# ── 会话状态（跨轮块合并用）────────────────────────────────────────────────────
# 结构: { person: {last_block_id, last_topic, last_block_content, last_round_count} }
_session_state: Dict[str, Any] = {}
_state_lock  = threading.Lock()
_person_locks: Dict[str, threading.Lock] = {}
_person_locks_mu = threading.Lock()


def _get_person_lock(person: str) -> threading.Lock:
    with _person_locks_mu:
        if person not in _person_locks:
            _person_locks[person] = threading.Lock()
        return _person_locks[person]


def _load_state() -> None:
    global _session_state
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            _session_state = json.load(f)
    except Exception:
        _session_state = {}


def _save_state() -> None:
    try:
        with _state_lock:
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(_session_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Memory] 状态保存失败: {e}")


# 模块导入时读取状态
_load_state()

# ── LLM 辅助 ─────────────────────────────────────────────────────────────────

_TOPIC_PROMPT = (
    "这段对话的核心话题是什么？用1-3个中文关键词概括（逗号分隔），"
    "不要解释，不要句号。\n"
    "[用户] {user}\n[AI] {ai}\n话题："
)

_REWRITE_PROMPT = (
    "把下面的问题改写成更适合检索记忆的简短查询（1句话，不超过30字），不要解释。\n"
    "问题：{query}\n查询："
)


def _extract_topic(user_text: str, ai_text: str, llm_call: Callable) -> str:
    """后台 LLM 提取话题标签。失败时返回 '通用'。"""
    try:
        msgs = [{"role": "user", "content": _TOPIC_PROMPT.format(
            user=user_text[:200], ai=(ai_text or "")[:400]
        )}]
        result = llm_call(msgs)
        topic = re.sub(r'[。！？\n]', '', (result or "通用").strip())
        topic = topic.strip(',，').strip()[:50]
        return topic or "通用"
    except Exception:
        return "通用"


def _rewrite_query(query: str, llm_call: Callable) -> str:
    """单次 LLM 改写检索 query，失败时原样返回。"""
    try:
        msgs = [{"role": "user", "content": _REWRITE_PROMPT.format(query=query[:200])}]
        result = llm_call(msgs)
        rewritten = (result or "").strip()
        if 5 < len(rewritten) < 200:
            return rewritten
    except Exception:
        pass
    return query


# ── 后台合并逻辑 ──────────────────────────────────────────────────────────────

def _background_finalize(
    person: str,
    block_id: str,
    merged_content: str,
    user_text: str,
    ai_text: str,
    conv_id: str,
    ts: str,
    user_msg_id: str,
    ai_msg_id: str,
    llm_call: Callable,
) -> None:
    """
    后台线程：提取话题标签，若与上一块同话题则合并，否则更新话题标签。
    操作方式：删除旧占位块 + 重新 add（ChromaDB 不支持 update）。
    """
    topic = _extract_topic(user_text, ai_text, llm_call)

    with _get_person_lock(person):
        state      = _session_state.get(person, {})
        last_id    = state.get("last_block_id")
        last_topic = state.get("last_topic", "")
        last_count = int(state.get("last_round_count", 0))
        last_body  = state.get("last_block_content", "")

        try:
            col = _get_collection(person)
        except Exception as e:
            print(f"[Memory] {person}: 获取集合失败 ({e})")
            return

        should_merge = (
            last_id
            and last_id != block_id
            and last_topic == topic
            and last_count < _MAX_BLOCK_ROUNDS
        )

        if should_merge:
            combined    = last_body + "\n\n" + merged_content
            new_block_id = f"{block_id}_m"
            try:
                col.delete(ids=[last_id, block_id])
                col.add(
                    documents=[combined],
                    metadatas=[{"topic": topic, "conv_id": conv_id,
                                "user_msg_id": user_msg_id,
                                "ai_msg_id": ai_msg_id,
                                "ts": ts, "round_count": str(last_count + 1)}],
                    ids=[new_block_id],
                )
                _session_state[person] = {
                    "last_block_id":      new_block_id,
                    "last_topic":         topic,
                    "last_block_content": combined,
                    "last_round_count":   last_count + 1,
                }
                print(f"[Memory] {person}: 合并话题「{topic}」→ 块 #{last_count + 1}")
            except Exception as e:
                print(f"[Memory] {person}: 合并失败 ({e})，保留独立块")
                should_merge = False  # fall through to standalone path

        if not should_merge:
            # 独立块：仅更新占位块的话题（delete + re-add）
            try:
                col.delete(ids=[block_id])
                col.add(
                    documents=[merged_content],
                    metadatas=[{"topic": topic, "conv_id": conv_id,
                                "user_msg_id": user_msg_id,
                                "ai_msg_id": ai_msg_id,
                                "ts": ts, "round_count": "1"}],
                    ids=[block_id],
                )
            except Exception as e:
                print(f"[Memory] {person}: 话题更新失败 ({e})")
            _session_state[person] = {
                "last_block_id":      block_id,
                "last_topic":         topic,
                "last_block_content": merged_content,
                "last_round_count":   1,
            }
            print(f"[Memory] {person}: 新话题块「{topic}」")

        _save_state()


# ── 公开 API ──────────────────────────────────────────────────────────────────

def list_persons() -> List[Dict]:
    """返回所有记忆人列表，含名称与记忆块数量。"""
    try:
        cli = _get_client()
        result: List[Dict] = []
        for col in cli.list_collections():
            if not col.name.startswith(_COL_PREFIX):
                continue
            name = col.name[len(_COL_PREFIX):]
            try:
                count = cli.get_collection(col.name, embedding_function=_get_ef()).count()
            except Exception:
                count = 0
            result.append({"name": name, "count": count})
        return result
    except Exception:
        return []


def create_person(name: str) -> bool:
    """新建记忆人（已存在时也返回 True）。"""
    name = (name or "").strip()
    if not name:
        return False
    try:
        _get_collection(name)
        return True
    except Exception:
        return False


def delete_person(name: str) -> bool:
    """删除记忆人及其全部记忆块。同时清除会话状态。"""
    try:
        _get_client().delete_collection(_col_name(name))
        with _get_person_lock(name):
            _session_state.pop(name, None)
            _save_state()
        return True
    except Exception:
        return False


def add_round(
    person: str,
    user_text: str,
    ai_text: str,
    *,
    conv_id: str = "",
    round_id: str = "",
    user_msg_id: str = "",
    ai_msg_id: str = "",
    llm_call: Optional[Callable] = None,
) -> bool:
    """
    将一轮对话存入记忆库（立即返回，话题提取与合并在后台完成）。

    llm_call: 可选，Callable[[List[dict]], str]，用于后台话题提取。
              若不提供，则话题标记为 "通用"，不做跨轮合并。
    """
    person    = (person    or "").strip()
    user_text = (user_text or "").strip()
    if not person or not user_text:
        return False

    try:
        merged = (
            f"[用户] {user_text[:_USER_LIMIT]}\n"
            f"[AI] {(ai_text or '').strip()[:_AI_LIMIT]}"
        )
        rid      = round_id or uuid.uuid4().hex[:12]
        block_id = f"{rid}_b"
        ts       = str(int(time.time()))

        col = _get_collection(person)
        # 立即写入（话题暂定 "通用"，后台更新）
        col.add(
            documents=[merged],
            metadatas=[{"topic": "通用", "conv_id": conv_id,
                        "user_msg_id": user_msg_id,
                        "ai_msg_id": ai_msg_id,
                        "ts": ts, "round_count": "1"}],
            ids=[block_id],
        )

        if llm_call:
            threading.Thread(
                target=_background_finalize,
                args=(person, block_id, merged, user_text, ai_text, conv_id, ts, user_msg_id, ai_msg_id, llm_call),
                daemon=True,
                name=f"mem-topic-{person[:8]}",
            ).start()
        else:
            # 无 LLM：直接更新会话状态
            with _get_person_lock(person):
                _session_state[person] = {
                    "last_block_id":      block_id,
                    "last_topic":         "通用",
                    "last_block_content": merged,
                    "last_round_count":   1,
                }
                _save_state()

        return True
    except Exception as e:
        print(f"[Memory] add_round 失败: {e}")
        return False


def delete_round_entries(
    person: str,
    *,
    conv_id: str = "",
    user_msg_id: str = "",
    ai_msg_id: str = "",
    user_text: str = "",
    ai_text: str = "",
) -> int:
    """按对话/消息关联删除记忆块。返回删除数量。"""
    person = (person or "").strip()
    if not person:
        return 0
    conv_id = (conv_id or "").strip()
    user_msg_id = (user_msg_id or "").strip()
    ai_msg_id = (ai_msg_id or "").strip()
    user_text = (user_text or "").strip()
    ai_text = (ai_text or "").strip()
    if not any([conv_id, user_msg_id, ai_msg_id, user_text, ai_text]):
        return 0

    try:
        col = _get_collection(person)
        result = col.get(include=["documents", "metadatas"])
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []
        if not ids:
            return 0

        user_probe = f"[用户] {user_text[:min(_USER_LIMIT, 80)]}" if user_text else ""
        ai_probe = f"[AI] {ai_text[:min(_AI_LIMIT, 120)]}" if ai_text else ""
        to_delete: List[str] = []

        for _id, doc, meta in zip(ids, docs, metas):
            meta = meta or {}
            _conv = str(meta.get("conv_id") or "").strip()
            if conv_id and _conv and _conv != conv_id:
                continue

            matched = False
            if user_msg_id and str(meta.get("user_msg_id") or "").strip() == user_msg_id:
                matched = True
            if ai_msg_id and str(meta.get("ai_msg_id") or "").strip() == ai_msg_id:
                matched = True

            if not matched and conv_id and isinstance(doc, str):
                if user_probe and user_probe in doc:
                    matched = True
                if ai_probe and ai_probe in doc:
                    matched = True

            if matched:
                to_delete.append(_id)

        if not to_delete:
            return 0

        col.delete(ids=to_delete)
        with _get_person_lock(person):
            st = _session_state.get(person) or {}
            last_id = str(st.get("last_block_id") or "")
            last_content = str(st.get("last_block_content") or "")
            hit_last = last_id in to_delete
            if not hit_last and last_content:
                if user_probe and user_probe in last_content:
                    hit_last = True
                if ai_probe and ai_probe in last_content:
                    hit_last = True
            if hit_last:
                _session_state.pop(person, None)
                _save_state()

        return len(to_delete)
    except Exception as e:
        print(f"[Memory] delete_round_entries 失败: {e}")
        return 0


def search(
    person: str,
    query: str,
    top_k: int = 5,
    llm_call: Optional[Callable] = None,
) -> List[Dict]:
    """
    语义检索记忆。

    llm_call: 可选，若提供则先做单次 query 改写再检索（不迭代）。
    返回: [{text, topic, conv_id, ts, distance}, ...]
    """
    person = (person or "").strip()
    query  = (query  or "").strip()
    if not person or not query:
        return []

    effective_query = query
    if llm_call:
        effective_query = _rewrite_query(query, llm_call)
        if effective_query != query:
            print(f"[Memory] query 改写: '{query[:40]}' → '{effective_query[:40]}'")

    try:
        col = _get_collection(person)
        cnt = col.count()
        if cnt == 0:
            return []
        n   = min(top_k, cnt)
        res = col.query(query_texts=[effective_query], n_results=n)
        hits: List[Dict] = []
        for i, doc in enumerate(res["documents"][0]):
            meta = ((res.get("metadatas") or [[]])[0] or [{}])[i]
            dist = ((res.get("distances") or [[]])[0] or [0.0])[i]
            hits.append({
                "text":     doc,
                "topic":    meta.get("topic", ""),
                "conv_id":  meta.get("conv_id", ""),
                "ts":       meta.get("ts", ""),
                "distance": round(float(dist), 4),
            })
        return hits
    except Exception as e:
        print(f"[Memory] search 失败: {e}")
        return []


# ── 迁移：v1 碎片格式 → v2 合并块格式 ────────────────────────────────────────

def migrate_v1(person: str) -> int:
    """
    将 v1 格式（user/AI 分离存储）原地迁移为 v2 合并块格式。
    返回迁移的原始片段数量（0 表示无需迁移或集合为空）。
    """
    person = (person or "").strip()
    if not person:
        return 0
    try:
        col = _get_collection(person)
        cnt = col.count()
        if cnt == 0:
            return 0

        sample = col.get(limit=1, include=["metadatas"])
        sample_meta = ((sample.get("metadatas") or [{}]))[0] or {}
        if sample_meta.get("topic") is not None:
            # 已是 v2 格式
            return 0

        print(f"[Memory migrate] {person}: 检测到 v1 格式，共 {cnt} 片段，开始迁移…")

        result  = col.get(include=["documents", "metadatas"])
        docs    = result.get("documents") or []
        metas   = result.get("metadatas") or []
        ids_all = result.get("ids") or []

        # 按 round_id 分组
        from collections import defaultdict
        rounds: Dict[str, Dict] = defaultdict(lambda: {"user": "", "ai": "", "ts": "", "conv_id": ""})
        order:  List[str] = []  # 保持 round_id 插入顺序

        for _id, doc, meta in zip(ids_all, docs, metas):
            if _id.endswith("_u"):
                rid = _id[:-2]
            elif _id.endswith("_a"):
                rid = _id[:-2]
            else:
                rid = _id  # 未知格式，当作独立块

            if rid not in rounds:
                order.append(rid)

            if _id.endswith("_u"):
                rounds[rid]["user"]    = doc
                rounds[rid]["ts"]      = meta.get("ts", "")
                rounds[rid]["conv_id"] = meta.get("conv_id", "")
            elif _id.endswith("_a"):
                rounds[rid]["ai"]      = doc
            else:
                rounds[rid]["user"]    = doc
                rounds[rid]["ts"]      = meta.get("ts", "")
                rounds[rid]["conv_id"] = meta.get("conv_id", "")

        # 删除所有旧片段
        if ids_all:
            col.delete(ids=ids_all)

        # 重新插入 v2 格式
        new_docs:  List[str]  = []
        new_metas: List[Dict] = []
        new_ids:   List[str]  = []

        for rid in order:
            r = rounds[rid]
            parts = []
            if r["user"]:
                parts.append(f"[用户] {r['user']}")
            if r["ai"]:
                parts.append(f"[AI] {r['ai']}")
            if not parts:
                continue
            new_docs.append("\n".join(parts))
            new_metas.append({
                "topic":       "通用",
                "conv_id":     r["conv_id"],
                "ts":          r["ts"] or str(int(time.time())),
                "round_count": "1",
            })
            new_ids.append(f"{rid}_v2")

        if new_docs:
            col.add(documents=new_docs, metadatas=new_metas, ids=new_ids)

        print(f"[Memory migrate] {person}: {cnt} 片段 → {len(new_docs)} 合并块 ✓")
        return cnt

    except Exception as e:
        print(f"[Memory migrate] {person} 迁移失败: {e}")
        return 0


def migrate_all_v1() -> Dict[str, int]:
    """迁移所有记忆人的 v1 数据，返回 {person: migrated_count} 字典。"""
    results: Dict[str, int] = {}
    for p in list_persons():
        name = p["name"]
        n = migrate_v1(name)
        if n:
            results[name] = n
    return results


def warmup() -> None:
    """预热嵌入模型，并在后台自动迁移 v1 数据。"""
    try:
        _get_ef()
        print(f"[Memory] 嵌入模型预热完成")
    except Exception as e:
        print(f"[Memory] 嵌入模型预热失败: {e}")
        return
    # 后台迁移旧数据
    def _migrate():
        res = migrate_all_v1()
        if res:
            print(f"[Memory] v1 迁移完成: {res}")
    threading.Thread(target=_migrate, daemon=True, name="mem-migrate-all").start()
