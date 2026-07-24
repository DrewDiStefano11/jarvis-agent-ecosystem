import unittest
from jarvis_context_assembler.deduplication import deduplicate_sources
from tests.helpers import create_mock_source

class TestDeduplication(unittest.TestCase):
    def test_exact_duplicate(self):
        src1 = create_mock_source(source_id="s1", content="same content")
        src2 = create_mock_source(source_id="s2", content="same content")
        deduped, removed = deduplicate_sources([src1, src2])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(len(removed), 1)
        self.assertEqual(deduped[0].source_id, "s1")
        self.assertEqual(removed[0]["reason"], "duplicate")

    def test_higher_trust_wins(self):
        src1 = create_mock_source(source_id="s1", content="same content", trust_level="prior_model_output")
        src2 = create_mock_source(source_id="s2", content="same content", trust_level="system_policy")
        deduped, removed = deduplicate_sources([src1, src2])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source_id, "s2")
        self.assertEqual(removed[0]["reason"], "duplicate_replaced_by_higher_trust")

    def test_exact_preservation_wins(self):
        src1 = create_mock_source(source_id="s1", content="same content", trust_level="repository_content", exact=False)
        src2 = create_mock_source(source_id="s2", content="same content", trust_level="repository_content", exact=True)
        deduped, removed = deduplicate_sources([src1, src2])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].source_id, "s2")
