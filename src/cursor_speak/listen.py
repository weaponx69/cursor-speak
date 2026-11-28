"""Push-to-talk: record, transcribe locally, hand the text to Cursor.

`cursor-speak listen --toggle` is what the keyboard shortcut runs: the first
press starts recording, the next press stops it (so does a pause in speech).
The running app, when there is one, is told so it can pause speech and show a
listening icon; listening also works without it.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from . import config, control, deliver, launcher, recorder, stt

log = logging.getLogger("cursor-speak.listen")


# pid file: one recorder at a time ------------------------------------------------
def pid_file() -> Path:
    return config.socket_path().with_name("cursor-speak-listen.pid")


def active_pid() -> int | None:
    try:
        pid = int(pid_file().read_text().strip())
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    try:  # don't signal a recycled pid that isn't us
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        if b"listen" not in cmdline:
            return None
    except OSError:
        pass
    return pid


def stop_active() -> bool:
    pid = active_pid()
    if not pid:
        return False
    os.kill(pid, signal.SIGUSR1)
    return True


# notifications -------------------------------------------------------------------
class Notifier:
    """One desktop notification that is updated in place (notify-send -p/-r)."""

    def __init__(self, enabled: bool = True, run=subprocess.run):
        self.enabled = enabled and bool(shutil.which("notify-send"))
        self.id: str | None = None
        self.run = run

    def show(self, title: str, body: str = "", urgent: bool = False) -> None:
        if not self.enabled:
            return
        cmd = ["notify-send", "-a", "Cursor Speak", "-i", "audio-input-microphone", "-p",
               "-h", "string:x-canonical-private-synchronous:cursor-speak-listen", "-e"]
        if urgent:
            cmd += ["-u", "critical"]
        if self.id:
            cmd += ["-r", self.id]
        cmd += [title, body]
        try:
            res = self.run(cmd, capture_output=True, text=True, timeout=5, env=launcher.host_env())
            out = (res.stdout or "").strip()
            if out.isdigit():
                self.id = out
        except (subprocess.SubprocessError, OSError):
            pass


def _tell_app(state: str) -> None:
    control.send(f"listening {state}", timeout=1.0)


def _short(text: str, n: int = 120) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# main flow -----------------------------------------------------------------------
def run(cfg: dict, toggle: bool = False, mode: str | None = None, enter: bool | None = None,
        wav: str | None = None, notify: bool = True, out=print) -> int:
    if toggle and stop_active():
        out("Stopped listening.")
        return 0

    note = Notifier(notify and cfg.get("listen_notify", True))
    model = cfg.get("stt_model", "base.en")
    if not stt.is_ready(model):
        started = stt.start_background_setup(model)
        msg = ("Setting up speech recognition (one-time download, about 200 MB). "
               "Try again in a few minutes." if started else
               "Speech recognition is still being set up. Try again in a minute.")
        note.show("Cursor Speak: voice input not ready yet", msg)
        out(msg)
        return 3

    tmpdir = None
    try:
        if wav is None:
            stop = threading.Event()
            pid_file().write_text(str(os.getpid()))
            for sig in (signal.SIGUSR1, signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())
            _tell_app("on")
            note.show("Listening…", "Speak now. Press the shortcut again (or pause) to finish.")
            tmpdir = tempfile.mkdtemp(prefix="cursor-speak-")
            wav = os.path.join(tmpdir, "take.wav")
            ep = recorder.Endpointer(
                threshold=float(cfg.get("listen_threshold", 500)),
                silence_s=float(cfg.get("listen_silence_seconds", 1.5)),
                no_speech_s=float(cfg.get("listen_no_speech_seconds", 8)),
                max_s=float(cfg.get("listen_max_seconds", 60)),
            )
            try:
                reason = recorder.record(Path(wav), ep, stop)
            finally:
                try:
                    pid_file().unlink()
                except OSError:
                    pass
            if reason == "no speech":
                note.show("Didn't hear anything", "Check your microphone in Settings › Sound.")
                out("No speech heard.")
                return 4
        _tell_app("busy")
        note.show("Transcribing…")
        text = stt.transcribe(wav, model, cfg.get("stt_language", "en"))
        if not text:
            note.show("Didn't catch that", "Nothing was recognised. Try again a little closer to the mic.")
            out("Nothing recognised.")
            return 4
        log.info("heard %d chars", len(text))
        res = deliver.deliver(text, cfg, mode, enter)
        if not res.ok:
            note.show("Couldn't send your words", res.note, urgent=True)
            out(f"{text}\n({res.note})")
            return 5
        title = {"new": "Sent to a new cursor-agent", "ydotool": "Typed", "wtype": "Typed",
                 "clipboard": "Copied to the clipboard", "print": "Heard"}.get(res.how, "Done")
        note.show(title, _short(text) + (f"\n{res.note}" if res.note else ""))
        if res.how != "print":
            out(text)
        return 0
    except Exception as e:
        log.exception("listen failed")
        note.show("Voice input failed", str(e), urgent=True)
        out(f"Voice input failed: {e}")
        return 1
    finally:
        _tell_app("off")
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
