import json
from pathlib import Path

import chromadb

MARKERS = [
    "Invalid consecutive assistant message",
    "⚠ HTTP ",
    "HTTP 400:",
    "tool_chat empty result",
    "连接超时，请检查网络或 API 地址",
]

MEM_DIR = Path("data/memory")
STATE_FILE = MEM_DIR / "_session_state.json"


def contains_error_text(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return any(marker in text for marker in MARKERS)


def main() -> None:
    client = chromadb.PersistentClient(path=str(MEM_DIR))
    collections = client.list_collections()

    deleted_by_collection = {}
    deleted_ids = set()

    for coll in collections:
        col = client.get_collection(coll.name)
        data = col.get(include=["documents"])
        ids = data.get("ids") or []
        docs = data.get("documents") or []

        bad_ids = [doc_id for doc_id, doc in zip(ids, docs) if contains_error_text(doc)]
        if bad_ids:
            col.delete(ids=bad_ids)
            deleted_by_collection[coll.name] = bad_ids
            deleted_ids.update(bad_ids)

    state_cleaned_keys = []
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        cleaned_state = {}
        for person, payload in state.items():
            if not isinstance(payload, dict):
                cleaned_state[person] = payload
                continue
            last_content = str(payload.get("last_block_content", ""))
            last_block_id = str(payload.get("last_block_id", ""))
            if contains_error_text(last_content) or (last_block_id and last_block_id in deleted_ids):
                state_cleaned_keys.append(person)
                continue
            cleaned_state[person] = payload
        STATE_FILE.write_text(json.dumps(cleaned_state, ensure_ascii=False, indent=2), encoding="utf-8")

    total_deleted = sum(len(v) for v in deleted_by_collection.values())
    print(f"deleted_blocks={total_deleted}")
    for cname, ids in deleted_by_collection.items():
        print(f"  {cname}: {len(ids)}")
        for i in ids[:8]:
            print(f"    - {i}")
    print(f"cleaned_state_keys={state_cleaned_keys}")


if __name__ == "__main__":
    main()
