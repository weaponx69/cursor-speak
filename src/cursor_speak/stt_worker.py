#!/usr/bin/env python3
"""Speech-to-text helper, run inside the per-user STT venv (faster-whisper).

Self-contained on purpose: it is executed by the venv's own Python, never imported
by the app.

  stt_worker.py --download MODEL DIR     fetch a model into DIR (one time)
  stt_worker.py --transcribe DIR WAV     print {"text": ...} as JSON
  stt_worker.py --serve DIR [LANG]       keep the model loaded; one JSON request
                                         per stdin line {"wav": path}, one
                                         {"text": ...} or {"error": ...} line back
"""

import json
import sys


def _model(model_dir: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_dir, device="cpu", compute_type="int8")


def download(model: str, dest: str) -> None:
    from faster_whisper import download_model

    download_model(model, output_dir=dest)
    print(json.dumps({"ok": True}))


def load_wav(path: str):
    """16 kHz mono float32 samples. Decoding the WAV ourselves avoids PyAV, whose
    API changes have broken faster-whisper's own decoder before."""
    import wave

    import numpy as np

    with wave.open(path, "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate != 16000 and len(a):
        n = int(round(len(a) * 16000 / rate))
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    return a


def transcribe(model_dir: str, wav: str, language: str = "en") -> None:
    segments, _info = _model(model_dir).transcribe(
        load_wav(wav), language=language or None, beam_size=5, vad_filter=True
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    print(json.dumps({"text": text}))


def serve(model_dir: str, language: str = "en") -> None:
    model = _model(model_dir)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            segments, _info = model.transcribe(
                load_wav(req["wav"]), language=language or None, beam_size=5, vad_filter=True
            )
            out = {"text": " ".join(s.text.strip() for s in segments).strip()}
        except Exception as e:  # keep serving
            out = {"error": f"{e.__class__.__name__}: {e}"}
        print(json.dumps(out), flush=True)


def main(argv: list) -> int:
    if len(argv) >= 2 and argv[0] == "--serve":
        serve(argv[1], argv[2] if len(argv) > 2 else "en")
        return 0
    if len(argv) >= 3 and argv[0] == "--download":
        download(argv[1], argv[2])
        return 0
    if len(argv) >= 3 and argv[0] == "--transcribe":
        transcribe(argv[1], argv[2], argv[3] if len(argv) > 3 else "en")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
