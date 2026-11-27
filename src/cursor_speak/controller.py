"""Ties watcher, text cleanup and speaker together; owns pause/replay state."""

from __future__ import annotations

import json
import logging
import threading
import time

from . import chatstore, config, setup_voice, textclean
from .speaker import Speaker
from .watcher import Watcher

log = logging.getLogger("cursor-speak")


class Controller:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.paused = False
        self.setup_note: str | None = None
        self.speaker = Speaker(cfg)
        self.watcher = Watcher(cfg, self._on_reply)
        self.last: dict | None = None
        self.quit_event = threading.Event()
        self._load_last()

    # state -------------------------------------------------------------------
    def _load_last(self) -> None:
        try:
            self.last = json.loads(config.STATE_FILE.read_text()).get("last")
        except (OSError, ValueError):
            self.last = None

    def _save_last(self) -> None:
        try:
            config.STATE_DIR.mkdir(parents=True, exist_ok=True)
            config.STATE_FILE.write_text(json.dumps({"last": self.last}))
        except OSError:
            pass

    # lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        self.speaker.start()
        self.watcher.start()
        if self.cfg.get("auto_setup", True) and not setup_voice.is_ready(self.cfg["voice"]):
            threading.Thread(target=self._first_run_setup, name="setup", daemon=True).start()

    def _first_run_setup(self) -> None:
        def progress(msg: str) -> None:
            self.setup_note = msg
            log.info("setup: %s", msg)

        try:
            setup_voice.run(self.cfg["voice"], progress)
        except Exception as e:
            log.exception("voice setup failed")
            self.setup_note = f"Voice setup failed ({e.__class__.__name__}); using {self.speaker.backend}"
            return
        self.speaker.reload()
        self.setup_note = None
        if not self.paused:
            self.speaker.speak("Cursor Speak is ready.")

    def shutdown(self) -> None:
        self.watcher.stop()
        self.speaker.close()

    # events ------------------------------------------------------------------
    def _on_reply(self, reply: chatstore.Reply) -> None:
        spoken = textclean.clean(reply.text, self.cfg["identifiers"], int(self.cfg["max_chars"]))
        if not spoken:
            log.info("reply %s has nothing speakable; skipped", reply.id[:12])
            return
        self.last = {
            "id": reply.id,
            "chat": reply.chat_id,
            "prompt": (reply.prompt or "")[:200],
            "spoken": spoken,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_last()
        if self.paused:
            log.info("paused; reply %s kept for Replay but not spoken", reply.id[:12])
            return
        self.speaker.speak(spoken)

    # commands ----------------------------------------------------------------
    def pause(self) -> None:
        self.paused = True
        self.speaker.stop()

    def resume(self) -> None:
        self.paused = False

    def toggle(self) -> None:
        self.resume() if self.paused else self.pause()

    def stop_speaking(self) -> None:
        self.speaker.stop()

    def replay(self) -> bool:
        if not self.last:
            return False
        self.speaker.stop()
        self.speaker.speak(self.last["spoken"])
        return True

    def say(self, text: str) -> None:
        self.speaker.stop()
        self.speaker.speak(text)

    def test(self) -> None:
        self.say("Cursor Speak is working.")

    def request_quit(self) -> None:
        self.quit_event.set()

    def status_line(self) -> str:
        if self.setup_note:
            return self.setup_note
        if self.paused:
            return "Paused"
        if self.speaker.speaking:
            return "Speaking"
        return f"Listening to {self.watcher.chat_count} chats"

    def status(self) -> dict:
        return {
            "paused": self.paused,
            "speaking": self.speaker.speaking,
            "backend": self.speaker.backend,
            "chats": self.watcher.chat_count,
            "setup": self.setup_note,
            "last": (self.last or {}).get("id"),
            "last_time": (self.last or {}).get("time"),
        }
