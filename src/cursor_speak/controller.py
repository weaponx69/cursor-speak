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
        # Voice input state from `cursor-speak listen`: None, "on" (recording), "busy" (transcribing)
        self.listening: str | None = None
        self._listen_since = 0.0
        self._held: list[str] = []
        # Chats a conversation-mode session reads aloud itself; skip them here.
        self.owned_chats: set[str] = set()
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
    def own_chat(self, chat_id: str) -> None:
        self.owned_chats.add(chat_id)

    def disown_chat(self, chat_id: str) -> None:
        self.owned_chats.discard(chat_id)

    def _on_reply(self, reply: chatstore.Reply) -> None:
        if reply.chat_id in self.owned_chats:
            log.info("reply %s belongs to a voice conversation; it reads it itself", reply.id[:12])
            return
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
        if self._holding():
            log.info("listening; reply %s held until you finish talking", reply.id[:12])
            self._held.append(spoken)
            return
        self.speaker.speak(spoken)

    # voice input -------------------------------------------------------------
    def _holding(self) -> bool:
        if not (self.listening and self.cfg.get("pause_tts_while_listening", True)):
            return False
        # A listen process that died without saying "off" must not mute us forever.
        if time.monotonic() - self._listen_since > float(self.cfg.get("listen_max_seconds", 60)) + 330:
            self.listening = None
            return False
        return True

    def set_listening(self, state: str) -> None:
        if state in ("on", "busy"):
            if not self.listening:
                self._listen_since = time.monotonic()
            self.listening = state
            if self.cfg.get("pause_tts_while_listening", True):
                self.speaker.stop()
            return
        self.listening = None
        held, self._held = self._held, []
        if held and not self.paused:
            self.speaker.speak(held[-1])

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
        if self.listening == "on":
            return "Listening to you…"
        if self.listening == "busy":
            return "Transcribing…"
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
            "listening": self.listening,
        }
