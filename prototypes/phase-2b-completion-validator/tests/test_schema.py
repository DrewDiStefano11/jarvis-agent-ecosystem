import unittest
import json
from pathlib import Path
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.errors import SchemaValidationError
from jarvis_completion_validator.normalization import normalize_task_input
from tests.helpers import load_example, merge_task_and_result

class TestSchemaValidation(unittest.TestCase):
    def setUp(self):
        self.valid_data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))

    def test_valid_input_parses(self):
        input_data = load_task_review_input(self.valid_data)
        self.assertEqual(input_data.task.task_id, "task-example-001")
        self.assertEqual(len(input_data.task.completion_criteria), 3)

    def test_missing_required_field_raises(self):
        del self.valid_data["worker_result"]
        with self.assertRaises(SchemaValidationError):
            load_task_review_input(self.valid_data)

    def test_invalid_enum_raises(self):
        self.valid_data["task"]["completion_criteria"][0]["criterion_type"] = "unknown_type"
        with self.assertRaises(SchemaValidationError):
            load_task_review_input(self.valid_data)

    def test_json_schemas_parse(self):
        # Verify that all schema files are valid JSON
        schemas_dir = Path(__file__).parent.parent / "schemas"
        for schema_file in schemas_dir.glob("*.schema.json"):
            with self.subTest(schema=schema_file.name):
                with open(schema_file, 'r') as f:
                    data = json.load(f)
                    self.assertIn("$schema", data)

    def test_duplicate_ids_handling(self):
        # Prototype dataclasses handle duplicate IDs blindly for now, but
        # validation logic relies on dicts/sets. This test ensures it doesn't crash.
        self.valid_data["artifacts"].append(self.valid_data["artifacts"][0])
        input_data = load_task_review_input(self.valid_data)
        self.assertEqual(len(input_data.artifacts), 2)

    def test_oversized_string_handling(self):
        # We don't strictly enforce string max lengths in dataclass parsing,
        # but ensure no crash.
        self.valid_data["task"]["title"] = "A" * 10000
        input_data = load_task_review_input(self.valid_data)
        self.assertEqual(len(input_data.task.title), 10000)

if __name__ == "__main__":
    unittest.main()
