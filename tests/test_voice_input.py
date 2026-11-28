"""Voice input: recording endpoint, delivery, shortcut, listen flow.

Nothing here records from the mic, makes sound, opens a terminal or types into a
window: subprocess runners are fakes. The optional end-to-end test renders speech
to a WAV file (silently) and transcribes it, only when the local models exist.
"""

import array
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cursor_speak import config, control, deliver, launcher, listen, recorder, shortcut, stt  # noqa: E402

RATE = recorder.RATE


def tone(seconds: float, amp: int = 8000) -> bytes:
    n = int(RATE * seconds)
    return array.array("h", (int(amp * math.sin(2 * math.pi * 440 * i / RATE)) for i in range(n))).tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(RATE * seconds) * 2)


def which_only(*names):
    return lambda n: f"/usr/bin/{n}" if n in names else None


class Calls:
    """Fake subprocess.run / Popen that records commands."""

    def __init__(self, fail=()):
        self.cmds = []
        self.inputs = []
        self.fail = fail

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        self.inputs.append(kw.get("input"))
        if cmd[0] in self.fail:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")


class EndpointerTests(unittest.TestCase):
    def test_stops_after_speech_then_silence(self):
        ep = recorder.Endpointer(silence_s=0.5)
        pcm = recorder.capture(io.BytesIO(tone(1.0) + silence(2.0)), ep, threading.Event())
        self.assertEqual(ep.reason, "silence")
        self.assertAlmostEqual(len(pcm) / 2 / RATE, 1.5, delta=0.1)

    def test_no_speech_gives_up(self):
        ep = recorder.Endpointer(no_speech_s=1.0)
        recorder.capture(io.BytesIO(silence(3.0)), ep, threading.Event())
        self.assertEqual(ep.reason, "no speech")

    def test_max_length(self):
        ep = recorder.Endpointer(max_s=1.0)
        recorder.capture(io.BytesIO(tone(3.0)), ep, threading.Event())
        self.assertEqual(ep.reason, "max length")

    def test_stop_event(self):
        stop = threading.Event()
        stop.set()
        ep = recorder.Endpointer()
        recorder.capture(io.BytesIO(tone(1.0)), ep, stop)
        self.assertEqual(ep.reason, "stopped")

    def test_record_with_fake_recorder_writes_wav(self):
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d, "in.raw")
            raw.write_bytes(tone(0.6) + silence(1.0))
            out = Path(d, "out.wav")
            reason = recorder.record(out, recorder.Endpointer(silence_s=0.3), threading.Event(),
                                     cmd=["cat", str(raw)])
            self.assertEqual(reason, "silence")
            with wave.open(str(out)) as w:
                self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2))
                self.assertGreater(w.getnframes(), RATE * 0.8)

    def test_recorder_preference(self):
        self.assertEqual(recorder.recorder_cmd(which_only("pw-record", "arecord"))[0], "pw-record")
        self.assertEqual(recorder.recorder_cmd(which_only("arecord"))[0], "arecord")
        self.assertIsNone(recorder.recorder_cmd(which_only()))
        self.assertIn("--raw", recorder.recorder_cmd(which_only("pw-record")))


class DeliverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sock = Path(self.tmp.name, ".ydotool_socket")
        self.env = mock.patch.dict(os.environ, {"YDOTOOL_SOCKET": str(self.sock)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_terminal_choice(self):
        argv = ["cursor-agent", "fix the bug"]
        self.assertEqual(deliver.terminal_cmd(argv, "/w", which_only("ptyxis", "gnome-terminal"))[:4],
                         ["ptyxis", "--new-window", "-d", "/w"])
        cmd = deliver.terminal_cmd(argv, "/w", which_only("gnome-terminal"))
        self.assertEqual(cmd, ["gnome-terminal", "--working-directory=/w", "--", "cursor-agent", "fix the bug"])
        self.assertEqual(deliver.terminal_cmd(argv, "/w", which_only("kgx"))[-1], "cursor-agent 'fix the bug'")
        self.assertIsNone(deliver.terminal_cmd(argv, "/w", which_only()))

    def test_new_mode_launches_agent_with_text_as_one_argument(self):
        run = Calls()
        res = deliver.open_agent("add a test; rm -rf nothing", {"agent_command": "/opt/cursor-agent", "agent_cwd": "/tmp"},
                                 run=run, which=which_only("gnome-terminal"))
        self.assertTrue(res.ok)
        self.assertEqual(run.cmds[0][-2:], ["/opt/cursor-agent", "add a test; rm -rf nothing"])

    def test_type_uses_ydotool_when_daemon_is_up(self):
        self.sock.touch()
        run = Calls()
        res = deliver.type_text("hello there", enter=True, run=run, which=which_only("ydotool"),
                                desktop="GNOME", sleep=lambda s: None)
        self.assertEqual(res.how, "ydotool")
        self.assertEqual(run.cmds[0], ["ydotool", "type", "--file", "-"])
        self.assertEqual(run.inputs[0], "hello there")
        self.assertEqual(run.cmds[1], ["ydotool", "key", "28:1", "28:0"])

    def test_type_without_enter(self):
        self.sock.touch()
        run = Calls()
        deliver.type_text("hi", enter=False, run=run, which=which_only("ydotool"), sleep=lambda s: None)
        self.assertEqual(len(run.cmds), 1)

    def test_no_daemon_falls_back_to_clipboard_on_gnome(self):
        run = Calls()
        res = deliver.type_text("hi", enter=True, run=run, which=which_only("ydotool", "wtype", "wl-copy"),
                                desktop="ubuntu:GNOME", sleep=lambda s: None)
        self.assertEqual(res.how, "clipboard")
        self.assertEqual(run.cmds, [["wl-copy"]])
        self.assertEqual(run.inputs, ["hi"])

    def test_wtype_off_gnome(self):
        run = Calls()
        res = deliver.type_text("hi", enter=True, run=run, which=which_only("wtype", "wl-copy"),
                                desktop="sway", sleep=lambda s: None)
        self.assertEqual(res.how, "wtype")
        self.assertEqual(run.cmds, [["wtype", "--", "hi"], ["wtype", "-k", "Return"]])

    def test_ydotool_failure_falls_back(self):
        self.sock.touch()
        run = Calls(fail=("ydotool",))
        res = deliver.type_text("hi", enter=False, run=run, which=which_only("ydotool", "wl-copy"),
                                desktop="GNOME", sleep=lambda s: None)
        self.assertEqual(res.how, "clipboard")

    def test_ydotool_help_lists_install(self):
        steps = deliver.ydotool_help(which_only())
        self.assertIn("sudo apt install ydotool", steps)
        self.assertTrue(any("systemctl --user enable --now ydotool" in s for s in steps))

    def test_print_mode(self):
        with mock.patch("builtins.print") as p:
            res = deliver.deliver("words", {}, mode="print")
        self.assertTrue(res.ok)
        p.assert_called_once_with("words")


class FakeGSettings:
    """In-memory gsettings for the shortcut code."""

    def __init__(self, listing="", customs=None):
        self.listing = listing
        self.store = {(shortcut.MEDIA, "custom-keybindings"): str(list(customs or {}))}
        for path, (name, binding) in (customs or {}).items():
            self.store[(f"{shortcut.CUSTOM}:{path}", "name")] = repr(name)
            self.store[(f"{shortcut.CUSTOM}:{path}", "binding")] = repr(binding)

    def __call__(self, cmd, **kw):
        args = cmd[1:]
        out = ""
        if args[0] == "list-recursively":
            out = self.listing
        elif args[0] == "get":
            out = self.store.get((args[1], args[2]), "''")
        elif args[0] == "set":
            v = args[3]
            self.store[(args[1], args[2])] = v if v.startswith(("[", "@")) else repr(v)
        elif args[0] == "reset":
            self.store.pop((args[1], args[2]), None)
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")


class ShortcutTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(shortcut.normalize("<Super><Alt>space"), shortcut.normalize("<Alt><Super>Space"))
        self.assertNotEqual(shortcut.normalize("<Alt>space"), shortcut.normalize("<Super><Alt>space"))

    def test_parse_strv(self):
        self.assertEqual(shortcut.parse_strv("@as []"), [])
        self.assertEqual(shortcut.parse_strv("['<Super>a', '<Alt>b']"), ["<Super>a", "<Alt>b"])
        self.assertEqual(shortcut.parse_strv("'<Super>l'"), ["<Super>l"])
        self.assertEqual(shortcut.parse_strv("uint32 5"), [])

    def test_install_picks_free_binding_and_appends_path(self):
        gs = FakeGSettings(
            listing="org.gnome.desktop.wm.keybindings activate-window-menu ['<Alt>space']\n"
                    "org.gnome.foo bar ['<Alt><Super>space']",
            customs={"/x/custom0/": ("Terminal", "<Super><Control>space")},
        )
        accel = shortcut.install("/usr/bin/cursor-speak listen --toggle", run=gs)
        self.assertEqual(accel, "<Super><Alt>v")
        paths = shortcut.parse_strv(gs.store[(shortcut.MEDIA, "custom-keybindings")])
        self.assertEqual(paths, ["/x/custom0/", shortcut._path()])
        st = shortcut.status(run=gs)
        self.assertEqual(st["command"], "/usr/bin/cursor-speak listen --toggle")
        # Installing again keeps one slot and the same key.
        self.assertEqual(shortcut.install("x listen --toggle", run=gs), "<Super><Alt>v")
        self.assertEqual(len(shortcut.parse_strv(gs.store[(shortcut.MEDIA, "custom-keybindings")])), 2)

    def test_preferred_key_conflict_is_refused(self):
        gs = FakeGSettings(listing="org.gnome.desktop.wm.keybindings x ['<Super><Alt>space']")
        with self.assertRaises(RuntimeError):
            shortcut.install("cmd", preferred="<Alt><Super>space", run=gs)

    def test_remove(self):
        gs = FakeGSettings()
        shortcut.install("cmd", run=gs)
        self.assertTrue(shortcut.remove(run=gs))
        self.assertIsNone(shortcut.status(run=gs))
        self.assertFalse(shortcut.remove(run=gs))


class LauncherTests(unittest.TestCase):
    def test_host_env_strips_appimage_paths(self):
        env = {"APPDIR": "/tmp/.mount_X", "PYTHONHOME": "/tmp/.mount_X/usr",
               "LD_LIBRARY_PATH": "/tmp/.mount_X/usr/lib:/opt/lib", "PATH": "/usr/bin", "APPIMAGE": "/a/b.AppImage"}
        out = launcher.host_env(env)
        self.assertNotIn("PYTHONHOME", out)
        self.assertNotIn("APPDIR", out)
        self.assertEqual(out["LD_LIBRARY_PATH"], "/opt/lib")
        self.assertEqual(out["PATH"], "/usr/bin")
        self.assertEqual(out["APPIMAGE"], "/a/b.AppImage")

    def test_host_env_unchanged_outside_appimage(self):
        self.assertEqual(launcher.host_env({"PATH": "/usr/bin"}), {"PATH": "/usr/bin"})

    def test_shortcut_command_quotes(self):
        self.assertEqual(launcher.shortcut_command("/home/b/My Apps/cs.AppImage"),
                         "'/home/b/My Apps/cs.AppImage' listen --toggle")


class ConfigAndControlTests(unittest.TestCase):
    def test_save_config_merges(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(config, "CONFIG_DIR", Path(d)), \
                    mock.patch.object(config, "CONFIG_FILE", Path(d, "config.json")):
                Path(d, "config.json").write_text(json.dumps({"voice": "x", "mine": 1}))
                config.save_config({"listen_mode": "type"})
                got = json.loads(Path(d, "config.json").read_text())
                self.assertEqual(got, {"voice": "x", "mine": 1, "listen_mode": "type"})
                self.assertEqual(config.load_config()["listen_mode"], "type")

    def test_defaults(self):
        self.assertEqual(config.DEFAULTS["listen_mode"], "new")
        self.assertFalse(config.DEFAULTS["listen_press_enter"])
        self.assertIn(config.DEFAULTS["stt_model"], stt.MODELS)

    def test_listening_command(self):
        ctl = mock.Mock()
        srv = control.Server.__new__(control.Server)
        srv.ctl = ctl
        self.assertEqual(srv._handle("listening on"), "ok")
        ctl.set_listening.assert_called_with("on")
        self.assertEqual(srv._handle("listening sideways"), "unknown command")


class ControllerListeningTests(unittest.TestCase):
    def make(self, **cfg):
        from cursor_speak import controller

        base = dict(config.DEFAULTS, **cfg)
        with mock.patch.object(controller, "Speaker"), mock.patch.object(controller, "Watcher"), \
                mock.patch.object(controller.Controller, "_load_last"):
            ctl = controller.Controller(base)
        ctl._save_last = lambda: None
        return ctl

    def reply(self, text="Done. The tests pass."):
        from cursor_speak.chatstore import Reply

        return Reply(id="r" + str(abs(hash(text))), chat_id="c", text=text, prompt="p", prompt_ts=None, chat_dir=Path("/nonexistent"))

    def test_reply_held_while_talking_then_spoken(self):
        ctl = self.make()
        ctl.set_listening("on")
        ctl.speaker.stop.assert_called()
        ctl._on_reply(self.reply())
        ctl.speaker.speak.assert_not_called()
        self.assertEqual(ctl.status_line(), "Listening to you…")
        ctl.set_listening("off")
        ctl.speaker.speak.assert_called_once()

    def test_option_off_keeps_speaking(self):
        ctl = self.make(pause_tts_while_listening=False)
        ctl.set_listening("on")
        ctl._on_reply(self.reply())
        ctl.speaker.speak.assert_called_once()


class ListenFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch.object(listen, "pid_file", lambda: Path(self.tmp.name, "listen.pid")),
            mock.patch.object(listen, "_tell_app", lambda state: None),
        ]
        for p in self.patches:
            p.start()
        self.out = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_first_use_starts_background_setup(self):
        with mock.patch.object(stt, "is_ready", return_value=False), \
                mock.patch.object(stt, "start_background_setup", return_value=True) as bg:
            rc = listen.run(dict(config.DEFAULTS), wav="x.wav", notify=False, out=self.out.append)
        self.assertEqual(rc, 3)
        bg.assert_called_once_with("base.en")

    def test_file_is_transcribed_and_delivered(self):
        with mock.patch.object(stt, "is_ready", return_value=True), \
                mock.patch.object(stt, "transcribe", return_value="open the readme"), \
                mock.patch.object(deliver, "deliver", return_value=deliver.Result(True, "new")) as d:
            rc = listen.run(dict(config.DEFAULTS), mode="new", wav="x.wav", notify=False, out=self.out.append)
        self.assertEqual(rc, 0)
        d.assert_called_once()
        self.assertEqual(d.call_args.args[0], "open the readme")

    def test_empty_transcript_sends_nothing(self):
        with mock.patch.object(stt, "is_ready", return_value=True), \
                mock.patch.object(stt, "transcribe", return_value=""), \
                mock.patch.object(deliver, "deliver") as d:
            rc = listen.run(dict(config.DEFAULTS), wav="x.wav", notify=False, out=self.out.append)
        self.assertEqual(rc, 4)
        d.assert_not_called()

    def test_stale_pid_file_is_ignored(self):
        Path(self.tmp.name, "listen.pid").write_text("999999999")
        self.assertIsNone(listen.active_pid())
        self.assertFalse(listen.stop_active())

    def test_toggle_signals_running_recorder(self):
        Path(self.tmp.name, "listen.pid").write_text("4242")
        with mock.patch.object(listen, "active_pid", return_value=4242), mock.patch("os.kill") as k:
            rc = listen.run(dict(config.DEFAULTS), toggle=True, notify=False, out=self.out.append)
        self.assertEqual(rc, 0)
        k.assert_called_once()
        self.assertEqual(k.call_args.args[0], 4242)


class TranscribeEndToEndTests(unittest.TestCase):
    """Silent: piper renders to a file, faster-whisper reads it. Skipped unless both are set up."""

    def test_rendered_speech_round_trip(self):
        model = config.load_config().get("stt_model", "base.en")
        piper = config.VENV_DIR / "bin" / "piper"
        voice = config.VOICES_DIR / "en_US-lessac-medium.onnx"
        if not (stt.is_ready(model) and piper.exists() and voice.exists()):
            self.skipTest("local speech models not set up")
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d, "speech.wav")
            subprocess.run([str(piper), "-m", str(voice), "-f", str(wav)],
                           input="Open a new terminal and run the unit tests.", text=True,
                           check=True, capture_output=True, timeout=120)
            text = stt.transcribe(wav, model).lower()
        for word in ("terminal", "unit tests"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
