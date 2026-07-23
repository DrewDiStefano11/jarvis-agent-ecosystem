import unittest
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.reviewer import Reviewer
from jarvis_completion_validator.scoring import calculate_score
from tests.helpers import merge_task_and_result

class TestScoring(unittest.TestCase):
    def test_perfect_score(self):
        raw_data = merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts")
        # Remove manual review for a true perfect score
        raw_data["task"]["completion_criteria"] = [c for c in raw_data["task"]["completion_criteria"] if not c.get("human_review_required")]

        reviewer = Reviewer(raw_data)
        res = reviewer.review()
        score = calculate_score(res["task_data"], res["evaluated_criteria"], res["findings"])

        self.assertEqual(score.total, 100)

    def test_missing_required_cap(self):
        raw_data = merge_task_and_result("task-envelope-example", "incomplete-result")
        reviewer = Reviewer(raw_data)
        res = reviewer.review()
        score = calculate_score(res["task_data"], res["evaluated_criteria"], res["findings"])

        self.assertLessEqual(score.total, 49)

    def test_policy_violation_zero(self):
        raw_data = merge_task_and_result("task-envelope-example", "policy-violation-result", "valid-complete-artifacts")
        reviewer = Reviewer(raw_data)
        res = reviewer.review()
        score = calculate_score(res["task_data"], res["evaluated_criteria"], res["findings"])

        self.assertEqual(score.total, 0)

    def test_missing_approval_cap(self):
        raw_data = merge_task_and_result("task-envelope-example", "approval-required-result", "valid-complete-artifacts")
        reviewer = Reviewer(raw_data)
        res = reviewer.review()
        score = calculate_score(res["task_data"], res["evaluated_criteria"], res["findings"])

        self.assertLessEqual(score.total, 69)

if __name__ == "__main__":
    unittest.main()
