"""Conversation mode, end to end with fakes: a fake mic stream, a fake
cursor-agent script, a fake speaker. Nothing records, plays or calls the agent."""

import array
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from cursor_speak import agent, config, converse, cues, recorder, stt  # noqa: E402

FAKE_AGENT = str(HERE / "fake_cursor_agent.py")
CHAT = "11111111-2222-3333-4444-555555555555"
RATE = recorder.RATE


def tone(seconds, amp=8000):
    return array.array("h", (int(amp * math.sin(2 * math.pi * 300 * i / RATE)) for i in range(int(RATE * seconds)))).tobytes()


def silence(seconds):
    return bytes(int(RATE * seconds) * 2)


def frames(pcm):
    for i in range(0, len(pcm), recorder.FRAME_BYTES):
        yield pcm[i:i + recorder.FRAME_BYTES]


class FakeMic:
    def __init__(self, pcm, delay=0.0):
        self.pcm, self.delay = pcm, delay

    def frames(self):
        for f in frames(self.pcm):
            if self.delay:
                time.sleep(self.delay)
            yield f

    def close(self):
        pass


class FakeSpeaker:
    def __init__(self):
        self.spoken = []
        self.speaking = False
        self.stops = 0

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        self.stops += 1
        self.speaking = False


class FakeTranscriber:
    def __init__(self, texts):
        self.texts = list(texts)
        self.wavs = []

    def transcribe(self, wav):
        with wave.open(str(wav)) as w:
            self.wavs.append(w.getnframes() / w.getframerate())
        return self.texts.pop(0) if self.texts else ""


class RecUI(converse.UI):
    def __init__(self):
        self.states, self.lines = [], []
        self.done = threading.Event()

    def state(self, state, detail=""):
        self.states.append(state)

    def line(self, kind, text):
        self.lines.append((kind, text))

    def ended(self):
        self.done.set()


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class TextHelperTests(unittest.TestCase):
    def test_commands_need_the_whole_utterance(self):
        self.assertEqual(converse.match_command("Stop."), "stop")
        self.assertEqual(converse.match_command("Okay, stop talking please"), "stop")
        self.assertEqual(converse.match_command("Pause listening."), "pause")
        self.assertEqual(converse.match_command("Resume"), "resume")
        self.assertEqual(converse.match_command("New conversation."), "new")
        self.assertEqual(converse.match_command("Goodbye!"), "end")
        self.assertIsNone(converse.match_command("Stop the dev server and restart it"))
        self.assertIsNone(converse.match_command("Please fix the login bug"))

    def test_wake_word(self):
        self.assertEqual(converse.strip_wake("Hey, Cursor. Run the tests.", "hey cursor"), "Run the tests.")
        self.assertEqual(converse.strip_wake("hey curser what's failing", "hey cursor"), "what's failing")
        self.assertEqual(converse.strip_wake("Okay hey Cursor, open the readme", "hey cursor"), "open the readme")
        self.assertEqual(converse.strip_wake("Hey Cursor.", "hey cursor"), "")
        self.assertIsNone(converse.strip_wake("Run the tests", "hey cursor"))

    def test_echo_detection(self):
        spoken = "All twelve tests pass and the build is green."
        self.assertTrue(converse.is_echo("the build is green", spoken))
        self.assertFalse(converse.is_echo("stop", spoken))
        self.assertFalse(converse.is_echo("now deploy it to staging", spoken))


