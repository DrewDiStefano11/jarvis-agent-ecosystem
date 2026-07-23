import unittest
from jarvis_plan_validator.policy import is_path_safe, is_url_safe
from jarvis_plan_validator.enums import NetworkPolicy

class TestPolicy(unittest.TestCase):
    def test_path_safe(self):
        roots = ["approved/"]
        self.assertTrue(is_path_safe("approved/input.txt", roots))
        self.assertFalse(is_path_safe("../../secrets.txt", roots))
        self.assertFalse(is_path_safe("/etc/passwd", roots))
        self.assertFalse(is_path_safe("C:\\Windows\\System32", roots))

    def test_url_safe(self):
        self.assertTrue(is_url_safe("http://localhost:8000", NetworkPolicy.LOOPBACK))
        self.assertFalse(is_url_safe("http://example.com", NetworkPolicy.LOOPBACK))
        self.assertTrue(is_url_safe("https://example.com", NetworkPolicy.ALL))
        self.assertFalse(is_url_safe("file:///etc/passwd", NetworkPolicy.ALL))
