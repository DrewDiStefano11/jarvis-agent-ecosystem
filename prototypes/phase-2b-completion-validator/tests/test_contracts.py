import unittest
from datetime import datetime
from jarvis_completion_validator.contracts import (
    CompletionCriterion, Evidence, ApprovalRecord
)
from jarvis_completion_validator.enums import (
    CriterionType, VerificationMethod, EvidenceTrustLevel
)

class TestContracts(unittest.TestCase):
    def test_completion_criterion_defaults(self):
        c = CompletionCriterion(
            criterion_id="c1",
            description="test",
            criterion_type=CriterionType.CONTENT,
            required=True,
            verification_method=VerificationMethod.DETERMINISTIC
        )
        self.assertTrue(c.automatic_validation_possible)
        self.assertFalse(c.human_review_required)
        self.assertIsNone(c.severity_if_unmet)

    def test_evidence_instantiation(self):
        e = Evidence(
            evidence_id="e1",
            evidence_type="test",
            source="system",
            trust_level=EvidenceTrustLevel.AUTHORITATIVE,
            timestamp=datetime.now()
        )
        self.assertEqual(e.related_criterion_ids, [])
        self.assertIsNone(e.payload)

    def test_approval_record(self):
        a = ApprovalRecord(
            approval_id="a1",
            task_id="t1",
            action_type="act",
            status="approved",
            risk_level="low",
            scope="public",
            reviewed_by="sys",
            reviewed_at=datetime.now()
        )
        self.assertIsNone(a.expiration)

if __name__ == "__main__":
    unittest.main()