class SegmenterTests(unittest.TestCase):
    def run_seg(self, pcm, speaking=False, **kw):
        seg = recorder.Segmenter(**kw)
        events = [e for e in (seg.feed(f, speaking) for f in frames(pcm)) if e]
        return seg, events

    def test_utterance_between_silences(self):
        _, ev = self.run_seg(silence(1.0) + tone(1.0) + silence(1.5))
        self.assertEqual(ev[0], "start")
        self.assertEqual(ev[1][0], "utterance")
        secs = len(ev[1][1]) / 2 / RATE
        self.assertGreater(secs, 1.2)  # speech + pre-roll + trailing quiet
        self.assertLess(secs, 2.6)

    def test_two_utterances(self):
        _, ev = self.run_seg(tone(0.8) + silence(1.5) + tone(0.8) + silence(1.5))
        self.assertEqual([e if isinstance(e, str) else e[0] for e in ev], ["start", "utterance", "start", "utterance"])

    def test_click_is_not_an_utterance(self):
        _, ev = self.run_seg(silence(0.5) + tone(0.06) + silence(2.0))
        self.assertEqual(ev, [])

    def test_barge_needs_louder_voice_while_speaking(self):
        _, ev = self.run_seg(tone(1.0, amp=900) + silence(1.5), speaking=True)
        self.assertEqual(ev, [])  # quiet speaker echo doesn't count
        _, ev = self.run_seg(tone(1.0, amp=9000) + silence(1.5), speaking=True)
        self.assertEqual(ev[0], "start")

    def test_noise_floor_adapts(self):
        noise = tone(2.0, amp=400)  # steady hum just under the bar
        seg, ev = self.run_seg(noise)
        self.assertEqual(ev, [])
        self.assertGreater(seg.floor, 100)


