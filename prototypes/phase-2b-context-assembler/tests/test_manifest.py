import unittest
from jarvis_context_assembler.manifest import ContextManifest

class TestManifest(unittest.TestCase):
    def test_manifest_defaults(self):
        m = ContextManifest(manifest_id="m1")
        self.assertEqual(m.manifest_id, "m1")
        self.assertEqual(m.schema_version, "1.0")
        self.assertEqual(len(m.included_sources), 0)
