import unittest
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.claim_validation import validate_unsupported_claims
from tests.helpers import merge_task_and_result

class TestClaimValidation(unittest.TestCase):
    def test_unsupported_tests_passed_claim(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "unsupported-claims-result"))
        input_data = load_task_review_input(data)
        findings = validate_unsupported_claims(input_data)
        self.assertTrue(any("tests passed without trusted evidence" in f.summary for f in findings))
        self.assertTrue(any("without artifact evidence" in f.summary for f in findings))

    def test_approval_claim_without_record(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result"))
        data["worker_result"]["approval_claims"].append("app-123")
        input_data = load_task_review_input(data)
        findings = validate_unsupported_claims(input_data)
        self.assertTrue(any("Worker claims approval app-123 but no record exists" in f.summary for f in findings))

if __name__ == "__main__":
    unittest.main()