class AgentTests(unittest.TestCase):
    def test_build_cmd(self):
        cmd = agent.build_cmd({"agent_command": "/x/cursor-agent", "converse_trust": True}, "/w", CHAT, "fix it")
        self.assertEqual(cmd[:6], ["/x/cursor-agent", "-p", "--output-format", "stream-json", "--workspace", "/w"])
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[cmd.index("--resume") + 1], CHAT)
        self.assertIn("--trust", cmd)
        self.assertNotIn("--force", cmd)
        self.assertEqual(cmd[-1], "fix it")
        cmd = agent.build_cmd({"converse_force": True, "converse_trust": False}, "/w", None, "-v please")
        self.assertIn("--force", cmd)
        self.assertNotIn("--resume", cmd)
        self.assertEqual(cmd[-2:], ["--", "-v please"])

    def test_turn_final_text_is_after_last_tool(self):
        t = agent.Turn()
        for ev in (
            {"type": "system", "subtype": "init", "session_id": CHAT},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me look."}]}},
            {"type": "tool_call", "subtype": "started", "tool_call": {"readToolCall": {"args": {"path": "a.py"}}}},
            {"type": "assistant", "model_call_id": "m", "message": {"content": [{"type": "text", "text": "dup"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "It's fixed."}]}},
            {"type": "result", "result": "Let me look.It's fixed.", "is_error": False},
        ):
            t.feed(json.dumps(ev))
        self.assertEqual(t.session_id, CHAT)
        self.assertEqual(t.final_text(), "It's fixed.")
        self.assertEqual(t.tools, 1)

    def test_tool_summary(self):
        self.assertEqual(agent.tool_summary({"shellToolCall": {"args": {"command": "ls"}}}), "$ ls")
        self.assertEqual(agent.tool_summary({"readToolCall": {"args": {"path": "x"}}}), "read x")
        self.assertEqual(agent.tool_summary({"function": {"name": "grep"}}), "grep")

    def test_runner_with_fake_agent(self):
        events = []
        with tempfile.TemporaryDirectory() as d:
            r = agent.AgentRunner({"agent_command": FAKE_AGENT})
            turn = r.run(d, CHAT, "check the build", lambda k, t: events.append((k, t)))
        self.assertIsNone(turn.error)
        self.assertIn(("tool", "$ npm test"), events)
        self.assertTrue(turn.final_text().startswith("You said: check the build."))

    def test_runner_reports_failure(self):
        with mock.patch.dict(os.environ, {"FAKE_AGENT_FAIL": "1"}), tempfile.TemporaryDirectory() as d:
            turn = agent.AgentRunner({"agent_command": FAKE_AGENT}).run(d, CHAT, "x")
        self.assertEqual(turn.error, "Error: not logged in")

    def test_cancel(self):
        with mock.patch.dict(os.environ, {"FAKE_AGENT_SLEEP": "30"}), tempfile.TemporaryDirectory() as d:
            r = agent.AgentRunner({"agent_command": FAKE_AGENT})
            out = {}
            t = threading.Thread(target=lambda: out.setdefault("turn", r.run(d, CHAT, "x")))
            t.start()
            self.assertTrue(wait_for(lambda: r.busy))
            time.sleep(0.3)
            self.assertTrue(r.cancel())
            t.join(5)
        self.assertEqual(out["turn"].error, "cancelled")

    def test_create_chat(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(agent.create_chat({"agent_command": FAKE_AGENT}, d), CHAT)


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        self.log = Path(self.tmp.name, "agent.log")
        self.env = mock.patch.dict(os.environ, {"FAKE_AGENT_LOG": str(self.log)})
        self.env.start()
        self.state = mock.patch.object(config, "STATE_DIR", Path(self.tmp.name, "state"))
        self.state.start()
        self.app = []
        self.cues = []

    def tearDown(self):
        self.env.stop()
        self.state.stop()
        self.tmp.cleanup()

    def make(self, pcm=b"", texts=(), cfg=None, speaker=None, chat_id=None):
        c = dict(config.DEFAULTS, agent_command=FAKE_AGENT, **(cfg or {}))
        self.ui = RecUI()
        self.speaker = speaker or FakeSpeaker()
        self.tr = FakeTranscriber(texts)
        conv = converse.Conversation(
            c, self.ws, self.ui, self.speaker, self.tr, chat_id=chat_id,
            mic_factory=lambda: FakeMic(pcm), cue=self.cues.append,
            tell_app=lambda cmd: self.app.append(cmd) or "ok", restart_mic=False)
        return conv

    def agent_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def test_full_loop_speech_to_agent_to_spoken_reply(self):
        pcm = silence(0.6) + tone(1.2) + silence(1.5)
        conv = self.make(pcm, ["Run the tests please."])
        conv.start()
        self.assertTrue(wait_for(lambda: self.speaker.spoken), self.ui.lines)
        conv.end()
        calls = self.agent_calls()
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[argv.index("--resume") + 1], CHAT)  # from create-chat
        self.assertEqual(argv[argv.index("--workspace") + 1], self.ws)
        self.assertEqual(argv[-1], "Run the tests please.")
        spoken = self.speaker.spoken[0]
        self.assertIn("All 12 tests pass", spoken)
        self.assertNotIn("npm test", spoken)  # code block and tool chatter not read
        self.assertNotIn("I'll run the tests", spoken)
        kinds = [k for k, _ in self.ui.lines]
        self.assertIn("you", kinds)
        self.assertIn(("tool", "$ npm test"), self.ui.lines)
        self.assertIn(f"own {CHAT}", self.app)
        self.assertIn(f"disown {CHAT}", self.app)
        self.assertIn("thinking", self.cues)
        self.assertEqual(converse.remembered_chat(self.ws), CHAT)

    def test_followups_resume_same_chat(self):
        conv = self.make(chat_id="aaaaaaaa-0000-0000-0000-000000000000")
        conv.start()
        conv.handle_text("first question")
        self.assertTrue(wait_for(lambda: len(self.speaker.spoken) == 1))
        conv.handle_text("and a follow up")
        self.assertTrue(wait_for(lambda: len(self.speaker.spoken) == 2))
        conv.end()
        ids = {a[a.index("--resume") + 1] for a in self.agent_calls()}
        self.assertEqual(ids, {"aaaaaaaa-0000-0000-0000-000000000000"})

    def test_voice_commands(self):
        conv = self.make(chat_id=CHAT)
        conv.start()
        self.assertEqual(conv.handle_text("Pause listening."), "command:pause")
        self.assertTrue(conv.standby)
        self.assertEqual(conv.handle_text("Fix the bug"), "standby")
        self.assertEqual(conv.handle_text("Resume."), "command:resume")
        self.assertFalse(conv.standby)
        self.assertEqual(conv.handle_text("Stop."), "command:stop")
        self.assertGreaterEqual(self.speaker.stops, 1)
        self.assertEqual(conv.handle_text("New conversation."), "command:new")
        self.assertEqual(conv.handle_text("Goodbye."), "command:end")
        self.assertTrue(self.ui.done.is_set())
        self.assertIn("goodbye", self.cues)
        self.assertEqual(self.agent_calls(), [])

    def test_stop_cancels_running_turn(self):
        with mock.patch.dict(os.environ, {"FAKE_AGENT_SLEEP": "30"}):
            conv = self.make(chat_id=CHAT)
            conv.start()
            conv.handle_text("do a long thing")
            self.assertTrue(wait_for(lambda: conv.runner.busy))
            time.sleep(0.3)
            self.assertEqual(conv.handle_text("stop"), "command:stop")
            self.assertTrue(wait_for(lambda: not conv.runner.busy, 5))
            conv.end()
        self.assertEqual(self.speaker.spoken, [])
        self.assertIn(("info", "Cancelled the agent's turn."), self.ui.lines)

    def test_wake_word(self):
        conv = self.make(chat_id=CHAT, cfg={"converse_wake_word_enabled": True})
        conv.start()
        self.assertEqual(conv.handle_text("what time is it"), "no wake word")
        self.assertEqual(conv.handle_text("Hey Cursor, run the linter."), "sent")
        self.assertTrue(wait_for(lambda: self.speaker.spoken))
        self.assertEqual(self.agent_calls()[0][-1], "run the linter.")
        # Right after a reply, a follow-up needs no wake word.
        self.assertEqual(conv.handle_text("and fix what it finds"), "sent")
        conv.end()

    def test_barge_in_stops_speech(self):
        sp = FakeSpeaker()
        sp.speaking = True
        conv = self.make(tone(1.0, amp=12000) + silence(1.5), ["Wait, use yarn instead."], speaker=sp, chat_id=CHAT)
        conv.spoken_text = "All twelve tests pass and the build is green."
        conv.start()
        self.assertTrue(wait_for(lambda: self.agent_calls()))
        conv.end()
        self.assertGreaterEqual(sp.stops, 1)
        self.assertEqual(self.agent_calls()[0][-1], "Wait, use yarn instead.")

    def test_echo_of_own_speech_is_ignored(self):
        sp = FakeSpeaker()
        sp.speaking = True
        conv = self.make(chat_id=CHAT, speaker=sp)
        conv.spoken_text = "All twelve tests pass and the build is green."
        self.assertEqual(conv.handle_text("tests pass and the build is green", during_speech=True), "echo")

    def test_older_app_gets_paused_instead(self):
        replies = {"own": "unknown command", "status": json.dumps({"paused": False})}
        cmds = []

        def tell(cmd):
            cmds.append(cmd)
            return replies.get(cmd.split()[0], "ok")

        conv = self.make(chat_id=CHAT)
        conv.tell_app = tell
        conv.start()
        conv.end()
        self.assertIn("pause", cmds)
        self.assertEqual(cmds[-1], "resume")

    def test_typed_message_while_busy_is_queued(self):
        with mock.patch.dict(os.environ, {"FAKE_AGENT_SLEEP": "0.8"}):
            conv = self.make(chat_id=CHAT)
            conv.start()
            conv.handle_text("first")
            self.assertTrue(wait_for(lambda: conv.runner.busy))
            conv.handle_text("second")
            self.assertTrue(wait_for(lambda: len(self.speaker.spoken) == 2, 15))
            conv.end()
        self.assertEqual([a[-1] for a in self.agent_calls()], ["first", "second"])


