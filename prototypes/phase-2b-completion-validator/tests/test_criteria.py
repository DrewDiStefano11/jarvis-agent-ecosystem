import unittest
from jarvis_completion_validator.schema import load_task_review_input
from jarvis_completion_validator.normalization import normalize_task_input
from jarvis_completion_validator.criteria import evaluate_criteria
from jarvis_completion_validator.enums import CriterionStatus
from tests.helpers import merge_task_and_result

class TestCriteria(unittest.TestCase):
    def test_evaluate_valid_criteria(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        input_data = load_task_review_input(data)

        evaluated, findings = evaluate_criteria(input_data)

        # criterion-001 and 002 are met, 003 is manual review
        c1 = next(c for c in evaluated if c.criterion_id == "criterion-001")
        self.assertEqual(c1.status, CriterionStatus.MET)

        c3 = next(c for c in evaluated if c.criterion_id == "criterion-003")
        self.assertEqual(c3.status, CriterionStatus.MANUAL_REVIEW_REQUIRED)

    def test_evaluate_missing_evidence(self):
        data = normalize_task_input(merge_task_and_result("task-envelope-example", "incomplete-result"))
        input_data = load_task_review_input(data)

        evaluated, findings = evaluate_criteria(input_data)
        c1 = next(c for c in evaluated if c.criterion_id == "criterion-001")
        self.assertEqual(c1.status, CriterionStatus.UNMET)
        self.assertTrue(any(f.related_criterion_id == "criterion-001" for f in findings))

if __name__ == "__main__":
    unittest.main()
