"""Record 16 kHz mono speech from the default mic until stopped or silent.

The recorder is an external tool writing raw s16le PCM to stdout (pw-record,
parecord or arecord); we watch the level to end the take after a pause.
"""

from __future__ import annotations

import array
import logging
import math
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import BinaryIO, Callable

from . import launcher

log = logging.getLogger("cursor-speak.recorder")

RATE = 16000
FRAME_MS = 30
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2  # s16 mono


def recorder_cmd(which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    if which("pw-record"):
        return ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16",
                "--media-role", "Communication", "-"]
    if which("parecord"):
        return ["parecord", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1"]
    if which("arecord"):
        return ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"]
    return None


def rms(frame: bytes) -> float:
    if len(frame) < 2:
        return 0.0
    a = array.array("h")
    a.frombytes(frame[: len(frame) // 2 * 2])
    return math.sqrt(sum(x * x for x in a) / len(a))


class Endpointer:
    """Decides when a take is over: speech then `silence_s` of quiet, nothing
    heard for `no_speech_s`, or `max_s` reached."""

    def __init__(self, threshold: float = 500.0, silence_s: float = 1.5,
                 no_speech_s: float = 8.0, max_s: float = 60.0):
        self.threshold = threshold
        self.silence_s = silence_s
        self.no_speech_s = no_speech_s
        self.max_s = max_s
        self.t = 0.0
        self.heard = False
        self.quiet = 0.0
        self.reason: str | None = None

    def feed(self, frame: bytes) -> bool:
        """Returns True when recording should stop."""
        dt = len(frame) / 2 / RATE
        self.t += dt
        if rms(frame) >= self.threshold:
            self.heard = True
            self.quiet = 0.0
        else:
            self.quiet += dt
        if self.heard and self.quiet >= self.silence_s:
            self.reason = "silence"
        elif not self.heard and self.t >= self.no_speech_s:
            self.reason = "no speech"
        elif self.t >= self.max_s:
            self.reason = "max length"
        return self.reason is not None


def capture(stream: BinaryIO, ep: Endpointer, stop: threading.Event) -> bytes:
    """Read PCM frames from `stream` until the endpointer or `stop` says so."""
    pcm = bytearray()
    while not stop.is_set():
        frame = stream.read(FRAME_BYTES)
        if not frame:
            ep.reason = ep.reason or "recorder ended"
            break
        pcm += frame
        if ep.feed(frame):
            break
    if stop.is_set() and not ep.reason:
        ep.reason = "stopped"
    return bytes(pcm)


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def record(dest: Path, ep: Endpointer, stop: threading.Event, cmd: list[str] | None = None) -> str:
    """Record from the mic into `dest` (WAV). Returns why it stopped."""
    cmd = cmd or recorder_cmd()
    if not cmd:
        raise RuntimeError("no recorder found (install pipewire-bin, pulseaudio-utils or alsa-utils)")
    log.info("recording with %s", cmd[0])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=launcher.host_env())
    try:
        # A stop request must not wait for the next read to return.
        threading.Thread(target=lambda: (stop.wait(), _terminate(proc)), daemon=True).start()
        started = time.monotonic()
        pcm = capture(proc.stdout, ep, stop)
        log.info("recorded %.1fs (%s)", time.monotonic() - started, ep.reason)
    finally:
        _terminate(proc)
    write_wav(dest, pcm)
    return ep.reason or "done"


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


class Segmenter:
    """Always-on voice activity detection for conversation mode.

    Feed it 30 ms frames forever. It keeps an adaptive noise floor, starts an
    utterance after `start_s` of loud frames (keeping `preroll_s` before it so
    the first syllable isn't lost) and ends it after `silence_s` of quiet.
    While our own speech is playing (`speaking=True`) the bar is raised by
    `barge_factor` and must be held longer, so the speakers' echo is less
    likely to count as you.
    """

    def __init__(self, threshold: float = 500.0, silence_s: float = 1.0, start_s: float = 0.15,
                 preroll_s: float = 0.3, max_s: float = 60.0, min_s: float = 0.35,
                 barge_factor: float = 3.0, barge_start_s: float = 0.35, floor_ratio: float = 3.0):
        self.threshold = threshold
        self.silence_s = silence_s
        self.start_s = start_s
        self.preroll = int(preroll_s * 1000 / FRAME_MS)
        self.max_s = max_s
        self.min_s = min_s
        self.barge_factor = barge_factor
        self.barge_start_s = barge_start_s
        self.floor_ratio = floor_ratio
        self.floor = 0.0
        self.recent: list[bytes] = []
        self.loud_run = 0.0
        self.active = False
        self.pcm = bytearray()
        self.quiet = 0.0
        self.voiced = 0.0
        self.length = 0.0

    def level(self, speaking: bool) -> float:
        bar = max(self.threshold, self.floor * self.floor_ratio)
        return bar * (self.barge_factor if speaking else 1.0)

    def feed(self, frame: bytes, speaking: bool = False):
        """Returns None, "start" (speech began) or ("utterance", pcm_bytes)."""
        dt = len(frame) / 2 / RATE
        level = rms(frame)
        loud = level >= self.level(speaking)
        if not self.active:
            if not loud:
                # Track background noise only while nobody is talking.
                self.floor = level if self.floor == 0 else 0.95 * self.floor + 0.05 * level
            self.recent.append(frame)
            if len(self.recent) > self.preroll + 20:
                self.recent.pop(0)
            self.loud_run = self.loud_run + dt if loud else 0.0
            need = self.barge_start_s if speaking else self.start_s
            if self.loud_run >= need:
                self.active = True
                keep = int(self.loud_run * 1000 / FRAME_MS) + self.preroll
                self.pcm = bytearray(b"".join(self.recent[-keep:]))
                self.length = len(self.pcm) / 2 / RATE
                self.voiced = self.loud_run
                self.quiet = 0.0
                self.recent = []
                return "start"
            return None
        self.pcm += frame
        self.length += dt
        if level >= self.level(False):
            self.voiced += dt
            self.quiet = 0.0
        else:
            self.quiet += dt
        if self.quiet >= self.silence_s or self.length >= self.max_s:
            self.active = False
            self.loud_run = 0.0
            pcm, self.pcm = bytes(self.pcm), bytearray()
            if self.voiced < self.min_s:
                return ("discard", pcm)
            return ("utterance", pcm)
        return None

    def reset(self) -> None:
        self.active = False
        self.loud_run = 0.0
        self.pcm = bytearray()
        self.recent = []
