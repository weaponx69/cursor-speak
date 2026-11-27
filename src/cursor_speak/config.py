"""Paths and user configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import APP_ID


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / APP_ID
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / APP_ID
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / APP_ID
AUTOSTART_FILE = _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / f"{APP_ID}.desktop"
CHATS_ROOT = Path(os.environ.get("CURSOR_SPEAK_CHATS") or Path.home() / ".cursor" / "chats")

VENV_DIR = DATA_DIR / "venv"
VOICES_DIR = DATA_DIR / "voices"
READY_MARKER = DATA_DIR / "piper-ready.json"
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "cursor-speak.log"
CONFIG_FILE = CONFIG_DIR / "config.json"


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/cursor-speak-{os.getuid()}"
    Path(runtime).mkdir(parents=True, exist_ok=True)
    return Path(runtime) / f"{APP_ID}.sock"


DEFAULTS = {
    # Piper voice name, e.g. en_US-lessac-medium, en_US-ryan-high, en_GB-alba-medium
    "voice": "en_US-lessac-medium",
    # 1.0 is normal speed, 1.2 is faster
    "speed": 1.0,
    # 0.0 to 1.0 (pw-play volume)
    "volume": 1.0,
    # How code-ish names in `backticks` are read: "speak" (as words) or "drop"
    "identifiers": "speak",
    # Longest reply to read in full; longer ones are cut at a sentence
    "max_chars": 2500,
    # Seconds between checks for chat changes
    "poll_seconds": 2.0,
    # Seconds a chat must be quiet before its last message counts as final
    "quiet_seconds": 3.0,
    # Download piper and the voice on first run
    "auto_setup": True,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        pass
    return cfg
