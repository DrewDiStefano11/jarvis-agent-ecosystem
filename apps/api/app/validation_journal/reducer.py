"""Deterministic reconstruction of bounded append-only validation history."""

from itertools import islice

from app.development_validation.planner import digest
from app.models.validation_journal import JournalEntry, JournalSnapshot, JournalStep

ZERO_DIGEST = "0" * 64


def fold(entries):
    entries = tuple(islice(entries, 129))
    if not entries or len(entries) > 128:
        raise ValueError("validation journal history is missing or exceeds bounds")
    snapshot = None
    for raw in entries:
        entry = JournalEntry.model_validate(raw.model_dump(mode="json"))
        if digest(entry.model_dump(mode="json", exclude={"entry_digest"})) != entry.entry_digest:
            raise ValueError("journal entry integrity mismatch")
        if snapshot is None:
            if (
                entry.sequence != 1
                or entry.previous_digest != ZERO_DIGEST
                or entry.kind != "intent"
            ):
                raise ValueError("journal must start with immutable intent")
            intent = entry.intent
            if intent.journal_id != entry.journal_id:
                raise ValueError("journal intent identity mismatch")
            plan = intent.plan
            # Reading trusted append-only history is not current-policy authority.
            # Keep old plans inspectable after package/policy upgrades; writers and
            # passing assessment still reconstruct the current packaged policy.
            if (
                digest(plan.code_state.model_dump(mode="json")) != plan.validation.code_state_hash
                or digest(plan.validation.model_dump(mode="json", exclude={"plan_hash"}))
                != plan.validation.plan_hash
                or tuple(c.gate for c in plan.commands)
                != tuple(g.gate for g in plan.validation.gates)
                or digest(plan.model_dump(mode="json", exclude={"execution_plan_hash"}))
                != plan.execution_plan_hash
            ):
                raise ValueError("recorded validation plan integrity mismatch")
            snapshot = JournalSnapshot(
                intent=intent,
                sequence=1,
                digest=entry.entry_digest,
                updated_at=entry.timestamp,
                state="prepared",
                steps=(),
            )
            continue
        if (
            entry.sequence != snapshot.sequence + 1
            or entry.previous_digest != snapshot.digest
            or entry.journal_id != snapshot.intent.journal_id
            or entry.timestamp < snapshot.updated_at
            or snapshot.state == "finalized"
        ):
            raise ValueError("journal history is reordered, conflicting or terminal")
        steps = list(snapshot.steps)
        gates = [c.gate for c in snapshot.intent.plan.commands]
        if entry.kind == "step_intent":
            if len(steps) >= len(gates) or entry.gate != gates[len(steps)]:
                raise ValueError("step intent must follow exact ordered plan")
            if steps and steps[-1].state not in {"measured", "uncertain"}:
                raise ValueError("only one validation step may be outstanding")
            if any(s.command_run_id == entry.command_run_id for s in steps):
                raise ValueError("command execution identity cannot be reused")
            steps.append(
                JournalStep(gate=entry.gate, command_run_id=entry.command_run_id, state="intended")
            )
        elif entry.kind in {"step_started", "step_result", "step_uncertain"}:
            if (
                not steps
                or steps[-1].gate != entry.gate
                or steps[-1].command_run_id != entry.command_run_id
            ):
                raise ValueError("transition does not reference current step")
            current = steps[-1]
            if entry.kind == "step_started":
                if current.state != "intended":
                    raise ValueError("only intended steps may start")
                steps[-1] = JournalStep(
                    gate=current.gate, command_run_id=current.command_run_id, state="started"
                )
            elif entry.kind == "step_uncertain":
                if current.state not in {"intended", "started"}:
                    raise ValueError("only unfinished steps may become uncertain")
                steps[-1] = JournalStep(
                    gate=current.gate, command_run_id=current.command_run_id, state="uncertain"
                )
            else:
                if current.state != "started":
                    raise ValueError("measurements require durable started intent")
                m = entry.receipt.measurement
                owner, plan = snapshot.intent.owner, snapshot.intent.plan
                command = plan.commands[len(steps) - 1]
                if (
                    m.command_run_id != current.command_run_id
                    or m.command != command
                    or m.execution_plan_hash != plan.execution_plan_hash
                    or m.validation_plan_hash != plan.validation.plan_hash
                    or m.code_state_hash != plan.validation.code_state_hash
                    or m.repository_identity != plan.code_state.repository_identity
                    or m.task_id != owner.task_id
                    or m.runtime_run_id != owner.runtime_run_id
                    or m.attempt_id != owner.attempt_id
                    or m.started_at < snapshot.updated_at
                    or m.finished_at > entry.timestamp
                ):
                    raise ValueError("measurement lineage differs from durable step intent")
                steps[-1] = JournalStep(
                    gate=current.gate,
                    command_run_id=current.command_run_id,
                    state="measured",
                    receipt=entry.receipt,
                )
        elif entry.kind == "finalized":
            if len(steps) != len(gates) or any(
                s.state not in {"measured", "uncertain"} for s in steps
            ):
                raise ValueError("finalization requires every gate to have a recorded disposition")
        else:
            raise ValueError("immutable intent cannot be rewritten")
        snapshot = JournalSnapshot(
            intent=snapshot.intent,
            sequence=entry.sequence,
            digest=entry.entry_digest,
            updated_at=entry.timestamp,
            state="finalized" if entry.kind == "finalized" else "active",
            steps=tuple(steps),
        )
    return snapshot
