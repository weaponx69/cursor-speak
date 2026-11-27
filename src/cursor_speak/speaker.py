"""Text-to-speech playback with stop support.

Backends, in order: piper (per-user venv, persistent worker process), espeak-ng,
spd-say. Utterances queue up; stop() cancels playback and drops what is queued.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from . import config, setup_voice
from .textclean import split_chunks

log = logging.getLogger("cursor-speak.speaker")


class Speaker:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.backend = "none"
        self.speaking = False
        self._q: queue.Queue = queue.Queue()
        self._gen = 0
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._worker: subprocess.Popen | None = None
        self._wid = 0
        self._tmp = Path(tempfile.mkdtemp(prefix="cursor-speak-", dir=os.environ.get("XDG_RUNTIME_DIR")))
        self._thread = threading.Thread(target=self._loop, name="speaker", daemon=True)

    # lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self.reload()
        self._thread.start()

    def reload(self) -> None:
        """Pick the best available backend (call again after first-run setup)."""
        voice = self.cfg["voice"]
        with self._lock:
            self._kill_worker()
            if setup_voice.is_ready(voice) and self._player_cmd("x.wav"):
                self.backend = f"piper ({voice})"
            elif shutil.which("espeak-ng"):
                self.backend = "espeak-ng"
            elif shutil.which("spd-say"):
                self.backend = "spd-say"
            else:
                self.backend = "none"
        log.info("speech backend: %s", self.backend)

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._kill_worker()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # public API ------------------------------------------------------------
    def speak(self, text: str) -> None:
        if text.strip():
            self._q.put((self._gen, text))

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            try:
                while True:
                    self._q.get_nowait()
            except queue.Empty:
                pass
            self._kill_proc()
        if self.backend == "spd-say":
            subprocess.run(["spd-say", "-C"], capture_output=True)

    # internals ---------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            gen, text = self._q.get()
            if gen != self._gen:
                continue
            self.speaking = True
            try:
                if self.backend.startswith("piper"):
                    try:
                        self._say_piper(gen, text)
                    except Exception:
                        log.exception("piper failed; using fallback voice")
                        with self._lock:
                            self._kill_worker()
                            self.backend = "espeak-ng" if shutil.which("espeak-ng") else "spd-say"
                        self._say_fallback(gen, text)
                elif self.backend != "none":
                    self._say_fallback(gen, text)
                else:
                    log.warning("no speech backend available")
            finally:
                self.speaking = not self._q.empty()

    def _kill_proc(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.terminate()

    def _kill_worker(self) -> None:
        w = self._worker
        self._worker = None
        if w and w.poll() is None:
            try:
                w.stdin.close()
            except OSError:
                pass
            w.terminate()

    def _player_cmd(self, wav: str) -> list[str] | None:
        vol = float(self.cfg.get("volume", 1.0))
        if shutil.which("pw-play"):
            return ["pw-play", "--volume", str(vol), wav]
        if shutil.which("aplay"):
            return ["aplay", "-q", wav]
        if shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(int(vol * 100)), wav]
        return None

    def _run(self, cmd: list[str], gen: int, stdin_text: str | None = None) -> None:
        with self._lock:
            if gen != self._gen:
                return
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            proc = self._proc
        if stdin_text is not None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except OSError:
                pass
        proc.wait()

    def _say_fallback(self, gen: int, text: str) -> None:
        if self.backend == "espeak-ng":
            self._run(["espeak-ng", "-s", "165", "--stdin"], gen, stdin_text=text)
        else:
            for chunk in split_chunks(text, 600):
                if gen != self._gen:
                    return
                self._run(["spd-say", "-w", chunk], gen)

    # piper ---------------------------------------------------------------------
    def _ensure_worker(self) -> subprocess.Popen:
        if self._worker and self._worker.poll() is None:
            return self._worker
        onnx, _ = setup_voice.voice_paths(self.cfg["voice"])
        worker = Path(__file__).with_name("piper_worker.py")
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        err = open(config.STATE_DIR / "piper-worker.log", "ab")
        self._worker = subprocess.Popen(
            [str(setup_voice.venv_python()), str(worker), str(onnx), str(self.cfg.get("speed", 1.0))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
            bufsize=1,
        )
        ready = self._worker.stdout.readline()
        if not ready or not json.loads(ready).get("ready"):
            raise RuntimeError("piper worker did not start")
        return self._worker

    def _request(self, w: subprocess.Popen, text: str) -> tuple[int, str]:
        self._wid += 1
        out = str(self._tmp / f"{self._wid}.wav")
        w.stdin.write(json.dumps({"id": self._wid, "text": text, "out": out}) + "\n")
        w.stdin.flush()
        return self._wid, out

    def _await(self, w: subprocess.Popen, want: int) -> None:
        while True:
            line = w.stdout.readline()
            if not line:
                raise RuntimeError("piper worker exited")
            msg = json.loads(line)
            if msg.get("id", 0) < want:
                Path(msg.get("out", "")).unlink(missing_ok=True)  # stale (stopped earlier)
                continue
            if not msg.get("ok"):
                raise RuntimeError(msg.get("error", "synthesis failed"))
            return

    def _say_piper(self, gen: int, text: str) -> None:
        chunks = split_chunks(text)
        if not chunks:
            return
        w = self._ensure_worker()
        cur = self._request(w, chunks[0])
        for i in range(len(chunks)):
            self._await(w, cur[0])
            wav = cur[1]
            if i + 1 < len(chunks):
                cur = self._request(w, chunks[i + 1])  # synthesize next while this plays
            if gen != self._gen:
                Path(wav).unlink(missing_ok=True)
                return
            cmd = self._player_cmd(wav)
            try:
                self._run(cmd, gen)
            finally:
                Path(wav).unlink(missing_ok=True)
            if gen != self._gen:
                return
