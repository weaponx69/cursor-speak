"""Register a GNOME custom keyboard shortcut for push-to-talk (no root needed)."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from typing import Callable

from . import launcher

MEDIA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM = MEDIA + ".custom-keybinding"
BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
NAME = "Cursor Speak: talk to Cursor"
SLOT = "cursor-speak-listen"
CANDIDATES = ["<Super><Alt>space", "<Super><Control>space", "<Super><Alt>v", "<Super><Shift>v", "<Control><Alt>space"]
Runner = Callable[..., subprocess.CompletedProcess]


def _gs(run: Runner, *args: str) -> str:
    res = run(["gsettings", *args], capture_output=True, text=True, timeout=10, env=launcher.host_env())
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "gsettings failed").strip())
    return res.stdout.strip()


def parse_strv(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("@as"):
        value = value[3:].strip()
    try:
        out = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return []
    if isinstance(out, str):
        return [out] if out else []
    if isinstance(out, (list, tuple)):
        return [str(x) for x in out]
    return []


_MODS = re.compile(r"<([^>]+)>")


def normalize(accel: str) -> tuple[frozenset, str]:
    """'<Alt><Super>Space' and '<Super><Alt>space' compare equal."""
    mods = frozenset(m.lower().replace("primary", "control").replace("ctrl", "control") for m in _MODS.findall(accel))
    key = _MODS.sub("", accel).lower()
    return mods, key


def used_bindings(run: Runner = subprocess.run, skip_path: str | None = None) -> dict:
    """{normalized accel: 'schema key'} for everything bound now, except our own slot."""
    used: dict = {}
    # Every schema, so extension and IBus hotkeys count too.
    try:
        listing = _gs(run, "list-recursively")
    except RuntimeError:
        listing = ""
    if listing:
        for line in listing.splitlines():
            parts = line.split(" ", 2)
            if len(parts) < 3:
                continue
            for v in parse_strv(parts[2]):
                if "<" in v:  # ours always has modifiers, so plain keys cannot clash
                    used[normalize(v)] = f"{parts[0]} {parts[1]}"
    for path in parse_strv(_gs(run, "get", MEDIA, "custom-keybindings")):
        if path == skip_path:
            continue
        try:
            b = parse_strv(_gs(run, "get", f"{CUSTOM}:{path}", "binding"))
            name = parse_strv(_gs(run, "get", f"{CUSTOM}:{path}", "name"))
        except RuntimeError:
            continue
        for v in b:
            used[normalize(v)] = f"custom shortcut {name[0] if name else path}"
    return used


def pick_free(used: dict, preferred: str | None = None, candidates: list[str] | None = None) -> str | None:
    for accel in ([preferred] if preferred else []) + (candidates or CANDIDATES):
        if normalize(accel) not in used:
            return accel
    return None


CONVERSE_SLOT = "cursor-speak-converse"
CONVERSE_NAME = "Cursor Speak: conversation on/off"
CONVERSE_CANDIDATES = ["<Super><Alt>c", "<Super><Control>c", "<Super><Alt>t", "<Super><Shift>c"]


def _path(slot: str = SLOT) -> str:
    return f"{BASE}{slot}/"


def status(run: Runner = subprocess.run, slot: str = SLOT) -> dict | None:
    paths = parse_strv(_gs(run, "get", MEDIA, "custom-keybindings"))
    if _path(slot) not in paths:
        return None
    rel = f"{CUSTOM}:{_path(slot)}"
    return {
        "binding": (parse_strv(_gs(run, "get", rel, "binding")) or [""])[0],
        "command": (parse_strv(_gs(run, "get", rel, "command")) or [""])[0],
    }


def install(command: str, preferred: str | None = None, run: Runner = subprocess.run,
            slot: str = SLOT, name: str = NAME, candidates: list[str] | None = None) -> str:
    """Create or update our slot with a conflict-free binding. Returns the binding."""
    if not shutil.which("gsettings") and run is subprocess.run:
        raise RuntimeError("gsettings not found (GNOME only)")
    used = used_bindings(run, skip_path=_path(slot))
    current = status(run, slot)
    if not preferred and current and current.get("binding") and normalize(current["binding"]) not in used:
        preferred = current["binding"]  # reinstalling keeps the key
    accel = pick_free(used, preferred, candidates)
    if not accel:
        raise RuntimeError("every candidate shortcut is taken; pass --key")
    if preferred and normalize(preferred) in used:
        raise RuntimeError(f"{preferred} is already used by {used[normalize(preferred)]}")
    rel = f"{CUSTOM}:{_path(slot)}"
    _gs(run, "set", rel, "name", name)
    _gs(run, "set", rel, "command", command)
    _gs(run, "set", rel, "binding", accel)
    paths = parse_strv(_gs(run, "get", MEDIA, "custom-keybindings"))
    if _path(slot) not in paths:
        _gs(run, "set", MEDIA, "custom-keybindings", str(paths + [_path(slot)]))
    return accel


def install_converse(command: str, preferred: str | None = None, run: Runner = subprocess.run) -> str:
    return install(command, preferred, run, slot=CONVERSE_SLOT, name=CONVERSE_NAME,
                   candidates=CONVERSE_CANDIDATES)


def remove(run: Runner = subprocess.run, slot: str = SLOT) -> bool:
    paths = parse_strv(_gs(run, "get", MEDIA, "custom-keybindings"))
    if _path(slot) not in paths:
        return False
    rest = [p for p in paths if p != _path(slot)]
    _gs(run, "set", MEDIA, "custom-keybindings", str(rest) if rest else "@as []")
    for key in ("name", "command", "binding"):
        try:
            _gs(run, "reset", f"{CUSTOM}:{_path(slot)}", key)
        except RuntimeError:
            pass
    return True
