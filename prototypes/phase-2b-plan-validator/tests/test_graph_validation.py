import unittest
from jarvis_plan_validator.graph_validation import validate_dependency_graph
from jarvis_plan_validator.contracts import ExecutionPlan, ExecutionStep, FinalOutput
from jarvis_plan_validator.enums import StepType, FinalOutputType, FinalOutputFormat

class TestGraphValidation(unittest.TestCase):
    def test_cycle(self):
        plan = ExecutionPlan(
            schema_version="1.0",
            plan_id="plan-1",
            task_id="task-1",
            objective="obj",
            completion_criteria=["done"],
            steps=[
                ExecutionStep("step-1", "s1", "d1", StepType.TOOL, ["step-2"], "out"),
                ExecutionStep("step-2", "s2", "d2", StepType.TOOL, ["step-1"], "out")
            ],
            final_output=FinalOutput(FinalOutputType.ARTIFACT, FinalOutputFormat.MARKDOWN)
        )
        valid, order, cycles, errors = validate_dependency_graph(plan)
        self.assertFalse(valid)
        self.assertTrue(len(cycles) > 0)

    def test_missing_dependency(self):
         plan = ExecutionPlan(
            schema_version="1.0",
            plan_id="plan-1",
            task_id="task-1",
            objective="obj",
            completion_criteria=["done"],
            steps=[
                ExecutionStep("step-1", "s1", "d1", StepType.TOOL, ["step-missing"], "out")
            ],
            final_output=FinalOutput(FinalOutputType.ARTIFACT, FinalOutputFormat.MARKDOWN)
         )
         valid, order, cycles, errors = validate_dependency_graph(plan)
         self.assertFalse(valid)
         self.assertTrue(any("unknown step" in e for e in errors))
