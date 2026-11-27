"""Read-only access to Cursor CLI chat stores (~/.cursor/chats/*/*/store.db).

A store.db is a content-addressed blob table. meta key "0" (hex-encoded JSON)
names latestRootBlobId. The root blob is protobuf; its field 1 repeats 32-byte
blob ids, in order, for the conversation. Message blobs are JSON
({"role": "system"|"user"|"assistant"|"tool", "content": ...}).

Nothing here ever writes to a chat directory.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


@dataclass
class Reply:
    id: str
    text: str
    prompt: str | None
    prompt_ts: str | None
    chat_id: str
    chat_dir: str


@dataclass
class Turn:
    # "complete": the last message is a prose-only assistant reply
    # "in_progress": the assistant is still working (tool calls pending)
    # "waiting": the last message is a user prompt
    # "empty": nothing usable
    status: str
    reply: Reply | None = None


def chat_dirs(root: Path) -> list[Path]:
    try:
        return sorted(p.parent for p in root.glob("*/*/store.db"))
    except OSError:
        return []


def signature(chat_dir: Path) -> tuple:
    """Cheap change detector from file stats (no DB open)."""
    sig = []
    for name in ("store.db-wal", "store.db", "meta.json"):
        try:
            st = (chat_dir / name).stat()
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


def last_update(chat_dir: Path) -> float:
    times = []
    for name in ("store.db-wal", "store.db", "meta.json"):
        try:
            times.append((chat_dir / name).stat().st_mtime)
        except OSError:
            pass
    return max(times) if times else 0.0


def open_ro(chat_dir: Path) -> sqlite3.Connection:
    db = chat_dir / "store.db"
    uri = f"file:{quote(str(db))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _varint(data: bytes, i: int) -> tuple[int, int]:
    x = s = 0
    while i < len(data):
        b = data[i]
        i += 1
        x |= (b & 0x7F) << s
        if b < 0x80:
            return x, i
        s += 7
        if s > 70:
            break
    raise ValueError("bad varint")


def _field1_hashes(data: bytes) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(data):
        try:
            key, i = _varint(data, i)
        except ValueError:
            break
        field, wire = key >> 3, key & 7
        if wire == 0:
            _, i = _varint(data, i)
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        elif wire == 2:
            ln, i = _varint(data, i)
            if i + ln > len(data):
                break
            if field == 1 and ln == 32:
                out.append(data[i : i + ln].hex())
            i += ln
        else:
            break
    return out


def _root_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='0'").fetchone()
    if row is None:
        row = conn.execute("SELECT value FROM meta LIMIT 1").fetchone()
    if row is None:
        return None
    raw = row[0]
    try:
        meta = json.loads(bytes.fromhex(raw).decode())
    except ValueError:
        meta = json.loads(raw)
    return meta.get("latestRootBlobId")


def _blob(conn: sqlite3.Connection, bid: str) -> bytes | None:
    row = conn.execute("SELECT data FROM blobs WHERE id=?", (bid,)).fetchone()
    if not row or row[0] is None:
        return None
    d = row[0]
    return d if isinstance(d, bytes) else str(d).encode("latin1")


def _role_prefix(conn: sqlite3.Connection, bid: str) -> bytes:
    row = conn.execute("SELECT substr(data,1,20) FROM blobs WHERE id=?", (bid,)).fetchone()
    if not row or row[0] is None:
        return b""
    d = row[0]
    return d if isinstance(d, bytes) else str(d).encode("latin1")


def _msg_text(content) -> tuple[str, bool]:
    """Return (prose, has_tool_call) for a message's content."""
    if isinstance(content, str):
        return content.strip(), False
    parts: list[str] = []
    has_tool = False
    if isinstance(content, list):
        for c in content:
            if not isinstance(c, dict):
                continue
            typ = c.get("type")
            if typ in ("tool-call", "tool_use", "tool-result"):
                has_tool = True
            elif typ == "text" or (typ is None and "text" in c):
                parts.append(c.get("text") or "")
    return "\n".join(parts).strip(), has_tool


_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
_TS = re.compile(r"<timestamp>(.*?)</timestamp>")


def _is_real_prompt(text: str) -> bool:
    if "<user_query>" in text:
        return True
    if "Your conversation was summarized due to context constraints" in text:
        return False
    if text.lstrip().startswith("<user_info>"):
        return False
    return bool(text.strip())


def inspect_chat(chat_dir: Path, max_walk: int = 4000) -> Turn:
    conn = open_ro(chat_dir)
    try:
        root = _root_id(conn)
        if not root:
            return Turn("empty")
        rdata = _blob(conn, root)
        if rdata is None:
            return Turn("empty")
        hashes = _field1_hashes(rdata)

        status = None
        reply_id = reply_text = None
        walked = 0
        for bid in reversed(hashes):
            walked += 1
            if walked > max_walk:
                break
            prefix = _role_prefix(conn, bid)
            if prefix.startswith(b'{"role":"tool"') or not prefix.startswith(b'{"role":"'):
                continue
            is_assistant = prefix.startswith(b'{"role":"assistant"')
            is_user = prefix.startswith(b'{"role":"user"')
            if not (is_assistant or is_user):
                continue
            if status is None and is_assistant:
                obj = json.loads(_blob(conn, bid).decode("utf-8"))
                text, has_tool = _msg_text(obj.get("content"))
                if has_tool or not text:
                    return Turn("in_progress")
                status, reply_id, reply_text = "complete", bid, text
                continue
            if is_user:
                obj = json.loads(_blob(conn, bid).decode("utf-8"))
                text, _ = _msg_text(obj.get("content"))
                if not _is_real_prompt(text):
                    if status is None:
                        status = "waiting"
                        return Turn("waiting")
                    continue
                if status is None:
                    return Turn("waiting")
                m = _QUERY.search(text)
                ts = _TS.search(text)
                chat_id = chat_dir.name
                return Turn(
                    "complete",
                    Reply(
                        reply_id,
                        reply_text,
                        m.group(1).strip() if m else text.strip()[:300],
                        ts.group(1) if ts else None,
                        chat_id,
                        str(chat_dir),
                    ),
                )
        if status == "complete":
            return Turn(
                "complete",
                Reply(reply_id, reply_text, None, None, chat_dir.name, str(chat_dir)),
            )
        return Turn("empty")
    finally:
        conn.close()
