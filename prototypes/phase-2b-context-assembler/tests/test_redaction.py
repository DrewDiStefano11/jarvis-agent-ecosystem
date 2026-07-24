import unittest
from jarvis_context_assembler.redaction import redact_sensitive_data

class TestRedaction(unittest.TestCase):
    def test_bearer_token(self):
        content = "Authorization: Bearer my-secret-token"
        redacted, f = redact_sensitive_data(content)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("my-secret-token", redacted)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["category"], "bearer_token")

    def test_api_key(self):
        content = "OPENAI_API_KEY=sk-1234567890abcdef"
        redacted, f = redact_sensitive_data(content)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("sk-1234567890abcdef", redacted)
        self.assertEqual(f[0]["category"], "api_key")

    def test_ordinary_secret_word(self):
        content = "This is my secret recipe."
        redacted, f = redact_sensitive_data(content)
        self.assertNotIn("[REDACTED]", redacted)
        self.assertEqual(len(f), 0)

    def test_private_key(self):
        content = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        redacted, f = redact_sensitive_data(content)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("MIIEowIBAAKCAQEA", redacted)
        self.assertEqual(f[0]["category"], "private_key")
