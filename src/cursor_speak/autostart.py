"""Per-user 'start at login' toggle.

The .deb ships /etc/xdg/autostart/cursor-speak.desktop. Turning it off writes a
per-user override (Hidden=true) in ~/.config/autostart; turning it on removes it.
"""

from __future__ import annotations

from . import config

_OFF = """[Desktop Entry]
Type=Application
Name=Cursor Speak
Hidden=true
X-GNOME-Autostart-enabled=false
"""


def is_enabled() -> bool:
    f = config.AUTOSTART_FILE
    if not f.exists():
        return True
    try:
        text = f.read_text()
    except OSError:
        return True
    return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text


def set_enabled(on: bool) -> None:
    f = config.AUTOSTART_FILE
    if on:
        f.unlink(missing_ok=True)
    else:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_OFF)
