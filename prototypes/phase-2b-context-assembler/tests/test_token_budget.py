import unittest
from jarvis_context_assembler.token_budget import estimate_tokens, BudgetTracker

class TestTokenBudget(unittest.TestCase):
    def test_estimate_tokens(self):
        # 14 chars / 3.5 = 4
        self.assertEqual(estimate_tokens("This is a test"), 4)

    def test_budget_tracker_under(self):
        b = BudgetTracker(100, 20)
        self.assertTrue(b.can_fit("a" * 35)) # ~10 tokens

    def test_budget_tracker_over(self):
        b = BudgetTracker(100, 90) # available 10
        self.assertFalse(b.can_fit("a" * 100)) # ~29 tokens

    def test_budget_add(self):
        b = BudgetTracker(100, 0)
        b.add("a" * 35) # +10
        self.assertEqual(b.used_tokens, 10)
        self.assertEqual(b.available_tokens, 90)
