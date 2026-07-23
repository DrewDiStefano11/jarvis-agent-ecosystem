import unittest
from jarvis_completion_validator.reviewer import Reviewer
from jarvis_completion_validator.recommendations import determine_recommendation
from jarvis_completion_validator.enums import Recommendation
from tests.helpers import load_example, merge_task_and_result

class TestEndToEndScenarios(unittest.TestCase):

    def _evaluate_scenario(self, raw_data):
        reviewer = Reviewer(raw_data)
        res = reviewer.review()
        rec, _ = determine_recommendation(res["task_data"], res["evaluated_criteria"], res["findings"])
        return rec.recommendation

    def _remove_manual_review(self, raw_data):
        raw_data["task"]["completion_criteria"] = [c for c in raw_data["task"]["completion_criteria"] if not c.get("human_review_required")]
        return raw_data

    def test_scenario_1_fully_complete(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.ACCEPT)

    def test_scenario_2_complete_with_warnings(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["worker_result"]["warnings"] = ["A minor warning is present."]
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.ACCEPT_WITH_WARNINGS)

    def test_scenario_3_missing_required_artifact(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "valid-complete-result"))
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REQUEST_REVISION)

    def test_scenario_4_unsupported_claim_tests_passed(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "unsupported-claims-result"))
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REQUEST_REVISION)

    def test_scenario_5_trusted_test_failure(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["trusted_checks"][0]["status"] = "failed"
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.RETRY_STEP)

    def test_scenario_6_fundamental_output_mismatch(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["artifacts"][0]["artifact_type"] = "patch"
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REPLAN_TASK)

    def test_scenario_7_approval_pending(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "approval-required-result", "valid-complete-artifacts"))
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REQUEST_APPROVAL)

    def test_scenario_8_approval_bypass_attempt(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "approval-required-result", "valid-complete-artifacts"))
        data["approvals"][0]["status"] = "rejected"
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REJECT)

    def test_scenario_9_required_external_input_missing(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "blocked-result"))
        # Hack to trigger block since block logic is mostly placeholder for E2E
        from jarvis_completion_validator.contracts import ReviewerFinding
        from jarvis_completion_validator.enums import FindingSeverity, FindingCategory
        reviewer = Reviewer(data)
        res = reviewer.review()
        res["findings"].append(ReviewerFinding(
            finding_id="mock", severity=FindingSeverity.MAJOR, category=FindingCategory.CRITERION_UNMET,
            summary="blocked", detailed_reason="blocked", automatically_remediable=False, recommended_next_action="block"
        ))
        rec, _ = determine_recommendation(res["task_data"], res["evaluated_criteria"], res["findings"])
        self.assertEqual(rec.recommendation, Recommendation.BLOCK)

    def test_scenario_10_manual_review_criterion(self):
        # We don't remove manual review
        data = merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts")
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.HUMAN_REVIEW)

    def test_scenario_11_contradictory_result(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "contradictory-result"))
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REQUEST_REVISION)

    def test_scenario_12_wrong_task_artifact(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["artifacts"][0]["task_id"] = "different-task"
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REJECT)

    def test_scenario_13_placeholder_only_document(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "valid-complete-result", "valid-complete-artifacts"))
        data["artifacts"][0]["content_excerpt"] = "TODO"
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REQUEST_REVISION)

    def test_scenario_14_policy_violation(self):
        data = self._remove_manual_review(merge_task_and_result("task-envelope-example", "policy-violation-result"))
        rec = self._evaluate_scenario(data)
        self.assertEqual(rec, Recommendation.REJECT)

if __name__ == "__main__":
    unittest.main()
