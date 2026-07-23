import unittest
from jarvis_completion_validator.redaction import redact_dict

class TestRedaction(unittest.TestCase):
    def test_redact_secrets(self):
        data = {
            "public_info": "safe",
            "api_key": "sk-12345",
            "metadata": {
                "user_password": "supersecret",
                "nested": {
                    "token": "abc"
                }
            },
            "list_of_stuff": [{"session_cookie": "xyz"}, {"normal": "ok"}]
        }

        redacted = redact_dict(data)

        self.assertEqual(redacted["public_info"], "safe")
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["metadata"]["user_password"], "[REDACTED]")
        self.assertEqual(redacted["metadata"]["nested"]["token"], "[REDACTED]")
        self.assertEqual(redacted["list_of_stuff"][0]["session_cookie"], "[REDACTED]")
        self.assertEqual(redacted["list_of_stuff"][1]["normal"], "ok")

if __name__ == "__main__":
    unittest.main()
