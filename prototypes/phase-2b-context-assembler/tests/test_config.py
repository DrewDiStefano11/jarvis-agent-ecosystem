import unittest
import os
import json
from jarvis_context_assembler.config import load_policy, load_task, load_sources

class TestConfig(unittest.TestCase):
    def test_load_policy(self):
        pol = load_policy("examples/example-policy.json")
        self.assertEqual(pol.policy_version, "phase-2b-context-prototype-1")
        self.assertEqual(pol.estimated_token_budget, 8192)

    def test_load_task(self):
        task = load_task("examples/example-task.json")
        self.assertEqual(task.task_id, "task-example-001")
        self.assertEqual(task.project_id, "jarvis-agent-ecosystem")

    def test_load_sources(self):
        srcs = load_sources("examples/example-context-sources.json")
        self.assertEqual(len(srcs), 1)
        self.assertEqual(srcs[0].source_id, "source-001")
        self.assertEqual(srcs[0].metadata.project_id, "jarvis-agent-ecosystem")
