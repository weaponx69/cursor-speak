"""Tiny unix-socket control channel so `cursor-speak pause` etc. work."""

from __future__ import annotations

import json
import os
import socket
import threading

from . import config

COMMANDS = {"pause", "resume", "toggle", "stop", "replay", "status", "test", "quit", "ping", "say", "listening", "own", "disown"}


def send(command: str, timeout: float = 3.0) -> str | None:
    """Send a command to the running app. Returns its reply or None if not running."""
    path = str(config.socket_path())
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall((command + "\n").encode())
        return s.makefile().readline().strip()
    except OSError:
        return None
    finally:
        s.close()


class Server:
    def __init__(self, controller):
        self.ctl = controller
        self.path = str(config.socket_path())
        self.sock: socket.socket | None = None

    def start(self) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        os.chmod(self.path, 0o600)
        self.sock.listen(4)
        threading.Thread(target=self._serve, name="control", daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    line = conn.makefile().readline().strip()
                    conn.sendall((self._handle(line) + "\n").encode())
                except OSError:
                    pass

    def _handle(self, line: str) -> str:
        cmd, _, arg = line.partition(" ")
        c = self.ctl
        if cmd == "ping":
            return "pong"
        if cmd == "pause":
            c.pause()
        elif cmd == "resume":
            c.resume()
        elif cmd == "toggle":
            c.toggle()
        elif cmd == "stop":
            c.stop_speaking()
        elif cmd == "replay":
            return "ok" if c.replay() else "nothing to replay yet"
        elif cmd == "test":
            c.test()
        elif cmd == "listening" and arg in ("on", "busy", "off"):
            c.set_listening(arg)
        elif cmd == "own" and arg:
            c.own_chat(arg.strip())
        elif cmd == "disown" and arg:
            c.disown_chat(arg.strip())
        elif cmd == "say" and arg:
            c.say(arg)
        elif cmd == "quit":
            c.request_quit()
        elif cmd == "status":
            return json.dumps(c.status())
        else:
            return "unknown command"
        return "ok"

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.close()
            os.unlink(self.path)
        except OSError:
            pass
