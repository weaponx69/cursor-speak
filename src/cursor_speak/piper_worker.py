#!/usr/bin/env python3
"""Piper synthesis worker. Runs inside the per-user venv (not the system python).

Protocol: one JSON object per stdin line {"id": n, "text": "...", "out": "/path.wav"};
one JSON reply per stdout line {"id": n, "ok": true} or {"id": n, "ok": false, "error": "..."}.
`--selftest MODEL` synthesizes a short phrase and exits 0 on success.
"""

import json
import sys
import tempfile
import wave


def load(model, speed):
    from piper import PiperVoice

    voice = PiperVoice.load(model)
    cfg = None
    try:
        from piper import SynthesisConfig

        cfg = SynthesisConfig(length_scale=1.0 / max(0.3, float(speed)))
    except Exception:
        pass
    return voice, cfg


def synth(voice, cfg, text, out):
    with wave.open(out, "wb") as wf:
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(text, wf, syn_config=cfg)
        else:
            voice.synthesize(text, wf)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        voice, cfg = load(sys.argv[2], 1.0)
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            synth(voice, cfg, "Cursor Speak self test.", f.name)
            f.seek(0, 2)
            sys.exit(0 if f.tell() > 2000 else 1)
    model = sys.argv[1]
    speed = sys.argv[2] if len(sys.argv) > 2 else "1.0"
    voice, cfg = load(model, speed)
    print(json.dumps({"id": 0, "ok": True, "ready": True}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        try:
            synth(voice, cfg, req["text"], req["out"])
            print(json.dumps({"id": req["id"], "ok": True, "out": req["out"]}), flush=True)
        except Exception as e:  # keep serving
            print(json.dumps({"id": req["id"], "ok": False, "error": str(e)}), flush=True)


if __name__ == "__main__":
    main()
