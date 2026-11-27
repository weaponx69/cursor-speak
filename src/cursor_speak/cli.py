"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

from . import __version__, autostart, chatstore, config, control, setup_voice, textclean


def _setup_logging(verbose: bool) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(config.LOG_FILE, maxBytes=500_000, backupCount=2)
    ]
    if verbose or sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _client(cmd: str) -> int:
    reply = control.send(cmd)
    if reply is None:
        print("Cursor Speak is not running. Start it with: cursor-speak", file=sys.stderr)
        return 1
    print(reply)
    return 0


def _run(args) -> int:
    _setup_logging(args.verbose)
    if control.send("ping") == "pong":
        print("Cursor Speak is already running.")
        return 0
    from .controller import Controller

    cfg = config.load_config()
    ctl = Controller(cfg)
    server = control.Server(ctl)
    server.start()
    ctl.start()
    gui = not args.headless and (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    try:
        if gui:
            try:
                from .tray import TrayApp
            except Exception as e:  # missing GTK etc.
                logging.getLogger("cursor-speak").warning("GUI unavailable (%s); running headless", e)
                gui = False
        if gui:
            app = TrayApp(ctl, force_window=args.window)
            logging.getLogger("cursor-speak").info("UI mode: %s", app.mode)
            GLib_main_with_signals(ctl)
            app.run()
        else:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: ctl.request_quit())
            ctl.quit_event.wait()
    finally:
        server.close()
        ctl.shutdown()
    return 0


def GLib_main_with_signals(ctl) -> None:
    from gi.repository import GLib

    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (ctl.request_quit(), True)[1])


def _latest(args) -> int:
    cfg = config.load_config()
    dirs = chatstore.chat_dirs(config.CHATS_ROOT)
    if args.chat:
        dirs = [Path(args.chat)]
    if not dirs:
        print("No Cursor chats found.", file=sys.stderr)
        return 1
    d = max(dirs, key=chatstore.last_update)
    turn = chatstore.inspect_chat(d)
    print(f"chat: {d}\nstatus: {turn.status}")
    if turn.reply:
        print(f"reply id: {turn.reply.id}\nprompt: {turn.reply.prompt}\n")
        print("--- spoken text ---")
        print(textclean.clean(turn.reply.text, cfg["identifiers"], int(cfg["max_chars"])))
    return 0


def _setup(args) -> int:
    cfg = config.load_config()
    try:
        setup_voice.run(cfg["voice"], print)
    except Exception as e:
        print(f"Setup failed: {e}", file=sys.stderr)
        return 1
    return 0


def _autostart(args) -> int:
    if args.state == "on":
        autostart.set_enabled(True)
    elif args.state == "off":
        autostart.set_enabled(False)
    print("start at login: " + ("on" if autostart.is_enabled() else "off"))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cursor-speak",
        description="Reads new Cursor CLI replies out loud. With no command, starts the app.",
    )
    p.add_argument("--version", action="version", version=f"cursor-speak {__version__}")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="start the app (default)")
    for sp in (p, r):
        sp.add_argument("--headless", action="store_true", help="no tray icon or window")
        sp.add_argument("--window", action="store_true", help="use a small window instead of the tray icon")
        sp.add_argument("-v", "--verbose", action="store_true", help="log to the terminal")
    for name, text in (
        ("pause", "stop reading new replies until resumed"), ("resume", "start reading new replies again"),
        ("toggle", "pause or resume"), ("stop", "stop the current speech"),
        ("replay", "read the last reply again"), ("status", "show app status as JSON"),
        ("test", "speak a short test phrase"), ("quit", "quit the running app"),
    ):
        sub.add_parser(name, help=text)
    say = sub.add_parser("say", help="speak some text")
    say.add_argument("text", nargs="+")
    latest = sub.add_parser("latest", help="show what would be read for the newest reply (no speech)")
    latest.add_argument("--chat", help="a specific chat directory")
    sub.add_parser("setup", help="download piper and the voice now (otherwise done on first run)")
    a = sub.add_parser("autostart", help="start at login: on, off, or status")
    a.add_argument("state", choices=["on", "off", "status"], nargs="?", default="status")

    args = p.parse_args(argv)
    cmd = args.cmd or "run"
    if cmd == "run":
        return _run(args)
    if cmd in ("pause", "resume", "toggle", "stop", "replay", "status", "test", "quit"):
        return _client(cmd)
    if cmd == "say":
        return _client("say " + " ".join(args.text))
    if cmd == "latest":
        return _latest(args)
    if cmd == "setup":
        return _setup(args)
    if cmd == "autostart":
        return _autostart(args)
    return 2
