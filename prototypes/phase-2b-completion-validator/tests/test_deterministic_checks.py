import unittest
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.deterministic_checks import validate_trusted_checks
from tests.helpers import merge_task_and_result

class TestDeterministicChecks(unittest.TestCase):
    def test_passed_check(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result"))
        input_data = load_task_review_input(data)
        findings = validate_trusted_checks(input_data)
        self.assertEqual(len(findings), 0)

    def test_failed_check(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result"))
        data["trusted_checks"][0]["status"] = "failed"
        input_data = load_task_review_input(data)
        findings = validate_trusted_checks(input_data)
        self.assertTrue(any("failed" in f.summary.lower() for f in findings))

    def test_wrong_task_check(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result"))
        data["trusted_checks"][0]["task_id"] = "other-task"
        input_data = load_task_review_input(data)
        findings = validate_trusted_checks(input_data)
        self.assertTrue(any("different task" in f.summary for f in findings))

if __name__ == "__main__":
    unittest.main()
