"""Run cursor-agent headless (`-p --output-format stream-json`) for one turn of a
conversation, resuming the same chat each time."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from typing import Callable

from . import deliver, launcher

log = logging.getLogger("cursor-speak.agent")

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
OnEvent = Callable[[str, str], None]  # (kind, text): kind in assistant, tool, error, session


def create_chat(cfg: dict, cwd: str, run=subprocess.run) -> str | None:
    """`cursor-agent create-chat` prints a new empty chat id."""
    try:
        res = run([deliver.agent_path(cfg), "create-chat"], capture_output=True, text=True,
                  timeout=60, cwd=cwd, env=launcher.host_env())
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("create-chat failed: %s", e)
        return None
    m = UUID.search(res.stdout or "")
    return m.group(0) if m else None


def build_cmd(cfg: dict, workspace: str, chat_id: str | None, prompt: str) -> list[str]:
    cmd = [deliver.agent_path(cfg), "-p", "--output-format", "stream-json", "--workspace", workspace]
    if chat_id:
        cmd += ["--resume", chat_id]
    if cfg.get("converse_trust", True):
        cmd.append("--trust")
    if cfg.get("converse_force", False):
        cmd.append("--force")
    if cfg.get("converse_model"):
        cmd += ["--model", str(cfg["converse_model"])]
    cmd += [str(a) for a in cfg.get("converse_agent_args") or []]
    if prompt.startswith("-"):
        cmd.append("--")
    cmd.append(prompt)
    return cmd


def tool_summary(tool_call: dict) -> str:
    """One readable line for a tool_call event: '$ npm test', 'read src/a.py'."""
    if not isinstance(tool_call, dict) or not tool_call:
        return "tool"
    key, body = next(iter(tool_call.items()))
    body = body if isinstance(body, dict) else {}
    args = body.get("args") or {}
    if key == "function":
        return f"{body.get('name', 'tool')}"
    name = re.sub(r"ToolCall$", "", key)
    if isinstance(args, dict):
        if args.get("command"):
            return f"$ {args['command']}"
        for k in ("path", "pattern", "query", "url", "globPattern"):
            if args.get(k):
                return f"{name} {args[k]}"
    return name


class Turn:
    """Parses one stream-json run. `final_text()` is what gets read aloud: the
    assistant's last message after its last tool call (its general answer)."""

    def __init__(self, on_event: OnEvent | None = None):
        self.on_event = on_event or (lambda kind, text: None)
        self.session_id: str | None = None
        self.segments: list[str] = []
        self.after_tool: list[str] = []
        self.result: str | None = None
        self.error: str | None = None
        self.tools = 0

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        t = ev.get("type")
        sid = ev.get("session_id")
        if sid and not self.session_id:
            self.session_id = sid
            self.on_event("session", sid)
        if t == "assistant":
            if ev.get("model_call_id"):  # duplicate flush before a tool call
                return
            text = "".join(c.get("text", "") for c in (ev.get("message") or {}).get("content", [])
                           if isinstance(c, dict) and c.get("type") == "text")
            if text.strip():
                self.segments.append(text)
                self.after_tool.append(text)
                self.on_event("assistant", text)
        elif t == "tool_call" and ev.get("subtype") == "started":
            self.tools += 1
            self.after_tool = []
            self.on_event("tool", tool_summary(ev.get("tool_call") or {}))
        elif t == "result":
            self.result = ev.get("result") or ""
            if ev.get("is_error"):
                self.error = self.result or "agent error"

    def final_text(self) -> str:
        if self.after_tool:
            return "\n\n".join(self.after_tool)
        if self.segments and not self.tools:
            return "\n\n".join(self.segments)
        return self.result or (self.segments[-1] if self.segments else "")


class AgentRunner:
    """One cursor-agent process at a time; cancel() stops it."""

    def __init__(self, cfg: dict, popen=subprocess.Popen):
        self.cfg = cfg
        self.popen = popen
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def run(self, workspace: str, chat_id: str | None, prompt: str, on_event: OnEvent | None = None) -> Turn:
        turn = Turn(on_event)
        cmd = build_cmd(self.cfg, workspace, chat_id, prompt)
        log.info("agent turn in %s (chat %s)", workspace, (chat_id or "new")[:8])
        with self.lock:
            self.cancelled = False
            self.proc = self.popen(cmd, cwd=workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=launcher.host_env(),
                                   start_new_session=True)
            proc = self.proc
        for line in proc.stdout:
            turn.feed(line)
        err = proc.stderr.read() if proc.stderr else ""
        code = proc.wait()
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, AttributeError):
                pass
        with self.lock:
            self.proc = None
        if self.cancelled:
            turn.error = "cancelled"
        elif code != 0 and not turn.error:
            last = [ln for ln in (err or "").strip().splitlines() if ln.strip()]
            turn.error = last[-1] if last else f"cursor-agent exited with {code}"
        if turn.error and turn.error != "cancelled":
            turn.on_event("error", turn.error)
        return turn

    def cancel(self) -> bool:
        with self.lock:
            p = self.proc
            if not p or p.poll() is not None:
                return False
            self.cancelled = True
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
        return True
