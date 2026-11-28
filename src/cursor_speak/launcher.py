"""How to start this same copy of Cursor Speak again (deb, AppImage or source tree)."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path


def self_command() -> list[str]:
    """argv prefix that runs this copy of cursor-speak in a new process."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).is_file():
        return [appimage]
    return [sys.executable, "-m", "cursor_speak"]


def launcher_path() -> str:
    """A stable executable for desktop shortcuts (they run without our env)."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).is_file():
        return appimage
    src = Path(__file__).resolve().parent.parent  # .../src or /usr/lib/cursor-speak
    dev = src.parent / "bin" / "cursor-speak-dev"
    if src.name == "src" and dev.is_file():
        return str(dev)
    return shutil.which("cursor-speak") or "/usr/bin/cursor-speak"


def shortcut_command(launcher: str | None = None, sub: str = "listen") -> str:
    return f"{shlex.quote(launcher or launcher_path())} {sub} --toggle"


def host_env(environ: dict | None = None) -> dict:
    """Environment for host programs (terminals, gsettings, the STT venv).

    Inside an AppImage, PYTHONHOME, LD_LIBRARY_PATH, GSETTINGS_SCHEMA_DIR and
    friends point into the bundle and break host binaries, so every path entry
    under $APPDIR is dropped (and a variable left empty is removed).
    """
    env = dict(os.environ if environ is None else environ)
    appdir = env.get("APPDIR")
    if not appdir:
        return env
    root = os.path.realpath(appdir)
    out = {}
    for k, v in env.items():
        if k in ("APPDIR", "PYTHONDONTWRITEBYTECODE"):
            continue
        parts = v.split(os.pathsep)
        kept = [p for p in parts if not (p == appdir or p.startswith(appdir.rstrip("/") + "/")
                                         or p == root or p.startswith(root + "/"))]
        if len(kept) == len(parts):
            out[k] = v
        elif kept:
            out[k] = os.pathsep.join(kept)
    return out
