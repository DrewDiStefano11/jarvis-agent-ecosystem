import unittest
from jarvis_context_assembler.provenance import verify_provenance
from jarvis_context_assembler.enums import ExclusionReason
from tests.helpers import create_mock_source

class TestProvenance(unittest.TestCase):
    def test_valid_provenance(self):
        src = create_mock_source(content="test")
        reason = verify_provenance(src)
        self.assertIsNone(reason)

    def test_invalid_hash(self):
        src = create_mock_source(content="test", content_hash="bad_hash")
        reason = verify_provenance(src)
        self.assertEqual(reason, ExclusionReason.INVALID_HASH)

    def test_missing_provenance(self):
        src = create_mock_source(source_id="")
        reason = verify_provenance(src)
        self.assertEqual(reason, ExclusionReason.MISSING_PROVENANCE)
