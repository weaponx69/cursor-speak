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
