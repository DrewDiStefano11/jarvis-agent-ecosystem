import unittest
import json
from jarvis_completion_validator.reporting import generate_report, ReportEncoder
from tests.helpers import merge_task_and_result

class TestReporting(unittest.TestCase):
    def test_generate_report(self):
        raw_data = merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts")
        report = generate_report(raw_data)

        self.assertEqual(report.task_id, "task-example-001")
        self.assertIsNotNone(report.score.total)
        self.assertIsNotNone(report.actual_result.recommendation)

        # Test serialization
        report_json = json.dumps(report, cls=ReportEncoder)
        self.assertIn("task-example-001", report_json)

    def test_redaction_in_report(self):
        raw_data = merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts")
        # Add sensitive info
        raw_data["artifacts"][0]["validation_metadata"]["api_key"] = "supersecret"

        report = generate_report(raw_data)

        self.assertEqual(report.artifacts[0]["validation_metadata"]["api_key"], "[REDACTED]")

if __name__ == "__main__":
    unittest.main()
