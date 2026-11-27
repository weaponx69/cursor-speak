"""Watch every Cursor CLI chat and report each finished reply exactly once."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from . import chatstore, config

log = logging.getLogger("cursor-speak.watcher")


class Watcher:
    """Polls file stats for all chats under the chats root (cheap). A chat that
    changed and then stayed quiet for `quiet_seconds` is opened read-only; if its
    last message is a finished prose reply that has not been seen, on_reply fires.

    Replies that exist at startup are marked seen, never spoken. A chat directory
    that appears after startup (a new session) is examined from scratch.
    """

    def __init__(self, cfg: dict, on_reply: Callable[[chatstore.Reply], None]):
        self.cfg = cfg
        self.on_reply = on_reply
        self.seen: set[str] = set()
        self.sigs: dict[Path, tuple] = {}
        self.dirty: dict[Path, float] = {}
        self.stop_event = threading.Event()
        self.chat_count = 0
        self.last_error: str | None = None
        self.active_chat: str | None = None

    def seed(self) -> None:
        for d in chatstore.chat_dirs(config.CHATS_ROOT):
            self.sigs[d] = chatstore.signature(d)
            try:
                turn = chatstore.inspect_chat(d)
            except Exception as e:
                log.warning("seed: could not read %s: %s", d, e)
                continue
            if turn.status == "complete" and turn.reply:
                self.seen.add(turn.reply.id)
        self.chat_count = len(self.sigs)
        log.info("seeded %d chats, %d existing replies marked as seen", self.chat_count, len(self.seen))

    def _tick(self) -> None:
        now = time.monotonic()
        dirs = chatstore.chat_dirs(config.CHATS_ROOT)
        self.chat_count = len(dirs)
        for d in dirs:
            sig = chatstore.signature(d)
            if self.sigs.get(d) != sig:
                self.sigs[d] = sig
                self.dirty[d] = now
        quiet = float(self.cfg["quiet_seconds"])
        ready = sorted((t, d) for d, t in self.dirty.items() if now - t >= quiet)
        for _, d in ready:
            self.dirty.pop(d, None)
            try:
                turn = chatstore.inspect_chat(d)
            except Exception as e:
                self.last_error = f"{d.name}: {e}"
                log.warning("could not read %s: %s", d, e)
                self.dirty[d] = now  # retry next quiet window
                continue
            self.last_error = None
            if turn.status == "complete" and turn.reply and turn.reply.id not in self.seen:
                self.seen.add(turn.reply.id)
                self.active_chat = d.name
                log.info("new reply %s in chat %s (%d chars)", turn.reply.id[:12], d.name, len(turn.reply.text))
                try:
                    self.on_reply(turn.reply)
                except Exception:
                    log.exception("on_reply failed")

    def run(self) -> None:
        try:
            self.seed()
        except Exception:
            log.exception("seed failed")
        poll = float(self.cfg["poll_seconds"])
        while not self.stop_event.wait(poll):
            try:
                self._tick()
            except Exception:
                log.exception("watcher tick failed")

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="watcher", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self.stop_event.set()
