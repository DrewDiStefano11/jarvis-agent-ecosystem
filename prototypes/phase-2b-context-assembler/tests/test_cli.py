import unittest
import sys
from io import StringIO
from unittest.mock import patch
from jarvis_context_assembler.cli import main

class TestCLI(unittest.TestCase):
    @patch('sys.argv', ['cli', 'validate-policy', '--policy', 'examples/example-policy.json'])
    def test_validate_policy(self):
        with self.assertRaises(SystemExit) as cm:
            main()
        self.assertEqual(cm.exception.code, 0)

    @patch('sys.argv', ['cli', 'validate-policy', '--policy', 'missing.json'])
    @patch('sys.stdout', new_callable=StringIO)
    def test_validate_policy_missing(self, mock_stdout):
        with self.assertRaises(SystemExit) as cm:
            main()
        self.assertEqual(cm.exception.code, 7)
