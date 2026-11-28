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
STT_VENV_DIR = DATA_DIR / "stt-venv"
STT_MODELS_DIR = DATA_DIR / "whisper-models"
STT_READY_MARKER = DATA_DIR / "stt-ready.json"
STT_SETUP_LOCK = DATA_DIR / "stt-setup.lock"


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
    # --- Voice input (push-to-talk, `cursor-speak listen`) ---
    # Local faster-whisper model: tiny.en, base.en (default), small.en (better, slower)
    "stt_model": "base.en",
    "stt_language": "en",
    # Where your words go: "new" opens a terminal running cursor-agent "<text>";
    # "type" types into the focused window (e.g. a running cursor-agent);
    # "clipboard" only copies
    "listen_mode": "new",
    # In "type" mode, press Enter after typing
    "listen_press_enter": False,
    # Folder a new cursor-agent starts in
    "agent_cwd": "~",
    "agent_command": "cursor-agent",
    # Stop reading replies out loud while you talk
    "pause_tts_while_listening": True,
    # Recording ends after this much quiet (seconds), or at listen_max_seconds
    "listen_silence_seconds": 1.5,
    "listen_no_speech_seconds": 8,
    "listen_max_seconds": 60,
    # Mic level (16-bit RMS) that counts as speech
    "listen_threshold": 500,
    "listen_notify": True,
    # --- Conversation mode (`cursor-speak converse`) ---
    # Folder the agent works in; empty = where the conversation was started
    "converse_workspace": "",
    # Say the wake word before each request (useful in noisy rooms); follow-ups
    # right after a reply don't need it
    "converse_wake_word_enabled": False,
    "converse_wake_word": "hey cursor",
    "converse_followup_seconds": 15,
    # Talking over a reply: "on" stops it at once, "command" lets it finish
    # (only voice commands act), "off" ignores the mic while speaking
    "converse_barge_in": "on",
    # How much louder than normal you must be to interrupt (speaker echo guard)
    "converse_barge_factor": 3.0,
    "converse_silence_seconds": 1.0,
    "converse_threshold": 500,
    # PipeWire echo cancellation (private, no root); best with speakers
    "converse_echo_cancel": False,
    "converse_sounds": True,
    # cursor-agent flags: --trust (headless can't answer the trust prompt),
    # --force (run commands without asking), --model, extra args
    "converse_trust": True,
    "converse_force": False,
    "converse_model": "",
    "converse_agent_args": [],
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


def save_config(updates: dict) -> None:
    """Merge `updates` into config.json (keeps keys the user added by hand)."""
    try:
        current = json.loads(CONFIG_FILE.read_text())
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    current.update(updates)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n")
    tmp.replace(CONFIG_FILE)
