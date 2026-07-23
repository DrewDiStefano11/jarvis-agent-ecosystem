import unittest
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.artifact_validation import validate_artifacts
from tests.helpers import merge_task_and_result

class TestArtifactValidation(unittest.TestCase):
    def test_valid_artifact(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        input_data = load_task_review_input(data)
        findings = validate_artifacts(input_data)
        self.assertEqual(len(findings), 0)

    def test_missing_required_artifact(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result"))
        # artifacts empty in merge if not provided
        input_data = load_task_review_input(data)
        findings = validate_artifacts(input_data)
        self.assertTrue(any("Missing required artifact" in f.summary for f in findings))

    def test_wrong_type(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["artifacts"][0]["artifact_type"] = "image"
        input_data = load_task_review_input(data)
        findings = validate_artifacts(input_data)
        self.assertTrue(any("wrong type" in f.summary.lower() for f in findings))

    def test_wrong_task(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["artifacts"][0]["task_id"] = "other-task"
        input_data = load_task_review_input(data)
        findings = validate_artifacts(input_data)
        self.assertTrue(any("different task" in f.summary for f in findings))

    def test_placeholder_content(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["artifacts"][0]["content_excerpt"] = "TODO: add summary"
        input_data = load_task_review_input(data)
        findings = validate_artifacts(input_data)
        self.assertTrue(any("placeholder" in f.summary.lower() for f in findings))

    def test_temporary_artifact_final(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["artifacts"][0]["is_temporary"] = True
        input_data = load_task_review_input(data)
        findings = validate_artifacts(input_data)
        self.assertTrue(any("temporary" in f.summary.lower() for f in findings))

if __name__ == "__main__":
    unittest.main()
