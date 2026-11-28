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
    gui = not args.headless and (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    try:
        if gui:
            try:
                from .tray import TrayApp
            except Exception as e:  # missing GTK etc.
                logging.getLogger("cursor-speak").warning("GUI unavailable (%s); running headless", e)
                gui = False
        if gui:
            # Build the window before first-run voice setup so a main window
            # appears immediately (AppImageHub and the desktop launcher).
            app = TrayApp(
                ctl,
                force_window=args.window,
                start_hidden=args.background,
            )
            logging.getLogger("cursor-speak").info("UI mode: %s", app.mode)
            ctl.start()
            GLib_main_with_signals(ctl)
            app.run()
        else:
            ctl.start()
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


def _listen(args) -> int:
    from . import listen

    cfg = config.load_config()
    if args.check:
        return _listen_check(cfg)
    if args.stop:
        print("Stopped listening." if listen.stop_active() else "Not listening.")
        return 0
    _setup_logging(False)
    return listen.run(cfg, toggle=args.toggle, mode=args.mode, enter=args.enter,
                      wav=args.file, notify=not args.quiet)


def _listen_check(cfg: dict) -> int:
    from . import deliver, recorder, shortcut, stt

    model = cfg.get("stt_model", "base.en")
    rec = recorder.recorder_cmd()
    print(f"microphone recorder: {rec[0] if rec else 'MISSING (install pipewire-bin)'}")
    state = "ready" if stt.is_ready(model) else "setting up" if stt.setup_running() else "not set up (downloads on first use, or run: cursor-speak setup-stt)"
    print(f"speech model ({model}, local faster-whisper): {state}")
    print(f"output mode: {cfg.get('listen_mode', 'new')}   press Enter after typing: {cfg.get('listen_press_enter', False)}")
    print(f"cursor-agent: {deliver.agent_path(cfg)}")
    term = deliver.terminal_cmd(["cursor-agent"], "~")
    print(f"terminal for new sessions: {term[0] if term else 'MISSING'}")
    print(f"typing backend: {deliver.type_backend()}")
    steps = deliver.ydotool_help()
    if steps:
        print("\nTo type into windows with ydotool, run these yourself:")
        for s in steps:
            print(f"  {s}")
    try:
        sc = shortcut.status()
    except Exception as e:
        sc = None
        print(f"shortcut: unavailable ({e})")
    else:
        print(f"shortcut: {sc['binding']} -> {sc['command']}" if sc else "shortcut: none (cursor-speak shortcut install)")
    return 0


def _setup_stt(args) -> int:
    from . import stt

    cfg = config.load_config()
    model = args.model or cfg.get("stt_model", "base.en")
    try:
        stt.run(model, print)
    except Exception as e:
        print(f"Speech recognition setup failed: {e}", file=sys.stderr)
        return 1
    return 0


def _shortcut(args) -> int:
    from . import launcher, shortcut

    slots = {
        "listen": (shortcut.SLOT, shortcut.NAME, shortcut.CANDIDATES, "push-to-talk"),
        "converse": (shortcut.CONVERSE_SLOT, shortcut.CONVERSE_NAME, shortcut.CONVERSE_CANDIDATES, "conversation"),
    }
    which = ["listen", "converse"] if args.which == "both" else [args.which]
    rc = 0
    for w in which:
        slot, name, cands, label = slots[w]
        try:
            if args.action == "install":
                cmd = launcher.shortcut_command(args.command, w)
                accel = shortcut.install(cmd, args.key if len(which) == 1 else None, slot=slot, name=name,
                                         candidates=cands)
                print(f"{label}: {accel} runs {cmd}")
            elif args.action == "remove":
                print(f"{label}: " + ("removed" if shortcut.remove(slot=slot) else "was not set"))
            else:
                sc = shortcut.status(slot=slot)
                print(f"{label}: " + (f"{sc['binding']} -> {sc['command']}" if sc else "not set"))
        except RuntimeError as e:
            print(f"{label} shortcut: {e}", file=sys.stderr)
            rc = 1
    return rc


def _converse(args) -> int:
    import signal as _signal

    from . import converse

    if args.toggle and converse.signal_active(_signal.SIGUSR1):
        print("Paused or resumed the conversation.")
        return 0
    if args.stop:
        print("Ended the conversation." if converse.signal_active(_signal.SIGTERM) else "No conversation is running.")
        return 0
    if converse.active_pid():
        print("A conversation is already running (use --toggle to pause/resume, --stop to end).")
        return 0
    _setup_logging(args.verbose)
    from . import converse_ui

    cfg = config.load_config()
    for key, val in (("converse_barge_in", args.barge_in), ("converse_force", args.force or None),
                     ("converse_model", args.model), ("converse_echo_cancel", args.echo_cancel or None)):
        if val is not None:
            cfg[key] = val
    return converse_ui.run(cfg, workspace=args.workspace, resume=args.resume, new=args.new,
                           wake_word=("" if args.no_wake_word else args.wake_word), terminal=args.terminal)


def _autostart(args) -> int:
    if args.state == "on":
        autostart.set_enabled(True)
    elif args.state == "off":
        autostart.set_enabled(False)
    print("start at login: " + ("on" if autostart.is_enabled() else "off"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cursor-speak",
        description="Reads new Cursor CLI replies out loud. With no command, starts the app.",
    )
    p.add_argument("--version", action="version", version=f"cursor-speak {__version__}")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="start the app (default)")
    for sp in (p, r):
        sp.add_argument("--headless", action="store_true", help="no tray icon or window")
        sp.add_argument("--window", action="store_true", help="always show the main window")
        sp.add_argument(
            "--background",
            "--hidden",
            dest="background",
            action="store_true",
            help="start hidden (tray only, or no window if there is no tray); used at login",
        )
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
    li = sub.add_parser("listen", help="push-to-talk: record, transcribe locally, send to cursor-agent")
    li.add_argument("--toggle", action="store_true", help="stop a recording in progress, or start one (for a shortcut)")
    li.add_argument("--stop", action="store_true", help="stop a recording in progress")
    li.add_argument("--mode", choices=["new", "type", "clipboard", "print"],
                    help="new: open cursor-agent with the text; type: type into the focused window; "
                         "clipboard: copy; print: just print (default from config)")
    g = li.add_mutually_exclusive_group()
    g.add_argument("--enter", dest="enter", action="store_true", default=None, help="press Enter after typing")
    g.add_argument("--no-enter", dest="enter", action="store_false", help="don't press Enter after typing")
    li.add_argument("--file", help="transcribe this WAV instead of recording")
    li.add_argument("--check", action="store_true", help="show what voice input will use and what is missing")
    li.add_argument("-q", "--quiet", action="store_true", help="no desktop notifications")
    ss = sub.add_parser("setup-stt", help="download faster-whisper and the speech model now")
    ss.add_argument("--model", help="tiny.en, base.en, small.en, ... (default from config)")
    cv = sub.add_parser("converse", help="hands-free voice conversation with cursor-agent")
    cv.add_argument("--toggle", action="store_true", help="pause/resume a running conversation, or start one")
    cv.add_argument("--stop", action="store_true", help="end a running conversation")
    cv.add_argument("--workspace", help="folder the agent works in (default: config, else the current folder)")
    cv.add_argument("--resume", metavar="CHAT_ID", help="continue this cursor-agent chat")
    cv.add_argument("--new", action="store_true", help="start a new chat instead of continuing the last one here")
    wk = cv.add_mutually_exclusive_group()
    wk.add_argument("--wake-word", metavar="PHRASE", help='require a wake phrase, e.g. "hey cursor"')
    wk.add_argument("--no-wake-word", action="store_true", help="no wake phrase even if the config has one")
    cv.add_argument("--barge-in", choices=["on", "command", "off"], help="what talking over a reply does")
    cv.add_argument("--echo-cancel", action="store_true", help="use PipeWire echo cancellation (speakers)")
    cv.add_argument("--force", action="store_true", help="let the agent run commands without asking (cursor-agent --force)")
    cv.add_argument("--model", help="cursor-agent model")
    cv.add_argument("--terminal", action="store_true", help="no window; print the conversation here")
    cv.add_argument("-v", "--verbose", action="store_true")
    sc = sub.add_parser("shortcut", help="GNOME keyboard shortcuts (push-to-talk and conversation): install, remove or status")
    sc.add_argument("action", choices=["install", "remove", "status"], nargs="?", default="status")
    sc.add_argument("--which", choices=["listen", "converse", "both"], default="both")
    sc.add_argument("--key", help="e.g. '<Super><Alt>space' (default: first free of a few)")
    sc.add_argument("--command", help="launcher to run (default: this copy of cursor-speak)")
    a = sub.add_parser("autostart", help="start at login: on, off, or status")
    a.add_argument("state", choices=["on", "off", "status"], nargs="?", default="status")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    if cmd == "listen":
        return _listen(args)
    if cmd == "setup-stt":
        return _setup_stt(args)
    if cmd == "shortcut":
        return _shortcut(args)
    if cmd == "converse":
        return _converse(args)
    return 2
