import argparse
import json
import sys
from pathlib import Path
from jarvis_completion_validator.reporting import generate_report, ReportEncoder
from jarvis_completion_validator.enums import Recommendation
from jarvis_completion_validator.errors import SchemaValidationError

EXIT_CODES = {
    Recommendation.ACCEPT: 0,
    Recommendation.ACCEPT_WITH_WARNINGS: 1,
    Recommendation.REQUEST_REVISION: 2,
    Recommendation.RETRY_STEP: 3,
    Recommendation.REPLAN_TASK: 4,
    Recommendation.REQUEST_APPROVAL: 5,
    Recommendation.BLOCK: 6,
    Recommendation.HUMAN_REVIEW: 7,
    Recommendation.REJECT: 8,
    "INVALID_INPUT": 9,
    "WRITE_FAILURE": 10,
    "INTERNAL_ERROR": 11
}

def load_json_file(filepath: str) -> dict:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}", file=sys.stderr)
        sys.exit(EXIT_CODES["INVALID_INPUT"])

def merge_inputs(args) -> dict:
    # Task is always required in these examples
    data = load_json_file(args.task)

    if args.result:
        result_data = load_json_file(args.result)
        data["worker_result"] = result_data.get("worker_result", result_data) # handle wrapped or unwrapped

    if args.artifacts:
        art_data = load_json_file(args.artifacts)
        if isinstance(art_data, list):
             data["artifacts"] = art_data
        elif "artifacts" in art_data:
             data["artifacts"] = art_data["artifacts"]

    # Default minimums if missing to pass basic parsing
    if "worker_result" not in data:
        data["worker_result"] = {
            "schema_version": "1.0",
            "task_id": data["task"]["task_id"],
            "status_claim": "unknown",
            "summary": "Mock summary",
            "result_type": "unknown",
            "artifact_ids": [],
            "criterion_claims": [],
            "worker_confidence": 0.0
        }

    return data

def handle_validate(args):
    data = merge_inputs(args)
    try:
        report = generate_report(data, args.profile)
    except SchemaValidationError as e:
        print(f"Schema Validation Error: {e}", file=sys.stderr)
        sys.exit(EXIT_CODES["INVALID_INPUT"])
    except Exception as e:
        print(f"Unexpected Error: {e}", file=sys.stderr)
        sys.exit(EXIT_CODES["INTERNAL_ERROR"])

    rec = report.actual_result.recommendation

    if args.format == "json":
        out_str = json.dumps(report, cls=ReportEncoder, indent=2)
    else:
        out_str = f"Recommendation: {rec.value}\nScore: {report.score.total}\n"
        for finding in report.findings:
            out_str += f"- [{finding.severity.value.upper()}] {finding.summary}\n"

    if args.output:
        out_path = Path(args.output)
        if out_path.exists() and not getattr(args, 'overwrite', False):
            print(f"Output file {args.output} already exists. Use --overwrite.", file=sys.stderr)
            sys.exit(EXIT_CODES["WRITE_FAILURE"])
        try:
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(out_str)
        except Exception as e:
            print(f"Failed to write output: {e}", file=sys.stderr)
            sys.exit(EXIT_CODES["WRITE_FAILURE"])
    else:
        print(out_str)

    sys.exit(EXIT_CODES[rec])

def handle_criteria(args):
    data = merge_inputs(args)
    try:
        report = generate_report(data, args.profile)
    except SchemaValidationError as e:
        print(f"Schema Validation Error: {e}", file=sys.stderr)
        sys.exit(EXIT_CODES["INVALID_INPUT"])

    if args.format == "json":
        print(json.dumps([c for c in report.criteria], cls=ReportEncoder, indent=2))
    else:
        for c in report.criteria:
            print(f"Criterion: {c.criterion_id} | Status: {c.status.value}")
    sys.exit(0)

def handle_artifacts(args):
    data = merge_inputs(args)
    try:
        report = generate_report(data, args.profile)
    except SchemaValidationError as e:
        print(f"Schema Validation Error: {e}", file=sys.stderr)
        sys.exit(EXIT_CODES["INVALID_INPUT"])

    art_findings = [f for f in report.findings if f.category in ["missing_artifact", "invalid_artifact", "wrong_task", "placeholder_content"]]
    if args.format == "json":
         print(json.dumps(art_findings, cls=ReportEncoder, indent=2))
    else:
        if not art_findings:
            print("Artifacts valid.")
        else:
            for f in art_findings:
                 print(f"Artifact Finding: {f.summary}")
    # Returning standard validation exit code based on report for artifacts as well
    sys.exit(EXIT_CODES[report.actual_result.recommendation])

def handle_findings(args):
    data = merge_inputs(args)
    try:
        report = generate_report(data, args.profile)
    except SchemaValidationError as e:
        print(f"Schema Validation Error: {e}", file=sys.stderr)
        sys.exit(EXIT_CODES["INVALID_INPUT"])

    if args.format == "json":
         print(json.dumps(report.findings, cls=ReportEncoder, indent=2))
    else:
        for f in report.findings:
            print(f"[{f.severity.value.upper()}] {f.summary}: {f.detailed_reason}")
    sys.exit(EXIT_CODES[report.actual_result.recommendation])

def handle_schema(args):
    import json
    schema_dir = Path(__file__).parent.parent.parent / "schemas"
    schema_file = schema_dir / f"{args.name}.schema.json"
    if not schema_file.exists():
        print(f"Schema {args.name} not found.", file=sys.stderr)
        sys.exit(EXIT_CODES["INVALID_INPUT"])
    print(schema_file.read_text(encoding="utf-8"))
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="Jarvis Completion Validator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common parser options
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--task", required=True, help="Path to task envelope JSON")
    common_parser.add_argument("--result", help="Path to worker result JSON")
    common_parser.add_argument("--artifacts", help="Path to artifact manifest JSON")
    common_parser.add_argument("--evidence", help="Path to evidence JSON")
    common_parser.add_argument("--approvals", help="Path to approvals JSON")
    common_parser.add_argument("--trusted-checks", help="Path to trusted checks JSON")
    common_parser.add_argument("--profile", help="Task profile name")
    common_parser.add_argument("--format", choices=["text", "json"], default="text")
    common_parser.add_argument("--output", help="Output file path")
    common_parser.add_argument("--overwrite", action="store_true", help="Overwrite output file if it exists")
    common_parser.add_argument("--verbose", action="store_true", help="Verbose output")

    # validate
    validate_parser = subparsers.add_parser("validate", parents=[common_parser])

    # criteria
    criteria_parser = subparsers.add_parser("criteria", parents=[common_parser])

    # artifacts
    artifacts_parser = subparsers.add_parser("artifacts", parents=[common_parser])

    # findings
    findings_parser = subparsers.add_parser("findings", parents=[common_parser])

    # schema
    schema_parser = subparsers.add_parser("schema")
    schema_parser.add_argument("--name", required=True, help="Schema name (e.g. completion-report)")

    args = parser.parse_args()

    if args.command == "validate":
        handle_validate(args)
    elif args.command == "criteria":
        handle_criteria(args)
    elif args.command == "artifacts":
        handle_artifacts(args)
    elif args.command == "findings":
        handle_findings(args)
    elif args.command == "schema":
        handle_schema(args)

if __name__ == "__main__":
    main()
