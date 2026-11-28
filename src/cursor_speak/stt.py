"""Local speech-to-text (faster-whisper) in its own per-user venv.

Nothing is sent to the network except the one-time pip install and model download.
The venv lives next to the piper one in ~/.local/share/cursor-speak so neither
package set can break the other.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from . import config, launcher, setup_voice

Progress = Callable[[str], None]

MODELS = ("tiny.en", "base.en", "small.en", "medium.en", "tiny", "base", "small", "medium")
PIP_RUN_TIMEOUT_SEC = 900  # faster-whisper and its wheels are ~150 MB
DOWNLOAD_TIMEOUT_SEC = 1800
TRANSCRIBE_TIMEOUT_SEC = 300


def venv_python() -> Path:
    return config.STT_VENV_DIR / "bin" / "python"


def model_dir(model: str) -> Path:
    return config.STT_MODELS_DIR / model


def worker_path() -> Path:
    return Path(__file__).with_name("stt_worker.py")


def is_ready(model: str) -> bool:
    if not (venv_python().exists() and (model_dir(model) / "model.bin").exists()):
        return False
    try:
        return json.loads(config.STT_READY_MARKER.read_text()).get("model") == model
    except (OSError, ValueError):
        return False


def setup_running() -> bool:
    """True while another process holds the setup lock."""
    try:
        pid = int(config.STT_SETUP_LOCK.read_text().strip())
    except (OSError, ValueError):
        return False
    return _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run(model: str, progress: Progress = print) -> None:
    """Idempotent, blocking. Raises on failure."""
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; pick one of {', '.join(MODELS)}")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.STT_SETUP_LOCK.write_text(str(os.getpid()))
    try:
        py = venv_python()
        if not py.exists():
            progress("Creating speech recognition environment")
            subprocess.run(
                [setup_voice.host_python(), "-m", "venv", str(config.STT_VENV_DIR)],
                check=True, timeout=120, env=launcher.host_env(),
            )
        have = subprocess.run([str(py), "-c", "import faster_whisper"], capture_output=True, env=launcher.host_env()).returncode == 0
        if not have:
            progress("Installing faster-whisper (a one-time download, about 150 MB)")
            subprocess.run(
                [str(py), "-m", "pip", "install", "--disable-pip-version-check",
                 "--timeout", "30", "faster-whisper"],
                check=True, timeout=PIP_RUN_TIMEOUT_SEC, env=launcher.host_env(),
            )
        dest = model_dir(model)
        if not (dest / "model.bin").exists():
            progress(f"Downloading the {model} speech model")
            subprocess.run(
                [str(py), str(worker_path()), "--download", model, str(dest)],
                check=True, timeout=DOWNLOAD_TIMEOUT_SEC, env=launcher.host_env(),
            )
        config.STT_READY_MARKER.write_text(json.dumps({"model": model}))
        progress("Speech recognition ready")
    finally:
        try:
            config.STT_SETUP_LOCK.unlink()
        except OSError:
            pass


def start_background_setup(model: str) -> bool:
    """Start `cursor-speak setup-stt` detached. False if one is already running."""
    if setup_running():
        return False
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(config.STATE_DIR / "stt-setup.log", "ab")
    subprocess.Popen(
        launcher.self_command() + ["setup-stt", "--model", model],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
        env=os.environ.copy(),
    )
    return True


def transcribe(wav: Path | str, model: str, language: str = "en") -> str:
    """Blocking. Returns the recognised text ('' for silence)."""
    res = subprocess.run(
        [str(venv_python()), str(worker_path()), "--transcribe", str(model_dir(model)), str(wav), language],
        capture_output=True, text=True, timeout=TRANSCRIBE_TIMEOUT_SEC, env=launcher.host_env(),
    )
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "transcription failed").strip().splitlines()[-1])
    line = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "{}"
    return (json.loads(line).get("text") or "").strip()
