import unittest
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.contradiction_detection import detect_contradictions
from tests.helpers import merge_task_and_result

class TestContradictions(unittest.TestCase):
    def test_completed_with_errors(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "contradictory-result"))
        input_data = load_task_review_input(data)
        findings, _ = detect_contradictions(input_data)
        self.assertTrue(any("completed but reports errors" in f.summary for f in findings))

    def test_completed_missing_artifact(self):
        # contradictory result is missing artifacts
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "contradictory-result"))
        input_data = load_task_review_input(data)
        findings, _ = detect_contradictions(input_data)
        self.assertTrue(any("missing required artifact" in f.summary for f in findings))

    def test_no_limitations_with_warnings(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result"))
        data["worker_result"]["summary"] = "Done with no limitations."
        data["worker_result"]["warnings"] = ["Some minor issue"]
        input_data = load_task_review_input(data)
        findings, _ = detect_contradictions(input_data)
        self.assertTrue(any("no limitations but reports warnings" in f.summary for f in findings))

    def test_completed_failed_check(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result"))
        data["trusted_checks"][0]["status"] = "failed"
        input_data = load_task_review_input(data)
        findings, _ = detect_contradictions(input_data)
        self.assertTrue(any("check check-tests-001 failed" in f.summary for f in findings))

if __name__ == "__main__":
    unittest.main()
