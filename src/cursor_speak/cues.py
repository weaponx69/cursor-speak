"""Short, quiet audio cues for conversation states (generated, not shipped)."""

from __future__ import annotations

import array
import math
import shutil
import subprocess
import wave
from pathlib import Path

from . import config, launcher

RATE = 22050
# name: list of (frequency Hz, seconds); 0 Hz is a gap
CUES = {
    "listening": [(660, 0.07), (0, 0.03), (880, 0.09)],   # rising: I'm listening
    "thinking": [(520, 0.08)],                            # one soft blip: sent to the agent
    "paused": [(880, 0.07), (0, 0.03), (590, 0.09)],      # falling: not listening
    "error": [(330, 0.12), (0, 0.05), (330, 0.12)],
    "goodbye": [(880, 0.07), (660, 0.07), (440, 0.12)],
}


def tone_pcm(parts: list[tuple[float, float]], volume: float = 0.25) -> bytes:
    out = array.array("h")
    for freq, secs in parts:
        n = int(RATE * secs)
        fade = max(1, int(RATE * 0.01))
        for i in range(n):
            if freq <= 0:
                out.append(0)
                continue
            env = min(1.0, i / fade, (n - 1 - i) / fade)
            out.append(int(32767 * volume * env * math.sin(2 * math.pi * freq * i / RATE)))
    return out.tobytes()


def cue_file(name: str) -> Path:
    d = config.socket_path().parent / "cursor-speak-cues"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.wav"
    if not path.exists():
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(tone_pcm(CUES[name]))
    return path


def player(path: Path, cfg: dict) -> list[str] | None:
    vol = float(cfg.get("volume", 1.0))
    if shutil.which("pw-play"):
        target = cfg.get("playback_target")
        return ["pw-play", "--volume", str(vol), *(["--target", target] if target else []), str(path)]
    if shutil.which("paplay"):
        return ["paplay", str(path)]
    if shutil.which("aplay"):
        return ["aplay", "-q", str(path)]
    return None


def play(name: str, cfg: dict, popen=subprocess.Popen) -> bool:
    if not cfg.get("converse_sounds", True) or name not in CUES:
        return False
    cmd = player(cue_file(name), cfg)
    if not cmd:
        return False
    try:
        popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=launcher.host_env())
    except OSError:
        return False
    return True
