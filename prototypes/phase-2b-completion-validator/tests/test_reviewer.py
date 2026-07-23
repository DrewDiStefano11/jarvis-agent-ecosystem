import unittest
from jarvis_completion_validator.reviewer import Reviewer
from tests.helpers import merge_task_and_result

class TestReviewer(unittest.TestCase):
    def test_reviewer_orchestration(self):
        raw_data = merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts")
        reviewer = Reviewer(raw_data)
        results = reviewer.review()

        self.assertIn("task_data", results)
        self.assertIn("evaluated_criteria", results)
        self.assertIn("findings", results)

        # In a valid scenario, there might be a few minor things (like the manual review info)
        # but no critical findings.
        critical_findings = [f for f in results["findings"] if f.severity == "critical"]
        self.assertEqual(len(critical_findings), 0)

if __name__ == "__main__":
    unittest.main()
