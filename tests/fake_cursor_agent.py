#!/usr/bin/env python3
"""Stands in for cursor-agent in tests: no network, no real agent.

`create-chat` prints a chat id. Otherwise it logs its argv to $FAKE_AGENT_LOG and
prints a stream-json run with one shell tool call. $FAKE_AGENT_SLEEP makes it
hang (to test cancelling); $FAKE_AGENT_FAIL makes it fail.
"""

import json
import os
import sys
import time

CHAT = "11111111-2222-3333-4444-555555555555"


def emit(**ev):
    ev.setdefault("session_id", CHAT)
    print(json.dumps(ev), flush=True)


def main(argv):
    if argv[:1] == ["create-chat"]:
        print(CHAT)
        return 0
    log = os.environ.get("FAKE_AGENT_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps(argv) + "\n")
    if os.environ.get("FAKE_AGENT_FAIL"):
        print("Error: not logged in", file=sys.stderr)
        return 1
    prompt = argv[-1]
    emit(type="system", subtype="init", cwd=os.getcwd(), model="fake")
    emit(type="user", message={"role": "user", "content": [{"type": "text", "text": prompt}]})
    emit(type="assistant", message={"role": "assistant", "content": [{"type": "text", "text": "I'll run the tests."}]})
    emit(type="tool_call", subtype="started", call_id="c1",
         tool_call={"shellToolCall": {"args": {"command": "npm test"}}})
    time.sleep(float(os.environ.get("FAKE_AGENT_SLEEP", "0")))
    emit(type="tool_call", subtype="completed", call_id="c1",
         tool_call={"shellToolCall": {"args": {"command": "npm test"}, "result": {"success": {}}}})
    final = f"You said: {prompt}. All 12 tests pass.\n\n```bash\nnpm test\n```\n\nThe build is green."
    emit(type="assistant", message={"role": "assistant", "content": [{"type": "text", "text": final}]})
    emit(type="result", subtype="success", is_error=False, result="I'll run the tests." + final, duration_ms=5)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
