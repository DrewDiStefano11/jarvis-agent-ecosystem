"""Bounded transfer verification and lifecycle reduction, with no launch effects.

Native adapters must persist these records in the existing journal/checkpoints.
No VM creation, host extraction, shell command or permissive fallback lives here.
"""

from hashlib import sha256
from itertools import islice

from app.development_validation.evidence import verify_plan
from app.development_validation.planner import digest
from app.models.validation_isolation import CapsuleFile, IsolationLifecycle, SourceCapsule


def build_capsule(plan, *, owner, journal_digest, native_inventory_digest, files):
    plan = verify_plan(plan)
    bounded = tuple(
        CapsuleFile.model_validate(item.model_dump(mode="json")) for item in islice(files, 4097)
    )
    provisional = SourceCapsule.model_construct(
        owner=owner,
        journal_digest=journal_digest,
        execution_plan_hash=plan.execution_plan_hash,
        code_state_hash=plan.validation.code_state_hash,
        files=bounded,
        inventory_digest=native_inventory_digest,
        capsule_digest="0" * 64,
    )
    capsule = SourceCapsule.model_validate(
        {
            **provisional.model_dump(mode="json"),
            "capsule_digest": digest(
                provisional.model_dump(mode="json", exclude={"capsule_digest"})
            ),
        }
    )
    return verify_capsule(
        capsule,
        plan,
        owner=owner,
        journal_digest=journal_digest,
        native_inventory_digest=native_inventory_digest,
    )


def verify_capsule(capsule, plan, *, owner, journal_digest, native_inventory_digest):
    capsule = SourceCapsule.model_validate(capsule.model_dump(mode="json"))
    plan = verify_plan(plan)
    if (
        capsule.owner != owner
        or capsule.journal_digest != journal_digest
        or capsule.inventory_digest != native_inventory_digest
        or capsule.execution_plan_hash != plan.execution_plan_hash
        or capsule.code_state_hash != plan.validation.code_state_hash
        or plan.validation.blocked_paths
    ):
        raise ValueError("CAPSULE_AUTHORITY_MISMATCH")
    files = {item.path: item for item in capsule.files}
    for change in plan.code_state.changes:
        actual = files.get(change.path)
        if change.after_hash is None:
            if actual is not None:
                raise ValueError("CAPSULE_DELETED_FILE_PRESENT")
        elif actual is None or (actual.content_digest, actual.mode) != (
            change.after_hash,
            change.after_mode,
        ):
            raise ValueError("CAPSULE_SOURCE_MISMATCH")
    return capsule


def verify_transfer(capsule, stream):
    """Check complete bytes before a guest importer may materialize them.

    Never extract guest/candidate filenames onto the host. The native receiver
    separately owns no-follow handle creation and checks exact modes in the guest.
    This byte verifier cannot attest that receiver's filesystem isolation.
    """
    capsule = SourceCapsule.model_validate(capsule.model_dump(mode="json"))
    expected = {item.path: item for item in capsule.files}
    seen = set()
    for raw in islice(stream, len(expected) + 1):
        if not isinstance(raw, tuple) or len(raw) != 3:
            raise ValueError("CAPSULE_TRANSFER_INVALID")
        path, mode, content = raw
        if type(path) is not str or path not in expected or path in seen:
            raise ValueError("CAPSULE_TRANSFER_PATH")
        item = expected[path]
        if type(content) is not bytes or len(content) != item.byte_count or mode != item.mode:
            raise ValueError("CAPSULE_TRANSFER_CONTENT")
        if sha256(content).hexdigest() != item.content_digest:
            raise ValueError("CAPSULE_TRANSFER_DIGEST")
        seen.add(path)
    if seen != set(expected):
        raise ValueError("CAPSULE_TRANSFER_INCOMPLETE")
    return capsule.capsule_digest


def advance_lifecycle(previous, phase, *, owner, journal_digest, authority_check):
    """A trusted caller supplies live approval/lease/checkpoint/stop guard.

    This reduces descriptive records only. No phase grants execution authority.
    An unknown external effect must be reconciled/destroyed, never relaunched.
    """
    previous = IsolationLifecycle.model_validate(previous.model_dump(mode="json"))
    transitions = {
        "requested": {"provisioned", "uncertain", "cleanup_requested"},
        "provisioned": {"transferred", "uncertain", "cleanup_requested"},
        "transferred": {"started", "uncertain", "cleanup_requested"},
        "started": {"stopped", "uncertain", "cleanup_requested"},
        "stopped": {"cleanup_requested"},
        "uncertain": {"cleanup_requested"},
        "cleanup_requested": {"destroyed", "uncertain"},
        "destroyed": set(),
    }
    authority_check()
    if previous.owner != owner or previous.journal_digest != journal_digest:
        raise ValueError("ISOLATION_OWNER_CHANGED")
    if phase not in transitions[previous.phase]:
        raise ValueError("ISOLATION_TRANSITION_INVALID")
    result = IsolationLifecycle.model_validate(
        {
            **previous.model_dump(mode="json"),
            "phase": phase,
            "sequence": previous.sequence + 1,
        }
    )
    authority_check()
    return result


def provisioning_digest(provisioning):
    # Revalidate model_copy before accepting any configuration digest.
    from app.models.validation_isolation import IsolationProvisioning

    checked = IsolationProvisioning.model_validate(provisioning.model_dump(mode="json"))
    return digest(checked.model_dump(mode="json"))


def assess_host_capacity(
    provisioning, capacity, *, now, reserve_memory_bytes=536870912, reserve_storage_bytes=1073741824
):
    """Read-only admission assessment, never provisioning or isolation attestation.

    A trusted native observer must measure the intended host and storage volume;
    development-host observations never transfer acceptance to another host.
    Reservation/launch will need a fresh native atomic capacity recheck.
    """
    from app.models.validation_isolation import HostAdmission, HostCapacity, IsolationProvisioning

    provisioning = IsolationProvisioning.model_validate(provisioning.model_dump(mode="json"))
    capacity = HostCapacity.model_validate(capacity.model_dump(mode="json"))
    if now.tzinfo is None or any(
        type(v) is not int or v < 0 for v in (reserve_memory_bytes, reserve_storage_bytes)
    ):
        raise ValueError("HOST_ADMISSION_INPUT_INVALID")
    blockers = []
    age = (now - capacity.observed_at).total_seconds()
    if not 0 <= age <= 60:
        blockers.append("host_capacity_stale")
    feature = (
        capacity.hyper_v_feature
        if provisioning.backend == "hyper_v_vm"
        else capacity.sandbox_feature
    )
    if feature != "enabled":
        blockers.append("required_windows_feature_unverified")
    if not capacity.required_services_available:
        blockers.append("required_services_unavailable")
    if not (
        capacity.hypervisor_present
        or (capacity.firmware_virtualization is True and capacity.slat is True)
    ):
        blockers.append("virtualization_unverified")
    limits = provisioning.limits
    if (
        limits.memory_bytes * limits.concurrency + reserve_memory_bytes
        > capacity.available_memory_bytes
    ):
        blockers.append("insufficient_available_memory")
    if limits.cpu_count * limits.concurrency > capacity.available_cpu_count:
        blockers.append("insufficient_available_cpu")
    if (
        limits.disk_bytes * limits.concurrency + reserve_storage_bytes
        > capacity.available_storage_bytes
    ):
        blockers.append("insufficient_available_storage")
    return HostAdmission(
        host_identity=capacity.host_identity,
        deployment_role=capacity.deployment_role,
        provisioning_digest=provisioning_digest(provisioning),
        blockers=tuple(blockers),
    )
