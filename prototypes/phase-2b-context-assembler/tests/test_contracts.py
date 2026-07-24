import unittest
from jarvis_context_assembler.contracts import ContextSource, ContextSourceMetadata

class TestContracts(unittest.TestCase):
    def test_context_source_dataclass(self):
        meta = ContextSourceMetadata(project_id="test-proj", approved=True)
        src = ContextSource(
            source_id="s1",
            source_type="repository_file",
            trust_level="repository_content",
            title="Title",
            content="Content",
            content_hash="hash",
            metadata=meta
        )
        self.assertEqual(src.source_id, "s1")
        self.assertEqual(src.metadata.project_id, "test-proj")
        self.assertTrue(src.metadata.approved)
