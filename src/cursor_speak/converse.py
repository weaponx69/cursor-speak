"""Conversation mode: talk to cursor-agent hands-free, like a phone call.

Cursor Speak owns the conversation: the mic is always on (voice activity
detection), each thing you say is transcribed locally and sent to
`cursor-agent -p --resume <chat>` in your workspace, the agent's progress shows
in a window, and its final answer is read aloud. Speak over it to interrupt.
No typing into other windows, so no ydotool and no sudo.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator

from . import agent, config, control, cues, recorder, textclean

log = logging.getLogger("cursor-speak.converse")

# Voice commands: the whole utterance must be one of these (after the wake word
# and filler words), so "stop the dev server" still goes to the agent.
COMMANDS = {
    "pause": ["pause listening", "stop listening", "pause", "mute", "hold on"],
    "resume": ["resume", "resume listening", "start listening", "im back", "unmute", "unpause"],
    "stop": ["stop", "stop talking", "cancel", "be quiet", "quiet", "never mind", "nevermind",
             "shut up", "stop that"],
    "new": ["new conversation", "start over", "new chat", "start a new conversation"],
    "end": ["goodbye", "good bye", "bye", "bye bye", "end conversation", "end the conversation",
            "hang up", "thats all", "that is all"],
}
FILLER = {"please", "ok", "okay", "um", "uh", "hey", "now", "cursor", "thanks", "thank", "you"}

STATES = ("starting", "listening", "standby", "hearing", "transcribing", "thinking", "speaking", "paused", "ended")


# text helpers -------------------------------------------------------------------
def normalize(text: str) -> str:
    text = text.lower().replace("’", "'").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text)).strip()


def match_command(text: str) -> str | None:
    norm = normalize(text)
    if not norm or len(norm.split()) > 6:
        return None
    words = norm.split()
    core = " ".join(w for w in words if w not in FILLER) or norm
    for name, phrases in COMMANDS.items():
        if norm in phrases or core in phrases:
            return name
    return None


def strip_wake(text: str, wake: str) -> str | None:
    """Text after the wake phrase, '' for the wake phrase alone, None if absent.
    Fuzzy, because Whisper hears 'hey cursor' as 'hey, Cursor.', 'hey curser'..."""
    wake_words = normalize(wake).split()
    if not wake_words:
        return text
    raw = text.strip()
    words = raw.split()
    n = len(wake_words)
    for extra in (0, 1):  # allow one stray word like "Okay, hey Cursor"
        head = normalize(" ".join(words[extra:extra + n]))
        if head and difflib.SequenceMatcher(None, head, " ".join(wake_words)).ratio() >= 0.75:
            rest = " ".join(words[extra + n:])
            return rest.lstrip(" ,.!?:;-").strip()
    return None


def is_echo(heard: str, spoken: str, ratio: float = 0.6) -> bool:
    """True when what the mic heard is mostly the words we were just saying."""
    h = normalize(heard).split()
    s = set(normalize(spoken).split())
    if not h or not s:
        return False
    return sum(1 for w in h if w in s) / len(h) >= ratio


# remembered chats per workspace -----------------------------------------------
def _chats_file() -> Path:
    return config.STATE_DIR / "conversations.json"


def remembered_chat(workspace: str) -> str | None:
    try:
        return json.loads(_chats_file().read_text()).get(workspace)
    except (OSError, ValueError, AttributeError):
        return None


def remember_chat(workspace: str, chat_id: str | None) -> None:
    try:
        data = json.loads(_chats_file().read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if chat_id:
        data[workspace] = chat_id
    else:
        data.pop(workspace, None)
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        _chats_file().write_text(json.dumps(data, indent=2))
    except OSError:
        pass


# the mic ------------------------------------------------------------------------
class Mic:
    """Continuous 30 ms frames from the recorder command until closed."""

    def __init__(self, target: str | None = None):
        cmd = recorder.recorder_cmd()
        if not cmd:
            raise RuntimeError("no recorder found (install pipewire-bin)")
        if target and cmd[0] == "pw-record":
            cmd = cmd[:-1] + ["--target", target, "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def frames(self) -> Iterator[bytes]:
        while True:
            f = self.proc.stdout.read(recorder.FRAME_BYTES)
            if not f:
                return
            yield f

    def close(self) -> None:
        recorder._terminate(self.proc)


# UI interface -------------------------------------------------------------------
class UI:
    """What the conversation tells its window/terminal. All calls may come from
    worker threads."""

    def state(self, state: str, detail: str = "") -> None: ...
    def line(self, kind: str, text: str) -> None: ...   # you, agent, tool, info, error
    def ended(self) -> None: ...


class Conversation:
    def __init__(self, cfg: dict, workspace: str, ui: UI, speaker, transcriber,
                 chat_id: str | None = None, runner: agent.AgentRunner | None = None,
                 mic_factory: Callable[[], object] | None = None,
                 create_chat: Callable[[], str | None] | None = None,
                 cue: Callable[[str], object] | None = None,
                 tell_app: Callable[[str], str | None] | None = None,
                 restart_mic: bool = True):
        self.cfg = cfg
        self.workspace = workspace
        self.ui = ui
        self.speaker = speaker
        self.transcriber = transcriber
        self.chat_id = chat_id
        self.runner = runner or agent.AgentRunner(cfg)
        self.mic_factory = mic_factory or (lambda: Mic(cfg.get("_mic_target")))
        self._create_chat = create_chat or (lambda: agent.create_chat(cfg, workspace))
        self.cue = cue or (lambda name: cues.play(name, cfg))
        self.tell_app = tell_app or (lambda cmd: control.send(cmd, timeout=1.0))
        self.wake = (cfg.get("converse_wake_word") or "").strip() if cfg.get("converse_wake_word_enabled") else ""
        self.barge = cfg.get("converse_barge_in", "on")  # on | command | off
        self.paused = False      # mic off (shortcut, tray, window)
        self.standby = False     # mic on, waiting for "resume" (voice "pause listening")
        self.followup_until = 0.0
        self.spoken_text = ""
        self.queue: list[str] = []
        self.state_name = "starting"
        self.stop_event = threading.Event()
        self.mic = None
        self._pause_app_on_exit = False
        self._threads: list[threading.Thread] = []
        self._agent_lock = threading.Lock()
        self.restart_mic = restart_mic
        self._utt_during_speech = False
        self.seg = recorder.Segmenter(
            threshold=float(cfg.get("converse_threshold", cfg.get("listen_threshold", 500))),
            silence_s=float(cfg.get("converse_silence_seconds", 1.0)),
            max_s=float(cfg.get("listen_max_seconds", 60)),
            barge_factor=float(cfg.get("converse_barge_factor", 3.0)),
        )

    # lifecycle -----------------------------------------------------------------
    def start(self) -> None:
        if not self.chat_id:
            self.chat_id = self._create_chat()
        if self.chat_id:
            remember_chat(self.workspace, self.chat_id)
            self._own(self.chat_id)
        self.ui.line("info", f"Workspace: {self.workspace}")
        self.ui.line("info", "Chat: " + (self.chat_id or "new (created on the first turn)"))
        if self.wake:
            self.ui.line("info", f'Say "{self.wake}" before each request.')
        self._spawn(self._audio_loop, "converse-audio")
        self._spawn(self._ticker, "converse-ticker")
        self._set("listening")
        self.cue("listening")

    def _spawn(self, fn, name: str) -> None:
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def end(self, say_goodbye: bool = False) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.runner.cancel()
        self._close_mic()
        if say_goodbye:
            self.cue("goodbye")
        if self.chat_id:
            self._disown(self.chat_id)
        if self._pause_app_on_exit:
            self.tell_app("resume")
        self._set("ended")
        self.ui.ended()

    def wait(self, timeout: float | None = None) -> bool:
        return self.stop_event.wait(timeout)

    # app watcher coordination: never read the same reply twice ---------------------
    def _own(self, chat_id: str) -> None:
        reply = self.tell_app(f"own {chat_id}")
        if reply == "unknown command":
            # An older Cursor Speak is running: pause its reader while we talk.
            st = self.tell_app("status")
            try:
                paused = json.loads(st or "{}").get("paused", True)
            except ValueError:
                paused = True
            if not paused:
                self.tell_app("pause")
                self._pause_app_on_exit = True
                self.ui.line("info", "Paused the older Cursor Speak reader to avoid reading replies twice.")

    def _disown(self, chat_id: str) -> None:
        self.tell_app(f"disown {chat_id}")

    # state ---------------------------------------------------------------------
    def _set(self, state: str, detail: str = "") -> None:
        if state != self.state_name or detail:
            self.state_name = state
            self.ui.state(state, detail)

    def _idle_state(self) -> str:
        if self.paused:
            return "paused"
        if self.runner.busy:
            return "thinking"
        if self.speaker.speaking:
            return "speaking"
        if self.seg.active:
            return "hearing"
        return "standby" if self.standby else "listening"

    def _ticker(self) -> None:
        was_speaking = False
        while not self.stop_event.wait(0.2):
            if self.state_name not in ("transcribing",):
                self._set(self._idle_state())
            speaking = self.speaker.speaking
            if was_speaking and not speaking:
                self.followup_until = time.monotonic() + float(self.cfg.get("converse_followup_seconds", 15))
            was_speaking = speaking

    # controls (shortcut, tray, window, voice) -------------------------------------
    def pause(self) -> None:
        self.paused = True
        self._close_mic()
        self.seg.reset()
        self._set("paused")
        self.cue("paused")

    def resume(self) -> None:
        self.paused = False
        self.standby = False
        self._set("listening")
        self.cue("listening")

    def toggle_pause(self) -> None:
        self.resume() if self.paused or self.standby else self.pause()

    def stop(self) -> None:
        """Stop talking and cancel a running agent turn."""
        self.speaker.stop()
        self.queue.clear()
        if self.runner.cancel():
            self.ui.line("info", "Cancelled the agent's turn.")

    def new_conversation(self) -> None:
        self.stop()
        old = self.chat_id
        self.chat_id = self._create_chat()
        if old:
            self._disown(old)
        if self.chat_id:
            self._own(self.chat_id)
        remember_chat(self.workspace, self.chat_id)
        self.ui.line("info", "New conversation" + (f" ({self.chat_id})" if self.chat_id else ""))

    # audio ---------------------------------------------------------------------
    def _close_mic(self) -> None:
        m, self.mic = self.mic, None
        if m:
            try:
                m.close()
            except Exception:
                pass

    def _audio_loop(self) -> None:
        while not self.stop_event.is_set():
            if self.paused:
                time.sleep(0.1)
                continue
            try:
                self.mic = self.mic_factory()
            except Exception as e:
                self.ui.line("error", f"Microphone: {e}")
                self.cue("error")
                self.end()
                return
            try:
                frames: Iterable[bytes] = self.mic.frames()
                for frame in frames:
                    if self.stop_event.is_set() or self.paused:
                        break
                    self._on_frame(frame)
            finally:
                self._close_mic()
            if not self.restart_mic:
                return
            time.sleep(0.2)  # recorder ended: restart it

    def _on_frame(self, frame: bytes) -> None:
        speaking = bool(self.speaker.speaking)
        if speaking and self.barge == "off":
            return
        ev = self.seg.feed(frame, speaking=speaking)
        if ev == "start":
            self._utt_during_speech = speaking
            if speaking and self.barge == "on":
                self.speaker.stop()
                log.info("barge-in: stopped speaking")
            self._set("hearing")
        elif isinstance(ev, tuple) and ev[0] == "utterance":
            self._spawn(lambda pcm=ev[1], sp=self._utt_during_speech or speaking:
                        self._handle_audio(pcm, sp), "converse-stt")

    def _handle_audio(self, pcm: bytes, during_speech: bool) -> None:
        tmp = tempfile.NamedTemporaryFile(prefix="cursor-speak-utt-", suffix=".wav", delete=False,
                                          dir=os.environ.get("XDG_RUNTIME_DIR"))
        tmp.close()
        try:
            recorder.write_wav(Path(tmp.name), pcm)
            self._set("transcribing")
            text = self.transcriber.transcribe(tmp.name)
        except Exception as e:
            log.exception("transcription failed")
            self.ui.line("error", f"Speech recognition: {e}")
            text = ""
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            self.state_name = ""
            self._set(self._idle_state())
        if text:
            self.handle_text(text, during_speech=during_speech)

    # what was said ---------------------------------------------------------------
    def handle_text(self, text: str, during_speech: bool = False) -> str:
        """Act on one transcribed utterance. Returns what happened (for tests/logs)."""
        text = text.strip()
        if not text or normalize(text) in ("", "you", "thank you", "thanks for watching"):
            return "ignored"  # Whisper's usual hallucinations on noise
        if during_speech and self.spoken_text and is_echo(text, self.spoken_text):
            log.info("ignored echo of our own speech")
            return "echo"
        body = text
        if self.wake and time.monotonic() > self.followup_until and not self.runner.busy:
            rest = strip_wake(text, self.wake)
            if rest is None:
                cmd = match_command(text)
                if cmd in ("stop", "pause", "end") and (self.speaker.speaking or self.runner.busy):
                    return self._command(cmd)
                return "no wake word"
            if rest == "":
                self.followup_until = time.monotonic() + float(self.cfg.get("converse_followup_seconds", 15))
                self.cue("listening")
                return "woke"
            body = rest
        elif self.wake:
            rest = strip_wake(text, self.wake)
            if rest:
                body = rest
        cmd = match_command(body)
        if self.standby:
            if cmd in ("resume", "end"):
                return self._command(cmd)
            return "standby"
        if cmd:
            return self._command(cmd)
        if during_speech and self.barge == "command":
            return "ignored during speech"
        self.send(body)
        return "sent"

    def _command(self, cmd: str) -> str:
        self.ui.line("info", {"pause": "Paused listening. Say \"resume\" when you're back.",
                              "resume": "Listening again.", "stop": "Stopped.",
                              "new": "Starting a new conversation.", "end": "Goodbye."}[cmd])
        if cmd == "pause":
            self.speaker.stop()
            self.standby = True
            self.cue("paused")
        elif cmd == "resume":
            self.resume()
        elif cmd == "stop":
            self.stop()
        elif cmd == "new":
            self.new_conversation()
        elif cmd == "end":
            self.speaker.stop()
            self.end(say_goodbye=True)
        return f"command:{cmd}"

    # agent turns -----------------------------------------------------------------
    def send(self, text: str) -> None:
        self.ui.line("you", text)
        if self.runner.busy:
            self.queue.append(text)
            self.ui.line("info", "Queued; I'll send that when the agent finishes.")
            return
        self.speaker.stop()
        self._spawn(lambda: self._turn(text), "converse-agent")

    def _turn(self, text: str) -> None:
        with self._agent_lock:
            self.cue("thinking")
            self._set("thinking")

            def on_event(kind: str, payload: str) -> None:
                if kind == "session":
                    if not self.chat_id:
                        self.chat_id = payload
                        remember_chat(self.workspace, payload)
                        self._own(payload)
                elif kind == "assistant":
                    self.ui.line("agent", payload)
                elif kind == "tool":
                    self.ui.line("tool", payload)
                elif kind == "error":
                    self.ui.line("error", payload)

            turn = self.runner.run(self.workspace, self.chat_id, text, on_event)
            if turn.error == "cancelled" or self.stop_event.is_set():
                return
            if turn.error:
                self.cue("error")
                self.speaker.speak("The agent hit an error. " + textclean.clean(turn.error, "speak", 200))
            else:
                spoken = textclean.clean(turn.final_text(), self.cfg.get("identifiers", "speak"),
                                         int(self.cfg.get("max_chars", 2500)))
                if spoken:
                    self.spoken_text = spoken
                    self.speaker.speak(spoken)
            self.followup_until = time.monotonic() + float(self.cfg.get("converse_followup_seconds", 15)) + 60
        if self.queue and not self.stop_event.is_set():
            nxt = self.queue.pop(0)
            self._spawn(lambda: self._turn(nxt), "converse-agent")


# pid file and toggle ----------------------------------------------------------------
def pid_file() -> Path:
    return config.socket_path().with_name("cursor-speak-converse.pid")


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
    try:
        if b"converse" not in Path(f"/proc/{pid}/cmdline").read_bytes():
            return None
    except OSError:
        pass
    return pid


def signal_active(sig: int) -> bool:
    pid = active_pid()
    if not pid:
        return False
    os.kill(pid, sig)
    return True


def install_signals(conv: Conversation, on_main: Callable[[Callable[[], None]], None]) -> None:
    """SIGUSR1 pauses/resumes (the shortcut), SIGTERM/SIGINT end."""
    def handler(sig, _frame):
        if sig == signal.SIGUSR1:
            on_main(conv.toggle_pause)
        else:
            on_main(conv.end)
    for sig in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)
