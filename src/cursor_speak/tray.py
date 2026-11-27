"""GTK3 front end: AppIndicator tray icon (GNOME), or a small window if no tray."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
except (ValueError, ImportError):
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator  # noqa: E402
    except (ValueError, ImportError):
        AppIndicator = None

from . import APP_ID, autostart  # noqa: E402

log = logging.getLogger("cursor-speak.tray")


def _icon_dir() -> str | None:
    """Theme path for running from a source checkout (the .deb uses /usr/share/icons)."""
    if Path("/usr/share/icons/hicolor/scalable/apps/cursor-speak.svg").exists():
        return None
    here = Path(__file__).resolve().parent
    for cand in (here.parent.parent / "data" / "icons", here / "icons"):
        if (cand / "hicolor" / "scalable" / "apps" / "cursor-speak.svg").exists():
            return str(cand)
    return None


def tray_host_available(wait_seconds: float = 0.0) -> bool:
    """True when a StatusNotifier host exists (GNOME: ubuntu-appindicators extension)."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 2000, None,
            )
            if res.unpack()[0]:
                return True
        except GLib.Error:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(1)


class TrayApp:
    def __init__(self, ctl, force_window: bool = False):
        self.ctl = ctl
        self.indicator = None
        self.window = None
        use_tray = AppIndicator is not None and not force_window and tray_host_available(20.0)
        self.menu = self._build_menu()
        if use_tray:
            theme = _icon_dir()
            if theme:
                self.indicator = AppIndicator.Indicator.new_with_path(
                    APP_ID, APP_ID, AppIndicator.IndicatorCategory.APPLICATION_STATUS, theme
                )
            else:
                self.indicator = AppIndicator.Indicator.new(
                    APP_ID, APP_ID, AppIndicator.IndicatorCategory.APPLICATION_STATUS
                )
            self.indicator.set_title("Cursor Speak")
            self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.indicator.set_menu(self.menu)
            self.mode = "tray"
        else:
            if not force_window:
                log.warning("no tray available; showing a window instead")
            self._build_window()
            self.mode = "window"
        GLib.timeout_add(1000, self._refresh)
        GLib.timeout_add(500, self._poll_quit)
        self._refresh()

    # menu -----------------------------------------------------------------------
    def _build_menu(self) -> Gtk.Menu:
        m = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="Starting")
        self.status_item.set_sensitive(False)
        self.pause_item = Gtk.MenuItem(label="Pause")
        self.pause_item.connect("activate", lambda _w: self.ctl.toggle())
        stop_item = Gtk.MenuItem(label="Stop speaking")
        stop_item.connect("activate", lambda _w: self.ctl.stop_speaking())
        replay_item = Gtk.MenuItem(label="Replay last")
        replay_item.connect("activate", lambda _w: self.ctl.replay())
        test_item = Gtk.MenuItem(label="Speak a test phrase")
        test_item.connect("activate", lambda _w: self.ctl.test())
        self.autostart_item = Gtk.CheckMenuItem(label="Start at login")
        self.autostart_item.set_active(autostart.is_enabled())
        self.autostart_item.connect("toggled", lambda w: autostart.set_enabled(w.get_active()))
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _w: self._quit())
        for item in (
            self.status_item, Gtk.SeparatorMenuItem(), self.pause_item, stop_item, replay_item,
            test_item, Gtk.SeparatorMenuItem(), self.autostart_item, Gtk.SeparatorMenuItem(), quit_item,
        ):
            m.append(item)
        m.show_all()
        return m

    def _build_window(self) -> None:
        w = Gtk.Window(title="Cursor Speak")
        w.set_default_size(260, 10)
        w.set_icon_name(APP_ID)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
        self.win_status = Gtk.Label(label="Starting")
        box.pack_start(self.win_status, False, False, 0)
        self.win_pause = Gtk.Button(label="Pause")
        self.win_pause.connect("clicked", lambda _b: self.ctl.toggle())
        for label, fn in (("Stop speaking", self.ctl.stop_speaking), ("Replay last", self.ctl.replay),
                          ("Speak a test phrase", self.ctl.test)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, f=fn: f())
            if label == "Stop speaking":
                box.pack_start(self.win_pause, False, False, 0)
            box.pack_start(b, False, False, 0)
        chk = Gtk.CheckButton(label="Start at login")
        chk.set_active(autostart.is_enabled())
        chk.connect("toggled", lambda c: autostart.set_enabled(c.get_active()))
        box.pack_start(chk, False, False, 0)
        q = Gtk.Button(label="Quit")
        q.connect("clicked", lambda _b: self._quit())
        box.pack_start(q, False, False, 0)
        w.add(box)
        w.connect("delete-event", lambda *_: (self._quit(), True)[1])
        w.show_all()
        self.window = w

    # updates ------------------------------------------------------------------------
    def _refresh(self) -> bool:
        ctl = self.ctl
        status = ctl.status_line()
        self.status_item.set_label(f"Cursor Speak: {status}")
        self.pause_item.set_label("Resume" if ctl.paused else "Pause")
        if self.indicator:
            icon = f"{APP_ID}-paused" if ctl.paused else APP_ID
            self.indicator.set_icon_full(icon, "Cursor Speak")
        if self.window:
            self.win_status.set_text(f"{status}\nVoice: {ctl.speaker.backend}")
            self.win_pause.set_label("Resume" if ctl.paused else "Pause")
        return True

    def _poll_quit(self) -> bool:
        if self.ctl.quit_event.is_set():
            Gtk.main_quit()
            return False
        return True

    def _quit(self) -> None:
        self.ctl.request_quit()

    def run(self) -> None:
        Gtk.main()
