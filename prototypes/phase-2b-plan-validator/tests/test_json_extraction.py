import unittest
from jarvis_plan_validator.json_extraction import extract_json_from_model_response, JSONExtractionError

class TestJSONExtraction(unittest.TestCase):
    def test_pure_json(self):
        js = '{"foo": "bar"}'
        parsed, extra, method = extract_json_from_model_response(js)
        self.assertEqual(parsed, {"foo": "bar"})
        self.assertFalse(extra)
        self.assertEqual(method, "pure_json")

    def test_fenced_json(self):
        js = 'Here is the plan:\n```json\n{"foo": "bar"}\n```\nDone.'
        parsed, extra, method = extract_json_from_model_response(js)
        self.assertEqual(parsed, {"foo": "bar"})
        self.assertTrue(extra)
        self.assertEqual(method, "fenced_json")

    def test_strict_mode(self):
        js = 'Here is the plan:\n```json\n{"foo": "bar"}\n```\nDone.'
        with self.assertRaises(JSONExtractionError):
            extract_json_from_model_response(js, strict=True)
