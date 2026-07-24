import unittest
from jarvis_context_assembler.manifest import ContextManifest
from jarvis_context_assembler.reporting import generate_assembly_report

class TestReporting(unittest.TestCase):
    def test_generate_report(self):
        m = ContextManifest(
            manifest_id="m1",
            budget={"estimated_total_tokens": 100, "maximum_context_tokens": 200, "reserved_output_tokens": 50}
        )
        m.included_sources.append({"source_id": "s1", "size": 1000})
        m.injection_findings.append({"severity": "critical"})

        rep = generate_assembly_report(m, "req-1")
        self.assertEqual(rep["included_source_count"], 1)
        self.assertEqual(rep["included_bytes"], 1000)
        self.assertTrue(rep["human_review_requirement"])
