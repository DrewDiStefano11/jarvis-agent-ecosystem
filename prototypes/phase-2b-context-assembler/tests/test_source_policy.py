import unittest
from jarvis_context_assembler.source_policy import validate_source_against_policy
from jarvis_context_assembler.enums import ExclusionReason
from tests.helpers import create_mock_source, create_mock_policy

class TestSourcePolicy(unittest.TestCase):
    def setUp(self):
        self.policy = create_mock_policy()
        self.task_project = "jarvis-agent-ecosystem"

    def test_valid_source(self):
        src = create_mock_source()
        reason = validate_source_against_policy(src, self.policy, self.task_project)
        self.assertIsNone(reason)

    def test_invalid_source_type(self):
        src = create_mock_source(source_type="unsupported_type")
        reason = validate_source_against_policy(src, self.policy, self.task_project)
        self.assertEqual(reason, ExclusionReason.SOURCE_TYPE_DENIED)

    def test_wrong_project(self):
        src = create_mock_source(project_id="other-project")
        reason = validate_source_against_policy(src, self.policy, self.task_project)
        self.assertEqual(reason, ExclusionReason.WRONG_PROJECT)

    def test_not_approved(self):
        src = create_mock_source(approved=False)
        reason = validate_source_against_policy(src, self.policy, self.task_project)
        self.assertEqual(reason, ExclusionReason.NOT_APPROVED)
