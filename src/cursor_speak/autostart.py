"""Per-user 'start at login' toggle.

Two cases:
- Installed from the .deb: /etc/xdg/autostart/cursor-speak.desktop starts it at
  login for everyone. Turning it off writes a per-user override (Hidden=true) in
  ~/.config/autostart; turning it on removes the override.
- No system entry (AppImage, source checkout): off by default. Turning it on writes
  ~/.config/autostart/cursor-speak.desktop that launches this AppImage (or
  `cursor-speak`); turning it off removes that file.

Login entries always pass --background so a window does not pop up at login.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config

SYSTEM_FILE = Path("/etc/xdg/autostart/cursor-speak.desktop")

_OFF = """[Desktop Entry]
Type=Application
Name=Cursor Speak
Hidden=true
X-GNOME-Autostart-enabled=false
"""

_ON = """[Desktop Entry]
Type=Application
Name=Cursor Speak
Comment=Reads new Cursor CLI replies out loud
Exec={exec_line}
Icon=cursor-speak
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
NoDisplay=true
"""


def _exec_line() -> str:
    appimage = os.environ.get("APPIMAGE")
    cmd = f'"{appimage}"' if appimage else "cursor-speak"
    return f"{cmd} --background"


def _hidden(text: str) -> bool:
    return "Hidden=true" in text or "X-GNOME-Autostart-enabled=false" in text


def is_enabled() -> bool:
    f = config.AUTOSTART_FILE
    try:
        text = f.read_text() if f.exists() else None
    except OSError:
        text = None
    if SYSTEM_FILE.exists():
        return text is None or not _hidden(text)
    return text is not None and not _hidden(text)


def set_enabled(on: bool) -> None:
    f = config.AUTOSTART_FILE
    if SYSTEM_FILE.exists():
        if on:
            f.unlink(missing_ok=True)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(_OFF)
        return
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_ON.format(exec_line=_exec_line()))
    else:
        f.unlink(missing_ok=True)
