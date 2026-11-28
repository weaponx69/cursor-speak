"""Send recognised text somewhere: a new cursor-agent terminal, or type it into
the focused window (ydotool, then wtype, then the clipboard)."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from . import launcher

log = logging.getLogger("cursor-speak.deliver")

MODES = ("new", "type", "clipboard", "print")
Which = Callable[[str], "str | None"]


# helpers --------------------------------------------------------------------------
def agent_path(cfg: dict, which: Which = shutil.which) -> str:
    cmd = cfg.get("agent_command") or "cursor-agent"
    if os.sep in cmd:
        return cmd
    found = which(cmd)
    if found:
        return found
    # Shortcuts run with a minimal PATH that often lacks ~/.local/bin.
    for d in (Path.home() / ".local" / "bin", Path("/usr/local/bin")):
        if (d / cmd).is_file():
            return str(d / cmd)
    return cmd


def terminal_cmd(argv: list[str], cwd: str, which: Which = shutil.which) -> list[str] | None:
    """Command that opens a new terminal window running argv in cwd."""
    if which("ptyxis"):
        return ["ptyxis", "--new-window", "-d", cwd, "--", *argv]
    if which("gnome-terminal"):
        return ["gnome-terminal", f"--working-directory={cwd}", "--", *argv]
    if which("kgx"):
        return ["kgx", f"--working-directory={cwd}", "-e", shlex.join(argv)]
    if which("x-terminal-emulator"):
        return ["x-terminal-emulator", "-e", shlex.join(argv)]
    return None


def ydotool_socket() -> Path:
    env = os.environ.get("YDOTOOL_SOCKET")
    if env:
        return Path(env)
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / ".ydotool_socket"


def ydotool_ready(which: Which = shutil.which) -> bool:
    return bool(which("ydotool")) and ydotool_socket().exists()


def type_backend(which: Which = shutil.which, desktop: str | None = None) -> str:
    """'ydotool', 'wtype' or 'clipboard'. wtype needs the virtual-keyboard
    protocol, which GNOME does not offer, so it is skipped there."""
    if ydotool_ready(which):
        return "ydotool"
    desktop = (desktop if desktop is not None else os.environ.get("XDG_CURRENT_DESKTOP", "")).lower()
    if which("wtype") and "gnome" not in desktop:
        return "wtype"
    return "clipboard"


def ydotool_help(which: Which = shutil.which) -> list[str]:
    """Commands Brian runs himself (sudo) to enable typing with ydotool. Empty when ready."""
    steps: list[str] = []
    if not which("ydotool"):
        steps.append("sudo apt install ydotool")
    if not os.access("/dev/uinput", os.R_OK | os.W_OK):
        steps.append("sudo usermod -aG input $USER    # the ydotool package's udev rule gives the input group /dev/uinput; log out and back in")
    if not ydotool_socket().exists():
        steps.append("systemctl --user enable --now ydotool    # starts ydotoold (no sudo)")
    return steps


# delivery -------------------------------------------------------------------------
class Result:
    def __init__(self, ok: bool, how: str, note: str = ""):
        self.ok, self.how, self.note = ok, how, note

    def __repr__(self) -> str:
        return f"Result({self.ok}, {self.how!r}, {self.note!r})"


def open_agent(text: str, cfg: dict, run=subprocess.Popen, which: Which = shutil.which) -> Result:
    cwd = os.path.expanduser(cfg.get("agent_cwd") or "~")
    argv = [agent_path(cfg, which), text]
    cmd = terminal_cmd(argv, cwd, which)
    if not cmd:
        return Result(False, "new", "no terminal found (gnome-terminal, ptyxis or kgx)")
    run(cmd, cwd=cwd, env=launcher.host_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    return Result(True, "new", f"opened {cmd[0]} with cursor-agent")


def copy_to_clipboard(text: str, run=subprocess.run, which: Which = shutil.which) -> bool:
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if which(cmd[0]):
            try:
                run(cmd, input=text, text=True, check=True, timeout=5, env=launcher.host_env())
                return True
            except (subprocess.SubprocessError, OSError):
                continue
    return False


def type_text(text: str, enter: bool, run=subprocess.run, which: Which = shutil.which,
              desktop: str | None = None, settle: float = 0.4, sleep=time.sleep) -> Result:
    backend = type_backend(which, desktop)
    # Let go of the shortcut's modifier keys first, or Super/Alt would combine with the text.
    sleep(settle)
    if backend == "ydotool":
        try:
            run(["ydotool", "type", "--file", "-"], input=text, text=True, check=True, timeout=60, env=launcher.host_env())
            if enter:
                run(["ydotool", "key", "28:1", "28:0"], check=True, timeout=5, env=launcher.host_env())
            return Result(True, "ydotool")
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("ydotool failed: %s", e)
    if backend == "wtype":
        try:
            run(["wtype", "--", text], check=True, timeout=60, env=launcher.host_env())
            if enter:
                run(["wtype", "-k", "Return"], check=True, timeout=5, env=launcher.host_env())
            return Result(True, "wtype")
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("wtype failed: %s", e)
    if copy_to_clipboard(text, run, which):
        return Result(True, "clipboard", "Copied. Paste with Ctrl+Shift+V in a terminal.")
    return Result(False, "clipboard", "no way to type or copy (install ydotool or wl-clipboard)")


def deliver(text: str, cfg: dict, mode: str | None = None, enter: bool | None = None, **kw) -> Result:
    mode = mode or cfg.get("listen_mode", "new")
    enter = cfg.get("listen_press_enter", False) if enter is None else enter
    if mode == "print":
        print(text)
        return Result(True, "print")
    if mode == "new":
        return open_agent(text, cfg, **{k: v for k, v in kw.items() if k in ("run", "which")})
    if mode == "clipboard":
        ok = copy_to_clipboard(text, **{k: v for k, v in kw.items() if k in ("run", "which")})
        return Result(ok, "clipboard", "Copied. Paste with Ctrl+Shift+V in a terminal." if ok else "no clipboard tool")
    if mode == "type":
        return type_text(text, enter, **kw)
    raise ValueError(f"unknown mode {mode!r}")