class AppOwnershipTests(unittest.TestCase):
    def test_controller_skips_owned_chat(self):
        from cursor_speak import controller
        from cursor_speak.chatstore import Reply

        with mock.patch.object(controller, "Speaker"), mock.patch.object(controller, "Watcher"), \
                mock.patch.object(controller.Controller, "_load_last"):
            ctl = controller.Controller(dict(config.DEFAULTS))
        ctl._save_last = lambda: None
        ctl.own_chat(CHAT)
        ctl._on_reply(Reply(id="r1", chat_id=CHAT, text="Done.", prompt="p", prompt_ts=None, chat_dir=Path("/x")))
        ctl.speaker.speak.assert_not_called()
        ctl.disown_chat(CHAT)
        ctl._on_reply(Reply(id="r2", chat_id=CHAT, text="Done.", prompt="p", prompt_ts=None, chat_dir=Path("/x")))
        ctl.speaker.speak.assert_called_once()

    def test_control_own_disown(self):
        from cursor_speak import control

        srv = control.Server.__new__(control.Server)
        srv.ctl = mock.Mock()
        self.assertEqual(srv._handle(f"own {CHAT}"), "ok")
        srv.ctl.own_chat.assert_called_with(CHAT)
        self.assertEqual(srv._handle(f"disown {CHAT}"), "ok")
        srv.ctl.disown_chat.assert_called_with(CHAT)


