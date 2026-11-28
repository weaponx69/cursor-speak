"""Front ends for conversation mode: a GTK window, or plain terminal output."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from . import APP_ID, converse, listen, stt

log = logging.getLogger("cursor-speak.converse-ui")

STATE_TEXT = {
    "starting": "Starting…",
    "listening": "Listening — just talk",
    "standby": "Standing by — say \u201cresume\u201d",
    "hearing": "Hearing you…",
    "transcribing": "Got it…",
    "thinking": "Agent is working…",
    "speaking": "Speaking — talk to interrupt",
    "paused": "Paused — mic off",
    "ended": "Conversation ended",
}
STATE_COLOR = {"listening": "#22c55e", "hearing": "#ef4444", "transcribing": "#f97316",
               "thinking": "#3b82f6", "speaking": "#a855f7", "paused": "#9ca3af",
               "standby": "#eab308", "ended": "#6b7280", "starting": "#6b7280"}
PREFIX = {"you": "You", "agent": "Agent", "tool": "  ·", "info": "  ·", "error": "  !"}


class TerminalUI(converse.UI):
    def __init__(self, out=print):
        self.out = out
        self.done = threading.Event()

    def state(self, state: str, detail: str = "") -> None:
        self.out(f"[{STATE_TEXT.get(state, state)}]" + (f" {detail}" if detail else ""))

    def line(self, kind: str, text: str) -> None:
        self.out(f"{PREFIX.get(kind, kind)}: {text}" if kind in ("you", "agent") else f"{PREFIX.get(kind, '')} {text}")

    def ended(self) -> None:
        self.done.set()


class Notifying(converse.UI):
    """Wraps a UI and adds desktop notifications for the big changes only."""

    def __init__(self, inner: converse.UI, enabled: bool):
        self.inner = inner
        self.note = listen.Notifier(enabled)
        self.last = None

    def state(self, state: str, detail: str = "") -> None:
        if state in ("paused", "standby", "ended") or (state == "listening" and self.last in (None, "starting", "paused", "standby")):
            self.note.show("Cursor Speak conversation", STATE_TEXT.get(state, state))
        self.last = state
        self.inner.state(state, detail)

    def line(self, kind: str, text: str) -> None:
        if kind == "error":
            self.note.show("Cursor Speak conversation", text, urgent=True)
        self.inner.line(kind, text)

    def ended(self) -> None:
        self.inner.ended()


def _gtk_window(conv_ref: dict, workspace: str):
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk, Pango

    class Window(converse.UI):
        def __init__(self):
            self.w = Gtk.Window(title="Cursor Speak — Conversation")
            self.w.set_default_size(560, 640)
            self.w.set_icon_name(APP_ID)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
            self.status = Gtk.Label(xalign=0)
            self.status.set_use_markup(True)
            box.pack_start(self.status, False, False, 0)
            row = Gtk.Box(spacing=6)
            row.pack_start(Gtk.Label(label="Folder:"), False, False, 0)
            self.folder = Gtk.FileChooserButton(title="Workspace folder", action=Gtk.FileChooserAction.SELECT_FOLDER)
            self.folder.set_filename(workspace)
            self.folder.connect("file-set", self._folder_changed)
            row.pack_start(self.folder, True, True, 0)
            box.pack_start(row, False, False, 0)
            self.buf = Gtk.TextBuffer()
            for name, props in (("you", {"weight": Pango.Weight.BOLD, "foreground": "#2563eb"}),
                                ("agent", {}),
                                ("tool", {"family": "monospace", "foreground": "#6b7280", "scale": 0.9}),
                                ("info", {"style": Pango.Style.ITALIC, "foreground": "#6b7280"}),
                                ("error", {"foreground": "#dc2626"})):
                self.buf.create_tag(name, **props)
            self.view = Gtk.TextView(buffer=self.buf, editable=False, cursor_visible=False,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6)
            sc = Gtk.ScrolledWindow()
            sc.add(self.view)
            box.pack_start(sc, True, True, 0)
            entry_row = Gtk.Box(spacing=6)
            self.entry = Gtk.Entry(placeholder_text="Or type a message…")
            self.entry.connect("activate", self._typed)
            entry_row.pack_start(self.entry, True, True, 0)
            send = Gtk.Button(label="Send")
            send.connect("clicked", self._typed)
            entry_row.pack_start(send, False, False, 0)
            box.pack_start(entry_row, False, False, 0)
            btns = Gtk.Box(spacing=6)
            self.pause_btn = Gtk.Button(label="Pause listening")
            self.pause_btn.connect("clicked", lambda _b: self._conv(lambda c: c.toggle_pause()))
            stop = Gtk.Button(label="Stop")
            stop.set_tooltip_text("Stop speaking and cancel the agent's current turn")
            stop.connect("clicked", lambda _b: self._conv(lambda c: c.stop()))
            new = Gtk.Button(label="New conversation")
            new.connect("clicked", lambda _b: self._conv(lambda c: threading.Thread(target=c.new_conversation, daemon=True).start()))
            end = Gtk.Button(label="End")
            end.connect("clicked", lambda _b: self._conv(lambda c: c.end(say_goodbye=True)))
            for b in (self.pause_btn, stop, new, end):
                btns.pack_start(b, True, True, 0)
            box.pack_start(btns, False, False, 0)
            hint = Gtk.Label(xalign=0, wrap=True)
            hint.set_markup("<small>Say <b>stop</b>, <b>pause listening</b>, <b>resume</b>, "
                            "<b>new conversation</b> or <b>goodbye</b>. Talk over a reply to interrupt it.</small>")
            box.pack_start(hint, False, False, 0)
            self.w.add(box)
            self.w.connect("delete-event", self._close)
            self.w.show_all()
            self.state("starting")

        def _conv(self, fn):
            c = conv_ref.get("conv")
            if c:
                fn(c)

        def _typed(self, *_):
            text = self.entry.get_text().strip()
            if text:
                self.entry.set_text("")
                self._conv(lambda c: c.handle_text(text))

        def _folder_changed(self, chooser):
            path = chooser.get_filename()
            c = conv_ref.get("conv")
            if path and c and path != c.workspace:
                c.workspace = path
                threading.Thread(target=c.new_conversation, daemon=True).start()

        def _close(self, *_):
            self._conv(lambda c: c.end())
            Gtk.main_quit()
            return False

        # UI interface (any thread) -------------------------------------------------
        def state(self, state: str, detail: str = "") -> None:
            GLib.idle_add(self._state, state, detail)

        def _state(self, state, detail):
            color = STATE_COLOR.get(state, "#6b7280")
            text = GLib.markup_escape_text(STATE_TEXT.get(state, state) + (f" {detail}" if detail else ""))
            self.status.set_markup(f'<span size="large"><span foreground="{color}">●</span> {text}</span>')
            self.pause_btn.set_label("Resume listening" if state in ("paused", "standby") else "Pause listening")
            return False

        def line(self, kind: str, text: str) -> None:
            GLib.idle_add(self._line, kind, text)

        def _line(self, kind, text):
            end = self.buf.get_end_iter()
            label = {"you": "You: ", "agent": "", "tool": "› ", "info": "", "error": "Error: "}.get(kind, "")
            self.buf.insert_with_tags_by_name(end, label + text.rstrip() + "\n\n" if kind in ("you", "agent")
                                              else label + text.rstrip() + "\n", kind)
            self.view.scroll_to_mark(self.buf.get_insert(), 0.0, False, 0.0, 0.0)
            self.view.scroll_mark_onscreen(self.buf.create_mark(None, self.buf.get_end_iter(), False))
            return False

        def ended(self) -> None:
            GLib.idle_add(self._ended)

        def _ended(self):
            self._state("ended", "")
            GLib.timeout_add(1500, lambda: (Gtk.main_quit(), False)[1])
            return False

    return Window(), GLib, Gtk


def run(cfg: dict, workspace: str | None = None, resume: str | None = None, new: bool = False,
        wake_word: str | None = None, terminal: bool = False, out=print) -> int:
    from .speaker import Speaker

    model = cfg.get("stt_model", "base.en")
    note = listen.Notifier(cfg.get("listen_notify", True))
    if not stt.is_ready(model):
        started = stt.start_background_setup(model)
        msg = ("Setting up speech recognition (one-time download, about 200 MB). Try again in a few minutes."
               if started else "Speech recognition is still being set up. Try again in a minute.")
        note.show("Cursor Speak: conversation not ready yet", msg)
        out(msg)
        return 3
    ws = os.path.realpath(os.path.expanduser(workspace or cfg.get("converse_workspace") or os.getcwd()))
    if not Path(ws).is_dir():
        out(f"Not a folder: {ws}")
        return 2
    if wake_word is not None:
        cfg["converse_wake_word_enabled"] = bool(wake_word.strip())
        if wake_word.strip():
            cfg["converse_wake_word"] = wake_word
    chat_id = resume or (None if new else converse.remembered_chat(ws))

    converse.pid_file().write_text(str(os.getpid()))
    ec = None
    if cfg.get("converse_echo_cancel"):
        from .echo_cancel import SINK, SOURCE, EchoCancel

        ec = EchoCancel()
        if ec.start():
            cfg["_mic_target"], cfg["playback_target"] = SOURCE, SINK
        else:
            ec = None
            out("Echo cancellation isn't available; using a higher barge-in threshold instead.")
    speaker = Speaker(cfg)
    speaker.start()
    transcriber = stt.Transcriber(model, cfg.get("stt_language", "en"))
    gui = not terminal and (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    conv_ref: dict = {}
    GLib = Gtk = None
    try:
        transcriber.start()
        if gui:
            try:
                ui, GLib, Gtk = _gtk_window(conv_ref, ws)
            except Exception as e:  # no GTK
                log.warning("no conversation window (%s); using the terminal", e)
                gui = False
        if not gui:
            ui = TerminalUI(out)
        conv = converse.Conversation(cfg, ws, Notifying(ui, cfg.get("listen_notify", True)), speaker,
                                     transcriber, chat_id=chat_id)
        conv_ref["conv"] = conv
        if gui:
            converse.install_signals(conv, lambda fn: GLib.idle_add(lambda: (fn(), False)[1]))
            threading.Thread(target=conv.start, daemon=True).start()
            Gtk.main()
        else:
            converse.install_signals(conv, lambda fn: threading.Thread(target=fn, daemon=True).start())
            conv.start()
            while not conv.wait(0.5):
                pass
        return 0
    except Exception as e:
        log.exception("conversation failed")
        note.show("Cursor Speak conversation failed", str(e), urgent=True)
        out(f"Conversation failed: {e}")
        return 1
    finally:
        c = conv_ref.get("conv")
        if c:
            c.end()
        transcriber.close()
        speaker.close()
        if ec:
            ec.stop()
        try:
            converse.pid_file().unlink()
        except OSError:
            pass
