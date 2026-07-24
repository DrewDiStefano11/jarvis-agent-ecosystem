import unittest
from jarvis_context_assembler.truncation import truncate_content
from jarvis_context_assembler.enums import TruncationStrategy
from jarvis_context_assembler.token_budget import estimate_tokens

class TestTruncation(unittest.TestCase):
    def test_no_truncation_needed(self):
        content = "a" * 10
        out, rejected = truncate_content(content, 100)
        self.assertFalse(rejected)
        self.assertEqual(out, content)

    def test_reject_strategy(self):
        content = "a" * 100
        out, rejected = truncate_content(content, 10, TruncationStrategy.REJECT)
        self.assertTrue(rejected)

    def test_tail_truncation(self):
        content = "a" * 100
        out, rejected = truncate_content(content, 15, TruncationStrategy.TAIL)
        self.assertFalse(rejected)
        self.assertIn("[TRUNCATED]", out)
        self.assertTrue(out.startswith("a"))

    def test_head_truncation(self):
        content = "a" * 100
        out, rejected = truncate_content(content, 15, TruncationStrategy.HEAD)
        self.assertFalse(rejected)
        self.assertIn("[TRUNCATED]", out)
        self.assertTrue(out.endswith("a"))

    def test_head_and_tail(self):
        content = "a" * 50 + "b" * 50
        out, rejected = truncate_content(content, 20, TruncationStrategy.HEAD_AND_TAIL)
        self.assertFalse(rejected)
        self.assertIn("[TRUNCATED]", out)
        self.assertTrue(out.startswith("a"))
        self.assertTrue(out.endswith("b"))
