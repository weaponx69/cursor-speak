"""Build synthetic Cursor-style store.db files for tests."""

import hashlib
import json
import sqlite3
from pathlib import Path


def _blob_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeChat:
    def __init__(self, root: Path, workspace: str, chat_id: str):
        self.dir = root / workspace / chat_id
        self.dir.mkdir(parents=True)
        self.hashes: list[str] = []
        self.conn = sqlite3.connect(self.dir / "store.db")
        self.conn.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
        self.conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        (self.dir / "meta.json").write_text("{}")
        self.add("system", "You are a coding assistant.")
        self.add("user", "<user_info>\nOS: linux\n</user_info>")
        self._commit()

    def add(self, role: str, content, tool_call: bool = False) -> str:
        if role == "user" and isinstance(content, str) and not content.startswith("<user_info>"):
            content = [{"type": "text", "text": f"<timestamp>Friday</timestamp>\n<user_query>\n{content}\n</user_query>"}]
        if role == "assistant":
            parts = [{"type": "reasoning", "text": ""}]
            if content:
                parts.append({"type": "text", "text": content})
            if tool_call:
                parts.append({"type": "tool-call", "toolCallId": "c1", "toolName": "Shell", "args": {}})
            content = parts
        if role == "tool":
            content = [{"type": "tool-result", "toolCallId": "c1", "toolName": "Shell", "result": "x" * 50}]
        data = json.dumps({"role": role, "content": content}, separators=(",", ":")).encode()
        bid = _blob_id(data)
        self.conn.execute("INSERT OR REPLACE INTO blobs VALUES (?, ?)", (bid, data))
        self.hashes.append(bid)
        self._commit()
        return bid

    def _commit(self) -> None:
        root = b"".join(b"\x0a\x20" + bytes.fromhex(h) for h in self.hashes)
        rid = _blob_id(root)
        self.conn.execute("INSERT OR REPLACE INTO blobs VALUES (?, ?)", (rid, root))
        meta = json.dumps({"latestRootBlobId": rid}).encode().hex()
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('0', ?)", (meta,))
        self.conn.commit()
        (self.dir / "meta.json").write_text(json.dumps({"n": len(self.hashes)}))

    def close(self) -> None:
        self.conn.close()
