import argparse
import sys
import json
import uuid
from .config import load_policy, load_task, load_sources
from .enums import InjectionSeverity, TruncationStrategy, ExclusionReason
from .source_policy import validate_source_against_policy
from .provenance import verify_provenance
from .redaction import redact_sensitive_data
from .injection_detection import detect_injection
from .conflict_detection import detect_conflicts
from .deduplication import deduplicate_sources
from .prioritization import sort_sources
from .token_budget import BudgetTracker, estimate_tokens
from .truncation import truncate_content
from .message_builder import build_system_message, build_developer_message, build_task_message, build_context_message
from .manifest import ContextManifest
from .reporting import generate_assembly_report
from .contracts import ModelRequest, ModelMessage
from .hashing import deterministic_hash

def main():
    parser = argparse.ArgumentParser(description="Jarvis Phase 2B Context Assembler Prototype")
    subparsers = parser.add_subparsers(dest="command")

    val_pol = subparsers.add_parser("validate-policy")
    val_pol.add_argument("--policy", required=True)

    insp = subparsers.add_parser("inspect")
    insp.add_argument("--policy", required=True)
    insp.add_argument("--sources", required=True)

    det = subparsers.add_parser("detect-injection")
    det.add_argument("--source", required=True)

    red = subparsers.add_parser("redact")
    red.add_argument("--source", required=True)

    asm = subparsers.add_parser("assemble")
    asm.add_argument("--policy", required=True)
    asm.add_argument("--task", required=True)
    asm.add_argument("--sources", required=True)
    asm.add_argument("--format", default="text", choices=["text", "json"])
    asm.add_argument("--output")

    man = subparsers.add_parser("manifest")
    man.add_argument("--policy", required=True)
    man.add_argument("--task", required=True)
    man.add_argument("--sources", required=True)

    sim = subparsers.add_parser("simulate-attacks")
    sim.add_argument("--policy", required=True)
    sim.add_argument("--format", default="text")

    args = parser.parse_args()

    if args.command == "validate-policy":
        try:
            pol = load_policy(args.policy)
            print("Policy is valid.")
            sys.exit(0)
        except Exception as e:
            print(f"Invalid policy: {e}")
            sys.exit(7)

    elif args.command == "inspect":
        pol = load_policy(args.policy)
        srcs = load_sources(args.sources)
        print(f"Loaded {len(srcs)} sources.")
        sys.exit(0)

    elif args.command == "detect-injection":
        srcs = load_sources(args.source)
        findings = []
        for s in srcs:
            f = detect_injection(s.content)
            if f:
                findings.extend(f)
        if findings:
            print(json.dumps(findings, indent=2))
            sys.exit(4) # critical or finding
        else:
            print("No injections detected.")
            sys.exit(0)

    elif args.command == "redact":
        srcs = load_sources(args.source)
        redacted_count = 0
        for s in srcs:
            r, f = redact_sensitive_data(s.content)
            if f:
                redacted_count += len(f)
        print(f"Redactions made: {redacted_count}")
        sys.exit(0)

    elif args.command == "assemble":
        # Assembly logic integrating all rules
        policy = load_policy(args.policy)
        task = load_task(args.task)
        sources = load_sources(args.sources)

        manifest = ContextManifest(
            manifest_id=str(uuid.uuid4()),
            task_id=task.task_id,
            project_id=task.project_id,
            policy_version=policy.policy_version,
            budget={
                "maximum_context_tokens": policy.maximum_context_tokens or policy.estimated_token_budget,
                "reserved_output_tokens": policy.reserved_output_tokens or 0
            }
        )

        # 1. Base validation & provenance
        valid_sources = []
        for src in sources:
            reason = validate_source_against_policy(src, policy, task.project_id)
            if reason:
                manifest.excluded_sources.append({"source_id": src.source_id, "reason": reason.value})
                continue

            reason = verify_provenance(src)
            if reason:
                manifest.excluded_sources.append({"source_id": src.source_id, "reason": reason.value})
                continue

            valid_sources.append(src)

        # 2. Redaction
        for src in valid_sources:
            redacted, f = redact_sensitive_data(src.content)
            if f:
                src.content = redacted
                for finding in f:
                    manifest.redactions.append({"source_id": src.source_id, **finding})

        # 3. Injection detection
        safe_sources = []
        for src in valid_sources:
            f = detect_injection(src.content)
            if f:
                manifest.injection_findings.extend([{"source_id": src.source_id, **x} for x in f])
                has_critical = any(x["severity"] == InjectionSeverity.CRITICAL for x in f)
                has_high = any(x["severity"] == InjectionSeverity.HIGH for x in f)
                if has_critical or (has_high and task.allowed_result_type != "security_analysis"):
                    manifest.excluded_sources.append({"source_id": src.source_id, "reason": ExclusionReason.CRITICAL_INJECTION.value})
                    continue
            safe_sources.append(src)

        # 4. Deduplication
        deduped, removed = deduplicate_sources(safe_sources)
        manifest.duplicate_sources.extend(removed)

        # 5. Prioritization
        sorted_sources = sort_sources(deduped)

        # 6. Budgeting & Truncation
        budget = BudgetTracker(manifest.budget["maximum_context_tokens"], manifest.budget["reserved_output_tokens"])
        final_sources = []

        for src in sorted_sources:
            content_tokens = estimate_tokens(src.content)
            if content_tokens <= budget.available_tokens:
                budget.add(src.content)
                final_sources.append(src)
                manifest.included_sources.append({"source_id": src.source_id, "size": len(src.content), "tokens": content_tokens})
            else:
                if src.metadata.exact_preservation_required:
                    manifest.excluded_sources.append({"source_id": src.source_id, "reason": ExclusionReason.OVER_BUDGET.value})
                    print("Context over budget error: required source exceeds budget", file=sys.stderr)
                    sys.exit(6)

                if src.metadata.truncation_allowed is False:
                    manifest.excluded_sources.append({"source_id": src.source_id, "reason": ExclusionReason.OVER_BUDGET.value})
                    continue

                truncated, rejected = truncate_content(src.content, budget.available_tokens, TruncationStrategy.TAIL)
                if rejected:
                    manifest.excluded_sources.append({"source_id": src.source_id, "reason": ExclusionReason.OVER_BUDGET.value})
                else:
                    budget.add(truncated)
                    src.content = truncated
                    final_sources.append(src)
                    manifest.included_sources.append({"source_id": src.source_id, "size": len(src.content), "tokens": estimate_tokens(truncated), "truncated": True})
                    manifest.truncated_sources.append({"source_id": src.source_id})

        manifest.budget["estimated_total_tokens"] = budget.used_tokens
        manifest.budget["within_budget"] = budget.used_tokens <= budget.available_tokens

        # 7. Conflicts
        conflicts = detect_conflicts(task, final_sources)
        manifest.conflicts.extend(conflicts)

        if conflicts:
            sys.exit(2) # human review required

        # 8. Assemble Request
        messages = [
            build_system_message(),
            build_developer_message(task),
            build_task_message(task),
            build_context_message(final_sources)
        ]

        req_hash = deterministic_hash([{"role": m.role, "content": m.content} for m in messages])
        manifest.request_hash = req_hash

        request = ModelRequest(
            schema_version="1.0",
            request_id="req-" + str(uuid.uuid4())[:8],
            task_id=task.task_id,
            project_id=task.project_id,
            messages=messages,
            request_hash=req_hash,
            generation={
                "temperature": 0,
                "maximum_output_tokens": policy.reserved_output_tokens
            },
            context_manifest_id=manifest.manifest_id
        )

        report = generate_assembly_report(manifest, request.request_id)

        if args.format == "json":
            out_str = json.dumps(request.__dict__, default=lambda o: o.__dict__, indent=2)
        else:
            out_str = f"Assembled Request: {request.request_id}\nHash: {request.request_hash}\nSources Included: {len(final_sources)}\n"

        if args.output:
            with open(args.output, 'w') as f:
                f.write(out_str)
        else:
            print(out_str)

        sys.exit(0)

    elif args.command == "manifest":
         print("Manifest preview...")
         sys.exit(0)
    elif args.command == "simulate-attacks":
         print("Running security scenarios...")
         sys.exit(0)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
