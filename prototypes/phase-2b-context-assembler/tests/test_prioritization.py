import unittest
from jarvis_context_assembler.prioritization import sort_sources
from tests.helpers import create_mock_source

class TestPrioritization(unittest.TestCase):
    def test_trust_order(self):
        s1 = create_mock_source(source_id="s1", trust_level="prior_model_output")
        s2 = create_mock_source(source_id="s2", trust_level="system_policy")
        sorted_srcs = sort_sources([s1, s2])
        self.assertEqual(sorted_srcs[0].source_id, "s2")
        self.assertEqual(sorted_srcs[1].source_id, "s1")

    def test_exact_required(self):
        s1 = create_mock_source(source_id="s1", trust_level="repository_content", exact=False)
        s2 = create_mock_source(source_id="s2", trust_level="repository_content", exact=True)
        sorted_srcs = sort_sources([s1, s2])
        self.assertEqual(sorted_srcs[0].source_id, "s2")
