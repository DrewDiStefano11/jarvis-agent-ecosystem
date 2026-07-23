import unittest
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.evidence import evaluate_evidence_for_criterion
from tests.helpers import merge_task_and_result

class TestEvidence(unittest.TestCase):
    def setUp(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        self.input_data = load_task_review_input(data)
        self.criterion = self.input_data.task.completion_criteria[0]

    def test_trusted_evidence_passes(self):
        is_met, ids, findings = evaluate_evidence_for_criterion(self.criterion, self.input_data)
        self.assertTrue(is_met)
        self.assertEqual(len(findings), 0)

    def test_worker_claim_only_fails(self):
        # Change trust level to worker_claim
        for ev in self.input_data.evidence:
            ev.trust_level = "worker_claim"

        is_met, ids, findings = evaluate_evidence_for_criterion(self.criterion, self.input_data)
        self.assertFalse(is_met)
        self.assertTrue(any("Only untrusted evidence" in f.summary for f in findings))

    def test_missing_evidence(self):
        self.input_data.evidence = []
        self.input_data.worker_result.criterion_claims = []
        is_met, ids, findings = evaluate_evidence_for_criterion(self.criterion, self.input_data)
        self.assertFalse(is_met)
        self.assertTrue(any("No evidence provided" in f.summary for f in findings))

if __name__ == "__main__":
    unittest.main()
