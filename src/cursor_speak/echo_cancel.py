"""Optional PipeWire echo cancellation for conversation mode (no root needed).

Starts a private `pipewire -c` process that loads libpipewire-module-echo-cancel
with a virtual mic ("Cursor Speak echo-cancelled mic") and a virtual speaker. Our
speech plays into the virtual speaker (which forwards to your normal output) and
we record from the virtual mic, which has our own voice subtracted. It goes away
when the conversation ends. Nothing in your PipeWire config is changed.
"""

from __future__ import annotations

import glob
import logging
import shutil
import subprocess
import time

from . import config, launcher

log = logging.getLogger("cursor-speak.echo")

SOURCE = "cursor_speak_ec_source"
SINK = "cursor_speak_ec_sink"

CONF = """# Written by Cursor Speak for conversation mode; safe to delete.
context.properties = { log.level = 0 }
context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}
context.modules = [
    { name = libpipewire-module-rt flags = [ ifexists nofail ] }
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-echo-cancel
      args = {
        library.name = aec/libspa-aec-webrtc
        monitor.mode = false
        capture.props = { node.name = "cursor_speak_ec_capture" node.passive = true }
        source.props  = { node.name = "%(source)s" node.description = "Cursor Speak echo-cancelled mic" }
        sink.props    = { node.name = "%(sink)s" node.description = "Cursor Speak echo-cancel speaker" }
        playback.props = { node.name = "cursor_speak_ec_playback" node.passive = true }
      }
    }
]
""" % {"source": SOURCE, "sink": SINK}


def available() -> bool:
    if not shutil.which("pipewire"):
        return False
    return bool(glob.glob("/usr/lib/*/pipewire-0.3/libpipewire-module-echo-cancel.so")
                or glob.glob("/usr/lib/pipewire-0.3/libpipewire-module-echo-cancel.so"))


class EchoCancel:
    def __init__(self):
        self.proc: subprocess.Popen | None = None

    def start(self, wait: float = 2.0) -> bool:
        if not available():
            return False
        conf = config.CONFIG_DIR / "echo-cancel.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(CONF)
        try:
            self.proc = subprocess.Popen(["pipewire", "-c", str(conf)], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, env=launcher.host_env())
        except OSError as e:
            log.warning("echo cancel failed to start: %s", e)
            return False
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                log.warning("echo cancel exited early")
                self.proc = None
                return False
            if _node_exists(SOURCE):
                return True
            time.sleep(0.1)
        return _node_exists(SOURCE)

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()


def _node_exists(name: str) -> bool:
    try:
        out = subprocess.run(["pw-cli", "ls", "Node"], capture_output=True, text=True, timeout=3,
                             env=launcher.host_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return f'"{name}"' in out
