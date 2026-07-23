import unittest
from datetime import datetime, timedelta, timezone
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.policy_validation import validate_approvals_and_policy
from tests.helpers import merge_task_and_result

class TestPolicyValidation(unittest.TestCase):
    def test_unapproved_patch(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "policy-violation-result"))
        input_data = load_task_review_input(data)
        findings = validate_approvals_and_policy(input_data)
        self.assertTrue(any("unapproved patch" in f.summary.lower() for f in findings))

    def test_pending_approval(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "approval-required-result"))
        input_data = load_task_review_input(data)
        findings = validate_approvals_and_policy(input_data)
        self.assertTrue(any("is pending" in f.summary for f in findings))

    def test_expired_approval(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "approval-required-result"))
        data["approvals"][0]["status"] = "approved"
        data["approvals"][0]["expiration"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        input_data = load_task_review_input(data)
        findings = validate_approvals_and_policy(input_data)
        self.assertTrue(any("has expired" in f.summary for f in findings))

    def test_wrong_task_approval(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "approval-required-result"))
        data["approvals"][0]["task_id"] = "different-task"
        input_data = load_task_review_input(data)
        findings = validate_approvals_and_policy(input_data)
        self.assertTrue(any("different task" in f.summary for f in findings))

    def test_rejected_approval(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "approval-required-result"))
        data["approvals"][0]["status"] = "rejected"
        input_data = load_task_review_input(data)
        findings = validate_approvals_and_policy(input_data)
        self.assertTrue(any("was rejected" in f.summary for f in findings))

if __name__ == "__main__":
    unittest.main()
