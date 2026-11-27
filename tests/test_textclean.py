import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cursor_speak import textclean as t  # noqa: E402


class TextCleanTests(unittest.TestCase):
    def test_fenced_code_dropped(self):
        out = t.clean("Run this:\n\n```bash\nsudo rm -rf /\n```\n\nThen relax.")
        self.assertNotIn("rm", out)
        self.assertIn("Then relax.", out)

    def test_unterminated_fence_drops_rest(self):
        self.assertEqual(t.clean("Intro.\n```\ncode forever"), "Intro.")

    def test_markdown_symbols_removed(self):
        out = t.clean("This is **bold** and *italic* and ~~gone~~ [a link](http://x.y/z).")
        self.assertEqual(out, "This is bold and italic and gone a link.")

    def test_headings_and_bullets_become_sentences(self):
        out = t.clean("### What changed\n- first thing\n- second thing\n1. numbered item")
        self.assertEqual(out.split("\n\n"), ["What changed.", "first thing.", "second thing.", "numbered item."])

    def test_inline_paths_and_commands_dropped(self):
        out = t.clean("Edit `Assets/Scripts/Foo.cs` then run `git status -s` and open foo.png.")
        self.assertNotIn("Assets", out)
        self.assertNotIn("git", out)
        self.assertNotIn("png", out)

    def test_identifiers_read_as_words(self):
        self.assertIn("Player Input", t.clean("Fix in `PlayerInput`."))
        self.assertIn("Center Camera On Map", t.clean("It calls `CenterCameraOnMap()`."))
        self.assertNotIn("Player", t.clean("Fix in `PlayerInput`.", identifiers="drop"))

    def test_question_mark_code(self):
        self.assertIn("question mark", t.clean("The `?` markers"))

    def test_urls_dropped(self):
        self.assertEqual(t.clean("See https://example.com/a/b now."), "See now.")

    def test_table_header_skipped_rows_read(self):
        out = t.clean("| File | What |\n|---|---|\n| `a.png` | Shows the camp |")
        self.assertEqual(out, "Shows the camp.")

    def test_no_period_inside_parentheses(self):
        self.assertEqual(t.clean("### Fix (`PlayerInput`)"), "Fix (Player Input).")

    def test_ranges_and_symbols(self):
        out = t.clean("about 6–48m, ~45s, 32% done → ok")
        self.assertIn("6 to 48m", out)
        self.assertIn("about 45s", out)
        self.assertIn("32 percent", out)
        self.assertIn("to ok", out)

    def test_emoji_removed(self):
        self.assertEqual(t.clean("All done 🎉"), "All done.")

    def test_only_code_reply_is_empty(self):
        self.assertEqual(t.clean("```\nonly code\n```"), "")

    def test_limit_cuts_at_sentence(self):
        out = t.clean("One sentence here. " * 50, max_chars=100)
        self.assertTrue(out.endswith("The rest is in the terminal."))
        self.assertLess(len(out), 150)

    def test_chunks_stay_small(self):
        chunks = t.split_chunks("Word " * 200 + ".")
        self.assertTrue(all(len(c) <= 300 for c in chunks))


if __name__ == "__main__":
    unittest.main()
