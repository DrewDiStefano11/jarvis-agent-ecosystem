"""Exact bounded journal transitions; no caller strings grant native authority."""

from app.models.git_execution import NativeCommitProgress, NativeCommitRecord
from app.self_build.policy import digest


def advance(record, action, *, device=None, inode=None):
    record = NativeCommitRecord.model_validate(record.model_dump(mode="json"))
    if record.intent_checkpoint_id is None:
        raise ValueError("progress requires the acknowledged native intent checkpoint")
    phase, pending = record.state, record.pending_object_index
    count = record.completed_object_count
    changes = None
    if (
        action == "object_intent"
        and phase == "prepared"
        and pending is None
        and count < record.plan.object_count
    ):
        changes = dict(pending_object_index=count)
    elif (
        action == "object_stage"
        and phase == "prepared"
        and pending == count
        and record.staging_inode is None
    ):
        changes = dict(staging_device=device, staging_inode=inode)
    elif (
        action == "object_directory"
        and phase == "prepared"
        and pending == count
        and record.staging_inode is not None
        and record.object_directory_inode is None
    ):
        changes = dict(object_directory_device=device, object_directory_inode=inode)
    elif (
        action == "object_store_stage"
        and phase == "prepared"
        and pending == count
        and record.object_directory_inode is not None
        and record.object_store_stage_inode is None
    ):
        changes = dict(object_store_stage_device=device, object_store_stage_inode=inode)
    elif (
        action == "object_verified"
        and phase == "prepared"
        and pending == count
        and record.staging_inode is not None
    ):
        changes = dict(
            completed_object_count=count + 1,
            pending_object_index=None,
            staging_device=None,
            staging_inode=None,
            object_directory_device=None,
            object_directory_inode=None,
            object_store_stage_device=None,
            object_store_stage_inode=None,
        )
    elif (
        action == "objects_verified"
        and phase == "prepared"
        and pending is None
        and count == record.plan.object_count
    ):
        changes = dict(state="objects_verified")
    elif action == "index_intent" and phase == "objects_verified":
        changes = dict(state="index_prepared")
    elif action == "index_stage" and phase == "index_prepared" and record.index_stage_inode is None:
        changes = dict(index_stage_device=device, index_stage_inode=inode)
    elif (
        action == "index_verified"
        and phase == "index_prepared"
        and record.index_stage_inode is not None
    ):
        changes = dict(state="indexed")
    elif action == "reference_intent" and phase == "indexed":
        changes = dict(state="reference_prepared")
    elif action == "reference_verified" and phase == "reference_prepared":
        changes = dict(state="committed")
    if changes is None:
        raise ValueError("progress cannot skip intent, adopt an inode or regress effects")
    following = NativeCommitRecord.model_validate(record.model_dump(mode="json") | changes)
    # Validate the action-specific claim shape even when an action carries no inode.
    NativeCommitProgress(
        operation_id=record.operation_id,
        ordinal=1,
        action=action,
        device=device,
        inode=inode,
        previous_digest=digest(record.model_dump(mode="json")),
        next_digest=digest(following.model_dump(mode="json")),
    )
    return following


def apply_progress(record, event):
    event = NativeCommitProgress.model_validate(event.model_dump(mode="json"))
    if event.operation_id != record.operation_id or event.previous_digest != digest(
        record.model_dump(mode="json")
    ):
        raise ValueError("progress predecessor differs from the durable record")
    following = advance(record, event.action, device=event.device, inode=event.inode)
    if digest(following.model_dump(mode="json")) != event.next_digest:
        raise ValueError("progress result differs from its exact journal digest")
    return following
