"""Compose exact native effects from protected intent; no public/model command."""

from inspect import iscoroutine
from time import monotonic

from app.self_build.commit_completion import complete
from app.self_build.commit_effect_fence import metadata_effect
from app.self_build.commit_index_install import install_index
from app.self_build.commit_journal import NativeCommitJournal
from app.self_build.commit_metadata_rules import claims
from app.self_build.commit_object_install import install_object
from app.self_build.commit_process import NativeCommitProcess, refuse
from app.self_build.commit_recovery_read import inspect_recovery
from app.self_build.commit_reference import advance_reference
from app.self_build.commit_source_namespace import source_namespace
from app.self_build.commit_staging import stage_payload


def require_validation(validation_gate, actor, workspace_id, request):
    if validation_gate is None or not callable(getattr(validation_gate, "require_commit", None)):
        refuse("GIT_COMMIT_VALIDATION_UNAVAILABLE")
    result = validation_gate.require_commit(actor, workspace_id, request)
    if result is not None:
        if iscoroutine(result):
            result.close()
        refuse("GIT_COMMIT_VALIDATION_UNAVAILABLE")


def execute(service, actor, workspace_id, request, validation_gate):
    """The trusted validation consumer must independently assess real B receipts.

    There is no default gate or model-selected adapter. New/retried effects require
    current lease, exact persisted approval, source, attempt and emergency-stop
    authority. Final historical reads remain on the journal's read interface.
    """
    if validation_gate is None or not callable(getattr(validation_gate, "require_commit", None)):
        refuse("GIT_COMMIT_VALIDATION_UNAVAILABLE")
    journal = NativeCommitJournal(service)

    # Missing intent is created only through the original approved measurement.
    try:
        history = journal.history(actor, workspace_id, request.command_id)
    except Exception as error:
        from app.core.errors import DomainError

        if not isinstance(error, DomainError) or error.code != "GIT_COMMIT_EXECUTION_NOT_FOUND":
            raise
        require_validation(validation_gate, actor, workspace_id, request)
        journal.begin(actor, workspace_id, request)
    preview, history, program = inspect_recovery(
        service,
        actor,
        workspace_id,
        request.command_id,
        finalize=lambda preview, history, program, guard: (preview, history, program),
        request=request,
    )
    record = history.current
    with service.repository.sessions() as session:
        _, attempt, policy, creation, nonce = service.preparation.live(
            actor, workspace_id, session, request=request
        )
    if attempt != record.plan.preparation.plan.proposal.lineage.attempt_id:
        refuse("GIT_COMMIT_EXECUTION_OWNER_CHANGED")

    def history_reader():
        return journal.history(actor, workspace_id, request.command_id)

    def authority():
        require_validation(validation_gate, actor, workspace_id, request)
        with service.repository.sessions() as session:
            row, current_attempt, current_policy, current_creation, current_nonce = (
                service.preparation.live(actor, workspace_id, session, request=request)
            )
            service.permission(actor, row, session)
            journal.approval(actor, row, record.plan, request, session)
            service.preparation.inspection_approval(
                actor, row, record.plan.preparation.plan.inspection_plan, request, session
            )
            if (current_attempt, current_policy, current_creation, current_nonce) != (
                attempt,
                policy,
                creation,
                nonce,
            ):
                refuse("GIT_COMMIT_EXECUTION_OWNER_CHANGED")
        if history_reader().current != record:
            refuse("GIT_COMMIT_EXECUTION_PROGRESS_CONFLICT")

    authority()
    checked = [float("-inf")]

    def scan_authority():
        if monotonic() - checked[0] >= 0.2:
            authority()
            checked[0] = monotonic()

    with source_namespace(policy, creation, nonce, history.source_snapshot, scan_authority) as (
        _,
        source_guard,
    ):

        def guard(current):
            if current != record:
                refuse("GIT_COMMIT_EXECUTION_PROGRESS_CONFLICT")
            source_guard(check_authority=False)
            authority()
            source_guard(check_authority=False)

        process = NativeCommitProcess(service.preparation.git.tool(), guard)

        def advance(action, *, device=None, inode=None, fence=None):
            nonlocal record

            def checked(*, check_authority=True):
                source_guard(check_authority=check_authority)
                if fence is not None:
                    fence(check_authority=check_authority)

            record = journal.transition(
                actor, request, record, action, checked, device=device, inode=inode
            )
            return record

        directories = {
            node.path: (node.device, node.inode)
            for node in history.metadata_snapshot.nodes
            if node.kind == "directory"
        }
        for index, receipt in claims(history).items():
            if "object_directory" in receipt:
                directories["objects/" + program.objects[index].sha[:2]] = receipt[
                    "object_directory"
                ]
        while record.state == "prepared" and record.completed_object_count < len(program.objects):
            if record.pending_object_index is None:
                advance("object_intent")
            item = program.objects[record.pending_object_index]
            with metadata_effect(policy, program, process, history_reader, authority) as fence:
                record = stage_payload(
                    policy,
                    record,
                    program,
                    lambda current, device, inode: advance(
                        "object_stage", device=device, inode=inode, fence=fence
                    ),
                    guard,
                )
                record = install_object(
                    policy,
                    record,
                    program,
                    process,
                    lambda current, device, inode: advance(
                        "object_directory", device=device, inode=inode, fence=fence
                    ),
                    lambda current, device, inode: advance(
                        "object_store_stage", device=device, inode=inode, fence=fence
                    ),
                    guard,
                    expected_directory=directories.get("objects/" + item.sha[:2]),
                )
                if record.object_directory_inode is not None:
                    directories["objects/" + item.sha[:2]] = (
                        record.object_directory_device,
                        record.object_directory_inode,
                    )
                if not process.object_available(
                    policy, record, program, record.pending_object_index
                ):
                    refuse("GIT_COMMIT_OBJECT_ACKNOWLEDGEMENT_INVALID")
                advance("object_verified", fence=fence)
        if record.state == "prepared":
            advance("objects_verified")
        if record.state == "objects_verified":
            advance("index_intent")
        if record.state == "index_prepared":
            with metadata_effect(policy, program, process, history_reader, authority) as fence:
                if preview.position.index != "exact_tree":
                    record = stage_payload(
                        policy,
                        record,
                        program,
                        lambda current, device, inode: advance(
                            "index_stage", device=device, inode=inode, fence=fence
                        ),
                        guard,
                    )
                    install_index(policy, record, guard)
                advance("index_verified", fence=fence)
        if record.state == "indexed":
            advance("reference_intent")
        if record.state == "reference_prepared":
            advance_reference(policy, history_reader(), program, process)
            advance("reference_verified")
        if record.state not in {"committed", "checkpointed"}:
            refuse("GIT_COMMIT_RECOVERY_POSITION_BLOCKED")
        record = complete(journal, actor, request, workspace_id, program, process, source_guard)
        return record