class CueAndCliTests(unittest.TestCase):
    def test_cue_tones(self):
        pcm = cues.tone_pcm(cues.CUES["listening"])
        self.assertGreater(len(pcm), 1000)
        launched = []
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            self.assertTrue(cues.play("thinking", {"converse_sounds": True}, popen=lambda cmd, **kw: launched.append(cmd))
                            or not cues.player(Path("x"), {}))
            self.assertFalse(cues.play("thinking", {"converse_sounds": False}, popen=lambda cmd, **kw: launched.append(cmd)))

    def test_cli_parses_converse(self):
        from cursor_speak.cli import build_parser

        ns = build_parser().parse_args(["converse", "--workspace", "/w", "--resume", CHAT, "--wake-word", "hey cursor"])
        self.assertEqual((ns.cmd, ns.workspace, ns.resume, ns.wake_word), ("converse", "/w", CHAT, "hey cursor"))
        self.assertTrue(build_parser().parse_args(["converse", "--toggle"]).toggle)

    def test_converse_shortcut_slot(self):
        from cursor_speak import shortcut
        from test_voice_input import FakeGSettings

        gs = FakeGSettings(listing="org.gnome.foo x ['<Super><Alt>space']")
        shortcut.install("a listen --toggle", run=gs)
        accel = shortcut.install_converse("a converse --toggle", run=gs)
        self.assertEqual(accel, "<Super><Alt>c")
        self.assertEqual(shortcut.status(run=gs)["command"], "a listen --toggle")
        self.assertEqual(shortcut.status(run=gs, slot=shortcut.CONVERSE_SLOT)["command"], "a converse --toggle")


def _resample(pcm: bytes, src: int, dst: int) -> bytes:
    a = array.array("h")
    a.frombytes(pcm)
    n = int(len(a) * dst / src)
    out = array.array("h", (a[min(len(a) - 1, int(i * src / dst))] for i in range(n)))
    return out.tobytes()


class SpokenWavEndToEndTests(unittest.TestCase):
    """Silent full loop: piper renders a request to a WAV, it is fed as the mic,
    faster-whisper transcribes it, the fake agent answers, the reply is 'spoken'
    to a fake speaker. Skipped unless the local models are installed."""

    def test_spoken_request_reaches_agent(self):
        cfg = config.load_config()
        model = cfg.get("stt_model", "base.en")
        piper = config.VENV_DIR / "bin" / "piper"
        voice = config.VOICES_DIR / "en_US-lessac-medium.onnx"
        if not (stt.is_ready(model) and piper.exists() and voice.exists()):
            self.skipTest("local speech models not set up")
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d, "req.wav")
            subprocess.run([str(piper), "-m", str(voice), "-f", str(wav)], input="Please run the unit tests.",
                           text=True, check=True, capture_output=True, timeout=120)
            with wave.open(str(wav)) as w:
                pcm = _resample(w.readframes(w.getnframes()), w.getframerate(), RATE)
            mic_pcm = silence(0.8) + pcm + silence(1.5)
            log = Path(d, "agent.log")
            tr = stt.Transcriber(model)
            ui, sp = RecUI(), FakeSpeaker()
            with mock.patch.dict(os.environ, {"FAKE_AGENT_LOG": str(log)}), \
                    mock.patch.object(config, "STATE_DIR", Path(d, "state")):
                conv = converse.Conversation(
                    dict(cfg, agent_command=FAKE_AGENT), d, ui, sp, tr, chat_id=CHAT,
                    mic_factory=lambda: FakeMic(mic_pcm), cue=lambda n: None,
                    tell_app=lambda c: "ok", restart_mic=False)
                try:
                    conv.start()
                    self.assertTrue(wait_for(lambda: sp.spoken, 60), ui.lines)
                finally:
                    conv.end()
                    tr.close()
            prompt = json.loads(log.read_text().splitlines()[0])[-1].lower()
        self.assertIn("unit tests", prompt)
        self.assertIn("you said", sp.spoken[0].lower())


if __name__ == "__main__":
    unittest.main()
