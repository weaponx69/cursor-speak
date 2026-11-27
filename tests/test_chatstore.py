import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cursor_speak import chatstore  # noqa: E402
from helpers import FakeChat  # noqa: E402


class ChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.chat = FakeChat(self.root, "ws", "chat-1")

    def tearDown(self):
        self.chat.close()
        self.tmp.cleanup()

    def test_waiting_after_prompt(self):
        self.chat.add("user", "hello")
        self.assertEqual(chatstore.inspect_chat(self.chat.dir).status, "waiting")

    def test_in_progress_with_tool_calls(self):
        self.chat.add("user", "do it")
        self.chat.add("assistant", "Working on it.", tool_call=True)
        self.chat.add("tool", "")
        self.assertEqual(chatstore.inspect_chat(self.chat.dir).status, "in_progress")

    def test_complete_reply_is_last_prose_only_message(self):
        self.chat.add("user", "do it")
        self.chat.add("assistant", "Narration.", tool_call=True)
        self.chat.add("tool", "")
        rid = self.chat.add("assistant", "All done, **really**.")
        turn = chatstore.inspect_chat(self.chat.dir)
        self.assertEqual(turn.status, "complete")
        self.assertEqual(turn.reply.id, rid)
        self.assertEqual(turn.reply.text, "All done, **really**.")
        self.assertEqual(turn.reply.prompt, "do it")

    def test_signature_changes_on_write(self):
        before = chatstore.signature(self.chat.dir)
        self.chat.add("user", "again")
        self.assertNotEqual(before, chatstore.signature(self.chat.dir))

    def test_chat_dirs_found(self):
        self.assertEqual(chatstore.chat_dirs(self.root), [self.chat.dir])

    def test_read_only_never_modifies(self):
        self.chat.add("user", "q")
        self.chat.add("assistant", "a")
        data = (self.chat.dir / "store.db").read_bytes()
        chatstore.inspect_chat(self.chat.dir)
        self.assertEqual(data, (self.chat.dir / "store.db").read_bytes())


if __name__ == "__main__":
    unittest.main()
