"""First-run setup: per-user piper venv and voice model (never inside the .deb)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from . import config

HF_BASE = os.environ.get(
    "CURSOR_SPEAK_VOICE_BASE", "https://huggingface.co/rhasspy/piper-voices/resolve/main"
)
Progress = Callable[[str], None]

# Fail fast when offline or when no wheel exists (old Python, no network).
PIP_TIMEOUT_SEC = 15
PIP_RETRIES = 0
PIP_RUN_TIMEOUT_SEC = 60


def venv_python() -> Path:
    return config.VENV_DIR / "bin" / "python"


def voice_paths(voice: str) -> tuple[Path, Path]:
    return config.VOICES_DIR / f"{voice}.onnx", config.VOICES_DIR / f"{voice}.onnx.json"


def voice_url(voice: str, suffix: str) -> str:
    # en_US-lessac-medium -> en/en_US/lessac/medium/en_US-lessac-medium.onnx
    try:
        locale, name, quality = voice.split("-", 2)
    except ValueError as e:
        raise ValueError(f"unexpected voice name: {voice}") from e
    lang = locale.split("_")[0]
    return f"{HF_BASE}/{lang}/{locale}/{name}/{quality}/{voice}{suffix}"


def is_ready(voice: str) -> bool:
    onnx, js = voice_paths(voice)
    if not (venv_python().exists() and onnx.exists() and js.exists() and config.READY_MARKER.exists()):
        return False
    try:
        return json.loads(config.READY_MARKER.read_text()).get("voice") == voice
    except (OSError, ValueError):
        return False


def host_python() -> str:
    """Python used to create the per-user venv.

    Prefer a host interpreter so the venv still works after an AppImage remounts
    at a new path. Fall back to the running interpreter.
    """
    env = os.environ.get("CURSOR_SPEAK_HOST_PYTHON")
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return env
    appdir = os.environ.get("APPDIR")
    candidates = []
    which = shutil.which("python3")
    if which:
        candidates.append(which)
    candidates.append(sys.executable)
    for cand in candidates:
        if not cand:
            continue
        real = os.path.realpath(cand)
        if appdir and (real == os.path.realpath(appdir) or real.startswith(os.path.realpath(appdir) + os.sep)):
            continue
        if Path(cand).is_file():
            return cand
    return sys.executable


def pip_install_cmd(py: Path | str) -> list[str]:
    return [
        str(py),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--retries",
        str(PIP_RETRIES),
        "--timeout",
        str(PIP_TIMEOUT_SEC),
        "piper-tts",
    ]


def _download(url: str, dest: Path, progress: Progress) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "cursor-speak"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = last = 0
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            done += len(block)
            if total and done - last > 8 << 20:
                last = done
                progress(f"Downloading voice {done * 100 // total}%")
    part.rename(dest)


def run(voice: str, progress: Progress = print) -> None:
    """Idempotent. Raises on failure."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    py = venv_python()
    if not py.exists():
        progress("Creating Python environment")
        subprocess.run(
            [host_python(), "-m", "venv", str(config.VENV_DIR)],
            check=True,
            timeout=PIP_RUN_TIMEOUT_SEC,
        )
    have = subprocess.run([str(py), "-c", "import piper"], capture_output=True).returncode == 0
    if not have:
        progress("Installing piper (a one-time download)")
        subprocess.run(
            pip_install_cmd(py),
            check=True,
            timeout=PIP_RUN_TIMEOUT_SEC,
        )
    onnx, js = voice_paths(voice)
    if not js.exists():
        _download(voice_url(voice, ".onnx.json"), js, progress)
    if not onnx.exists():
        progress("Downloading voice")
        _download(voice_url(voice, ".onnx"), onnx, progress)
    progress("Testing voice")
    worker = Path(__file__).with_name("piper_worker.py")
    subprocess.run([str(py), str(worker), "--selftest", str(onnx)], check=True, capture_output=True, timeout=60)
    config.READY_MARKER.write_text(json.dumps({"voice": voice}))
    progress("Voice ready")
