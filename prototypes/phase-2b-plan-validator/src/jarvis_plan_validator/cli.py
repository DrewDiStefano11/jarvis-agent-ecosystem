import argparse
import sys
import json
import os
from .validation import validate_plan
from .reporting import generate_json_report, generate_text_report
from .enums import ExitCode, RecommendedAction, ExecutionEligibility
from .policy import TOOL_REGISTRY

def main():
    parser = argparse.ArgumentParser(description="Phase 2B Plan Validator Prototype")
    subparsers = parser.add_subparsers(dest="command")

    # Command: validate
    val_parser = subparsers.add_parser("validate", help="Validate a plan against a task envelope")
    val_parser.add_argument("--task-envelope", required=True, help="Path to the task envelope JSON")
    val_parser.add_argument("--model-response", required=True, help="Path to the model response file (JSON or text)")
    val_parser.add_argument("--strict-json", action="store_true", help="Reject responses with extra prose")
    val_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    val_parser.add_argument("--output", help="Output file path (optional)")
    val_parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists")

    # Command: validate-plan (same as validate but param name is --plan for direct files)
    val_plan_parser = subparsers.add_parser("validate-plan", help="Validate a pure plan file against a task envelope")
    val_plan_parser.add_argument("--task-envelope", required=True, help="Path to the task envelope JSON")
    val_plan_parser.add_argument("--plan", required=True, help="Path to the pure plan JSON file")
    val_plan_parser.add_argument("--strict-json", action="store_true", help="Reject responses with extra prose")
    val_plan_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    val_plan_parser.add_argument("--output", help="Output file path (optional)")
    val_plan_parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists")

    # Command: tools
    tools_parser = subparsers.add_parser("tools", help="Inspect tool policy")

    # Command: schema
    schema_parser = subparsers.add_parser("schema", help="Print JSON schemas")
    schema_parser.add_argument("--name", choices=["execution-plan", "task-envelope", "execution-step", "validation-report"], required=True)

    args = parser.parse_args()

    if args.command == "tools":
        print(f"{'Tool Name':<30} | {'Risk':<10} | {'Approval':<10}")
        print("-" * 60)
        for name, tool in TOOL_REGISTRY.items():
            print(f"{name:<30} | {tool.risk_level.value:<10} | {str(tool.requires_approval):<10}")
        sys.exit(0)

    if args.command == "schema":
        schema_path = os.path.join(os.path.dirname(__file__), "..", "..", "schemas", f"{args.name}.schema.json")
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                print(f.read())
            sys.exit(0)
        except Exception as e:
            print(f"Error loading schema: {e}", file=sys.stderr)
            sys.exit(ExitCode.UNEXPECTED_ERROR)

    if args.command in ["validate", "validate-plan"]:
        try:
            with open(args.task_envelope, "r", encoding="utf-8") as f:
                envelope_dict = json.load(f)
        except Exception as e:
            print(f"Error reading task envelope: {e}", file=sys.stderr)
            sys.exit(ExitCode.CONFIGURATION_ERROR)

        response_path = args.model_response if args.command == "validate" else args.plan
        try:
            with open(response_path, "r", encoding="utf-8") as f:
                response_text = f.read()
        except Exception as e:
            print(f"Error reading model response/plan: {e}", file=sys.stderr)
            sys.exit(ExitCode.CONFIGURATION_ERROR)

        report = validate_plan(
            task_envelope_dict=envelope_dict,
            model_response_text=response_text,
            strict_json=args.strict_json
        )

        if args.format == "json":
            out_str = generate_json_report(report)
        else:
            out_str = generate_text_report(report)

        if args.output:
            if os.path.exists(args.output) and not args.overwrite:
                print(f"Output file {args.output} exists. Use --overwrite.", file=sys.stderr)
                sys.exit(ExitCode.REPORT_WRITE_FAILURE)
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(out_str)
            except Exception as e:
                print(f"Failed to write report: {e}", file=sys.stderr)
                sys.exit(ExitCode.REPORT_WRITE_FAILURE)
        else:
            print(out_str)

        # Determine exit code deterministically based on report
        if not report.result.schema_valid and "JSON Extraction Error" in str(report.errors):
             sys.exit(ExitCode.INVALID_SCHEMA_OR_MALFORMED)
        if not report.result.schema_valid:
             sys.exit(ExitCode.INVALID_SCHEMA_OR_MALFORMED)
        if not report.result.graph_valid:
             sys.exit(ExitCode.INVALID_GRAPH)

        # Check specific policy violations / unsupported
        if "policy_violation" in report.errors or any("Unsafe" in e for e in report.errors):
            sys.exit(ExitCode.POLICY_VIOLATION)

        if report.result.execution_eligibility == ExecutionEligibility.UNSUPPORTED:
            sys.exit(ExitCode.UNSUPPORTED_TOOL)

        if report.result.execution_eligibility == ExecutionEligibility.PROHIBITED:
            sys.exit(ExitCode.POLICY_VIOLATION)

        if report.result.execution_eligibility == ExecutionEligibility.BLOCKED:
             sys.exit(ExitCode.VALID_BLOCKED_OR_REVIEW)

        if report.recommended_action == RecommendedAction.REQUEST_APPROVAL:
             sys.exit(ExitCode.VALID_APPROVAL_REQUIRED)

        if report.recommended_action == RecommendedAction.ACCEPT:
             sys.exit(ExitCode.VALID_AUTOMATIC)

        # Fallback
        if not report.result.policy_valid:
            sys.exit(ExitCode.POLICY_VIOLATION)

        sys.exit(ExitCode.UNEXPECTED_ERROR)

    parser.print_help()
    sys.exit(0)

if __name__ == "__main__":
    main()
