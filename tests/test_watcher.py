import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cursor_speak import config  # noqa: E402
from cursor_speak.watcher import Watcher  # noqa: E402
from helpers import FakeChat  # noqa: E402

CFG = {"poll_seconds": 0.05, "quiet_seconds": 0.0}


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = config.CHATS_ROOT
        config.CHATS_ROOT = self.root
        self.got = []

    def tearDown(self):
        config.CHATS_ROOT = self._old
        self.tmp.cleanup()

    def watcher(self):
        return Watcher(CFG, self.got.append)

    def test_old_replies_not_spoken_at_startup(self):
        c = FakeChat(self.root, "ws", "a")
        c.add("user", "q")
        c.add("assistant", "old answer")
        w = self.watcher()
        w.seed()
        w._tick()
        self.assertEqual(self.got, [])
        c.close()

    def test_new_reply_spoken_exactly_once(self):
        c = FakeChat(self.root, "ws", "a")
        c.add("user", "q1")
        c.add("assistant", "old answer")
        w = self.watcher()
        w.seed()
        c.add("user", "q2")
        c.add("assistant", "thinking", tool_call=True)
        c.add("tool", "")
        w._tick()
        self.assertEqual(self.got, [], "in-progress text must not be spoken")
        c.add("assistant", "the new answer")
        w._tick()
        w._tick()
        self.assertEqual([r.text for r in self.got], ["the new answer"])
        c.close()

    def test_new_chat_session_is_picked_up(self):
        w = self.watcher()
        w.seed()
        c = FakeChat(self.root, "ws2", "brand-new")
        c.add("user", "hi")
        c.add("assistant", "hello there")
        w._tick()
        self.assertEqual([r.text for r in self.got], ["hello there"])
        c.close()

    def test_duplicate_copies_of_same_chat_speak_once(self):
        a = FakeChat(self.root, "ws1", "same")
        b = FakeChat(self.root, "ws2", "same")
        w = self.watcher()
        w.seed()
        for c in (a, b):
            c.add("user", "q")
            c.add("assistant", "identical reply")
        w._tick()
        self.assertEqual(len(self.got), 1)
        a.close()
        b.close()

    def test_quiet_window_delays_reply(self):
        c = FakeChat(self.root, "ws", "a")
        w = Watcher({"poll_seconds": 0.05, "quiet_seconds": 0.3}, self.got.append)
        w.seed()
        c.add("user", "q")
        c.add("assistant", "answer")
        w._tick()
        self.assertEqual(self.got, [])
        time.sleep(0.35)
        w._tick()
        self.assertEqual(len(self.got), 1)
        c.close()


if __name__ == "__main__":
    unittest.main()
