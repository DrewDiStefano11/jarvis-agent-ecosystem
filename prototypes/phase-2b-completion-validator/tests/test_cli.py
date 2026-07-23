import unittest
import subprocess
import json
from pathlib import Path
from jarvis_completion_validator.cli import EXIT_CODES
from jarvis_completion_validator.enums import Recommendation

class TestCLI(unittest.TestCase):
    def setUp(self):
        self.base_cmd = ["python", "-m", "jarvis_completion_validator"]
        self.examples_dir = Path(__file__).parent.parent / "examples"

    def test_help(self):
        result = subprocess.run(self.base_cmd + ["--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Jarvis Completion Validator", result.stdout)

    def test_validate_valid(self):
        cmd = self.base_cmd + [
            "validate",
            "--task", str(self.examples_dir / "task-envelope-example.json"),
            "--result", str(self.examples_dir / "valid-complete-result.json"),
            "--artifacts", str(self.examples_dir / "valid-complete-artifacts.json")
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        # Note: task-envelope-example has a manual review criterion, so it returns HUMAN_REVIEW
        self.assertEqual(result.returncode, EXIT_CODES[Recommendation.HUMAN_REVIEW])
        self.assertIn("Recommendation: human_review", result.stdout)

    def test_validate_incomplete(self):
        # Setup similar temp task to bypass human review
        import copy
        task_data = json.loads((self.examples_dir / "task-envelope-example.json").read_text())
        task_data["task"]["completion_criteria"] = [c for c in task_data["task"]["completion_criteria"] if not c.get("human_review_required")]

        test_task_file = self.examples_dir / "temp-task-2.json"
        test_task_file.write_text(json.dumps(task_data))

        cmd = self.base_cmd + [
            "validate",
            "--task", str(test_task_file),
            "--result", str(self.examples_dir / "incomplete-result.json")
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        test_task_file.unlink() # Cleanup
        self.assertEqual(result.returncode, EXIT_CODES[Recommendation.REQUEST_REVISION])

    def test_schema_command(self):
        cmd = self.base_cmd + ["schema", "--name", "completion-report"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Completion Report", result.stdout)

    def test_invalid_input(self):
        cmd = self.base_cmd + ["validate", "--task", "nonexistent.json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, EXIT_CODES["INVALID_INPUT"])

    def test_json_format(self):
        # We need a task without manual review to get request_revision directly instead of human_review
        import copy
        task_data = json.loads((self.examples_dir / "task-envelope-example.json").read_text())
        task_data["task"]["completion_criteria"] = [c for c in task_data["task"]["completion_criteria"] if not c.get("human_review_required")]

        test_task_file = self.examples_dir / "temp-task.json"
        test_task_file.write_text(json.dumps(task_data))

        cmd = self.base_cmd + [
            "validate",
            "--task", str(test_task_file),
            "--result", str(self.examples_dir / "incomplete-result.json"),
            "--format", "json"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        test_task_file.unlink() # Cleanup
        report = json.loads(result.stdout)
        self.assertEqual(report["actual_result"]["recommendation"], "request_revision")

if __name__ == "__main__":
    unittest.main()
