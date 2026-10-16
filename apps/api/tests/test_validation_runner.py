"""Fixed family descriptions and non-bypassable execution denial."""

import pytest

from app.core.errors import DomainError
from app.development_validation.evidence import build_execution_plan
from app.development_validation.planner import digest
from app.models.development_validation import CodeState, FileChange
from app.models.validation_runner import RunnerAvailability
from app.validation_runner.catalog import (
    DisabledValidationRunner,
    describe_invocations,
    verify_invocations,
)


@pytest.fixture
def isolation_bundle():
    from hashlib import sha256

    from app.models.validation_isolation import CapsuleFile
    from app.models.validation_journal import JournalOwner
    from app.validation_runner.isolation import build_capsule

    content = b"value = 1\n"
    file = CapsuleFile(
        path="apps/api/app/example.py",
        mode="100644",
        content_digest=sha256(content).hexdigest(),
        byte_count=len(content),
    )
    state = CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha="a" * 40,
        base_sha="b" * 40,
        changes=(
            FileChange(
                path=file.path,
                before_hash=None,
                before_mode=None,
                after_hash=file.content_digest,
                after_mode=file.mode,
            ),
        ),
    )
    expected = build_execution_plan(state)
    owner = JournalOwner(
        workspace_id="workspace",
        task_id="task",
        runtime_run_id="run",
        attempt_id="attempt",
        worker_id="worker",
        approval_id="approval",
        lease_fingerprint="d" * 64,
    )
    inventory = digest([file.model_dump(mode="json")])
    capsule = build_capsule(
        expected,
        owner=owner,
        journal_digest="e" * 64,
        native_inventory_digest=inventory,
        files=[file],
    )
    return expected, owner, capsule, content


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "C:/private",
        ".env",
        ".ssh/key",
        ".aws/credentials",
        ".codex/token",
        "a\\b",
        "NUL",
        "alias~1/file",
        "file:stream",
        ".jarvis-owner/x",
    ],
)
def test_isolation_transfer_contract_refuses_escape_and_credential_paths(path):
    from app.models.validation_isolation import CapsuleFile

    with pytest.raises(ValueError):
        CapsuleFile(path=path, mode="100644", content_digest="a" * 64, byte_count=1)


def test_isolation_capsule_exact_transfer_and_authority(isolation_bundle):
    from app.validation_runner.isolation import verify_capsule, verify_transfer

    expected, owner, capsule, content = isolation_bundle
    assert (
        verify_transfer(capsule, [(capsule.files[0].path, "100644", content)])
        == capsule.capsule_digest
    )
    assert (
        verify_capsule(
            capsule,
            expected,
            owner=owner,
            journal_digest="e" * 64,
            native_inventory_digest=capsule.inventory_digest,
        )
        == capsule
    )
    for foreign in [
        owner.model_copy(update={"attempt_id": "foreign"}),
        owner.model_copy(update={"approval_id": "foreign"}),
        owner.model_copy(update={"lease_fingerprint": "f" * 64}),
    ]:
        with pytest.raises(ValueError):
            verify_capsule(
                capsule,
                expected,
                owner=foreign,
                journal_digest="e" * 64,
                native_inventory_digest=capsule.inventory_digest,
            )


@pytest.mark.parametrize("attack", ["missing", "extra", "duplicate", "content", "mode", "link"])
def test_isolation_transfer_adversarial_inputs(isolation_bundle, attack):
    from app.validation_runner.isolation import verify_transfer

    _, _, capsule, content = isolation_bundle
    good = (capsule.files[0].path, "100644", content)
    stream = {
        "missing": [],
        "extra": [good, ("../host", "100644", b"x")],
        "duplicate": [good, good],
        "content": [(good[0], good[1], b"tampered")],
        "mode": [(good[0], "100755", content)],
        "link": [(good[0], "120000", b"../host")],
    }[attack]
    with pytest.raises(ValueError):
        verify_transfer(capsule, stream)


@pytest.mark.parametrize(
    "field",
    [
        "external_network",
        "host_network",
        "host_credentials",
        "clipboard",
        "devices",
        "writable_host_mounts",
        "enhanced_session",
    ],
)
def test_isolation_provisioning_refuses_escape_configuration(field):
    from app.models.validation_isolation import IsolationProvisioning

    with pytest.raises(ValueError):
        IsolationProvisioning(
            backend="hyper_v_vm",
            image_digest="a" * 64,
            dependency_digest="b" * 64,
            collector_identity="collector",
            existing_image_license_confirmed=True,
            limits=dict(
                memory_bytes=2147483648,
                cpu_count=1,
                disk_bytes=1073741824,
                process_count=16,
                wall_seconds=60,
                output_bytes=1024,
            ),
            **{field: True},
        )


def test_isolation_uncertainty_never_relaunches_and_cleanup_is_owned(isolation_bundle):
    from app.models.validation_isolation import IsolationLifecycle
    from app.validation_runner.isolation import advance_lifecycle

    _, owner, capsule, _ = isolation_bundle
    previous = IsolationLifecycle(
        isolation_id="vm",
        owner=owner,
        journal_digest="e" * 64,
        capsule_digest=capsule.capsule_digest,
        provisioning_digest="a" * 64,
        sequence=1,
        phase="started",
    )
    seen = []

    def guard():
        seen.append(True)

    unknown = advance_lifecycle(
        previous, "uncertain", owner=owner, journal_digest="e" * 64, authority_check=guard
    )
    with pytest.raises(ValueError):
        advance_lifecycle(
            unknown, "started", owner=owner, journal_digest="e" * 64, authority_check=guard
        )
    cleanup = advance_lifecycle(
        unknown, "cleanup_requested", owner=owner, journal_digest="e" * 64, authority_check=guard
    )
    final = advance_lifecycle(
        cleanup, "destroyed", owner=owner, journal_digest="e" * 64, authority_check=guard
    )
    assert not final.execution_authorized and len(seen) == 7

    def stopped():
        raise RuntimeError("emergency stop")

    with pytest.raises(RuntimeError):
        advance_lifecycle(
            previous, "stopped", owner=owner, journal_digest="e" * 64, authority_check=stopped
        )


def test_quarantined_collector_cannot_promote_guest_json_or_forged_pass(isolation_bundle):
    from datetime import UTC, datetime, timedelta

    from app.development_validation.evidence import ReceiptVerifier, assess_receipts
    from app.validation_runner.collector import QuarantinedEvidenceCollector

    expected, owner, capsule, _ = isolation_bundle
    key = b"fixture-only-key-outside-candidate"
    collector = QuarantinedEvidenceCollector(
        collector_id="fixture", key_id="key", key_provider=lambda: key, authority_check=lambda: None
    )
    now = datetime.now(UTC)
    signed = collector.capture(
        plan=expected,
        owner=owner,
        command=expected.commands[0],
        command_run_id="command",
        started_at=now - timedelta(seconds=1),
        finished_at=now,
        stdout=[b'{"outcome":"passed","exit_code":0,"secret":"candidate"}'],
        stderr=[],
        capsule=capsule,
        journal_digest="e" * 64,
        native_inventory_digest=capsule.inventory_digest,
    )
    verifier = ReceiptVerifier({("fixture", "key"): key})
    assert verifier.verify(signed).outcome == "unmeasured"
    assert "candidate" not in signed.model_dump_json()
    assert not assess_receipts(
        expected.code_state,
        expected,
        [signed],
        verifier,
        now=now,
        expected_execution_plan_hash=expected.execution_plan_hash,
    ).ready
    forged = signed.model_copy(
        update={
            "measurement": signed.measurement.model_copy(
                update={"outcome": "passed", "exit_code": 0}
            )
        }
    )
    with pytest.raises(ValueError):
        verifier.verify(forged)


def test_quarantined_collector_bounds_output_and_rechecks_stop(isolation_bundle):
    from datetime import UTC, datetime, timedelta

    from app.validation_runner.collector import QuarantinedEvidenceCollector

    expected, owner, capsule, _ = isolation_bundle
    now = datetime.now(UTC)
    calls = []

    def guard():
        calls.append(True)
        if len(calls) == 3:
            raise RuntimeError("lease lost")

    collector = QuarantinedEvidenceCollector(
        collector_id="fixture", key_id="key", key_provider=lambda: b"x" * 32, authority_check=guard
    )
    with pytest.raises(RuntimeError):
        collector.capture(
            plan=expected,
            owner=owner,
            command=expected.commands[0],
            command_run_id="command",
            started_at=now - timedelta(seconds=1),
            finished_at=now,
            stdout=[b"a", b"b"],
            stderr=[],
            capsule=capsule,
            journal_digest="e" * 64,
            native_inventory_digest=capsule.inventory_digest,
        )


@pytest.mark.parametrize("chunks", [[b"x" * 65537], [b""] * 4097])
def test_collector_output_flood_is_bounded_before_signing(isolation_bundle, chunks):
    from datetime import UTC, datetime

    from app.validation_runner.collector import QuarantinedEvidenceCollector

    expected, owner, capsule, _ = isolation_bundle

    def no_key_read():
        pytest.fail("invalid capture must not reach key provider")

    collector = QuarantinedEvidenceCollector(
        collector_id="fixture", key_id="key", key_provider=no_key_read, authority_check=lambda: None
    )
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        collector.capture(
            plan=expected,
            owner=owner,
            command=expected.commands[0],
            command_run_id="command",
            started_at=now,
            finished_at=now,
            stdout=chunks,
            stderr=[],
            capsule=capsule,
            journal_digest="e" * 64,
            native_inventory_digest=capsule.inventory_digest,
        )


@pytest.mark.parametrize("blob", [b"", b"not base64!", b"eA==", b"x" * 129])
def test_collector_key_invalid_bytes_never_leak(blob):
    from app.validation_runner.credentials import decode_key

    with pytest.raises(ValueError, match="COLLECTOR_KEY_UNAVAILABLE"):
        decode_key(blob)


def test_collector_windows_key_fixed_target_and_native_buffer_release(monkeypatch):
    import base64
    import ctypes
    from types import SimpleNamespace

    from app.validation_runner import credentials

    held, freed, targets = [], [], []

    class Function:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def read(target, kind, flags, destination):
        targets.append((target, kind, flags))
        record_type = destination._obj._type_
        blob = base64.b64encode(b"x" * 32)
        data = (ctypes.c_ubyte * len(blob))(*blob)
        record = record_type(
            Type=1,
            TargetName=target,
            CredentialBlobSize=len(blob),
            CredentialBlob=ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte)),
        )
        held.extend((data, record))
        ctypes.cast(destination, ctypes.POINTER(ctypes.POINTER(record_type)))[0] = ctypes.pointer(
            record
        )
        return True

    api = SimpleNamespace(
        CredReadW=Function(read), CredFree=Function(lambda pointer: freed.append(bool(pointer)))
    )

    def load(name, *, winmode):
        assert name == "advapi32.dll" and winmode == 0x00000800
        return api

    monkeypatch.setattr(credentials, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(credentials.ctypes, "WinDLL", load, raising=False)
    assert credentials.WindowsCollectorKey()() == b"x" * 32
    assert targets == [(credentials.TARGET, 1, 0)] and freed == [True]


@pytest.mark.parametrize(
    "field,value",
    [
        ("cpu_count", 0),
        ("cpu_count", 100),
        ("process_count", 0),
        ("process_count", 10000),
        ("memory_bytes", 2**40),
        ("disk_bytes", 2**60),
        ("wall_seconds", 86400),
        ("output_bytes", 2**30),
    ],
)
def test_provisioning_process_and_resource_escape_is_rejected(field, value):
    from app.models.validation_isolation import IsolationLimits

    values = dict(
        memory_bytes=2147483648,
        cpu_count=1,
        disk_bytes=1073741824,
        process_count=16,
        wall_seconds=60,
        output_bytes=1024,
    )
    with pytest.raises(ValueError):
        IsolationLimits(**{**values, field: value})


def test_portable_host_admission_supports_one_lightweight_vm_without_laptop_assumptions():
    from datetime import UTC, datetime, timedelta

    from app.models.validation_isolation import HostCapacity, IsolationProvisioning
    from app.validation_runner.isolation import assess_host_capacity

    now = datetime.now(UTC)
    provision = IsolationProvisioning(
        backend="hyper_v_vm",
        image_digest="a" * 64,
        dependency_digest="b" * 64,
        collector_identity="collector",
        existing_image_license_confirmed=True,
        limits=dict(
            memory_bytes=2147483648,
            cpu_count=1,
            disk_bytes=1073741824,
            process_count=16,
            wall_seconds=60,
            output_bytes=1024,
        ),
    )
    host = HostCapacity(
        host_identity="small-production-host",
        deployment_role="production",
        observed_at=now,
        physical_memory_bytes=4 * 1024**3,
        available_memory_bytes=3 * 1024**3,
        logical_cpu_count=2,
        available_cpu_count=1,
        available_storage_bytes=3 * 1024**3,
        hypervisor_present=True,
        hyper_v_feature="enabled",
        sandbox_feature="disabled",
        required_services_available=True,
    )
    result = assess_host_capacity(provision, host, now=now)
    assert not result.blockers and not result.execution_authorized
    assert (
        result.deployment_role == "production" and result.host_identity == "small-production-host"
    )
    parallel = provision.model_copy(
        update={"limits": provision.limits.model_copy(update={"concurrency": 2})}
    )
    denied = assess_host_capacity(parallel, host, now=now)
    assert "insufficient_available_memory" in denied.blockers
    assert "insufficient_available_cpu" in denied.blockers
    assert (
        "host_capacity_stale"
        in assess_host_capacity(provision, host, now=now + timedelta(seconds=61)).blockers
    )
    unknown = host.model_copy(
        update={
            "hyper_v_feature": "unknown",
            "required_services_available": False,
            "hypervisor_present": False,
        }
    )
    denied = assess_host_capacity(provision, unknown, now=now)
    assert {
        "required_windows_feature_unverified",
        "required_services_unavailable",
        "virtualization_unverified",
    } <= set(denied.blockers)
    changed = host.model_copy(
        update={"host_identity": "development-laptop", "deployment_role": "development"}
    )
    assert assess_host_capacity(provision, changed, now=now).deployment_role == "development"


def test_collector_key_provider_error_is_sanitized(isolation_bundle):
    from datetime import UTC, datetime

    from app.validation_runner.collector import QuarantinedEvidenceCollector

    expected, owner, capsule, _ = isolation_bundle

    def unavailable():
        raise RuntimeError("private-provider-key-material")

    collector = QuarantinedEvidenceCollector(
        collector_id="fixture", key_id="key", key_provider=unavailable, authority_check=lambda: None
    )
    now = datetime.now(UTC)
    with pytest.raises(DomainError) as caught:
        collector.capture(
            plan=expected,
            owner=owner,
            command=expected.commands[0],
            command_run_id="command",
            started_at=now,
            finished_at=now,
            stdout=[],
            stderr=[],
            capsule=capsule,
            journal_digest="e" * 64,
            native_inventory_digest=capsule.inventory_digest,
        )
    assert caught.value.code == "COLLECTOR_KEY_UNAVAILABLE"
    assert "private-provider-key-material" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_capsule_subset_cannot_replace_trusted_complete_inventory(isolation_bundle):
    from app.validation_runner.isolation import build_capsule, verify_capsule

    expected, owner, capsule, _ = isolation_bundle
    with pytest.raises(ValueError):
        verify_capsule(
            capsule,
            expected,
            owner=owner,
            journal_digest="e" * 64,
            native_inventory_digest="f" * 64,
        )
    with pytest.raises(ValueError):
        build_capsule(
            expected,
            owner=owner,
            journal_digest="e" * 64,
            native_inventory_digest=capsule.inventory_digest,
            files=[],
        )


def plan(**options):
    state = CodeState(
        repository_identity="github.com/example/jarvis",
        head_sha="a" * 40,
        base_sha="b" * 40,
        changes=(
            FileChange(
                path="apps/api/app/example.py",
                before_hash=None,
                before_mode=None,
                after_hash="c" * 64,
                after_mode="100644",
            ),
        ),
    )
    return build_execution_plan(state, **options)


def test_fixed_families_cover_every_publication_gate_and_browser():
    expected = plan(boundary="publication", browser_required=True)
    invocations = describe_invocations(expected)
    assert len(invocations) == 9
    assert verify_invocations(expected, invocations) == invocations
    assert {g.gate for g in invocations} == set(DisabledValidationRunner.availability().gates)
    ruff = next(g for g in invocations if g.gate == "backend_ruff")
    assert ruff.invocations[0].arguments[-1] == "--check"
    assert ruff.invocations[1].arguments[1:3] == ("ruff", "check")
    assert next(g for g in invocations if g.gate == "frontend_build").invocations[0].arguments == (
        "build",
    )


def test_focused_targets_come_only_from_deterministic_plan():
    expected = plan(affected_backend_tests=("apps/api/tests/test_api.py",))
    backend = next(g for g in describe_invocations(expected) if g.gate == "backend_tests")
    assert backend.invocations[0].arguments == (
        "-m",
        "pytest",
        "tests/test_api.py",
        "--timeout=300",
    )
    full = next(
        g
        for g in describe_invocations(
            plan(boundary="publication", affected_backend_tests=("apps/api/tests/test_api.py",))
        )
        if g.gate == "backend_tests"
    )
    assert full.invocations[0].arguments == ("-m", "pytest", "tests", "--timeout=300")


@pytest.mark.parametrize("packaged_file", ["catalog.py", "validation_runner.py"])
def test_approved_execution_plan_rejects_packaged_invocation_policy_drift(
    monkeypatch, packaged_file
):
    from pathlib import Path

    from app.development_validation.evidence import verify_plan

    approved = plan(boundary="publication")
    read_text = Path.read_text

    def updated(path, *args, **kwargs):
        text = read_text(path, *args, **kwargs)
        return text + "\n# updated invocation policy\n" if path.name == packaged_file else text

    monkeypatch.setattr(Path, "read_text", updated)
    assert plan(boundary="publication").execution_plan_hash != approved.execution_plan_hash
    with pytest.raises(ValueError):
        verify_plan(approved)
    with pytest.raises(ValueError):
        describe_invocations(approved)


def test_browser_acceptance_requires_browser_and_office_invocations():
    expected = plan(boundary="publication", browser_required=True)
    browser = next(g for g in describe_invocations(expected) if g.gate == "browser_acceptance")
    assert len(browser.invocations) == 2
    normal, office = browser.invocations
    assert normal.arguments == office.arguments == ("scripts/smoke-local-planning.py",)
    assert normal.environment == (("JARVIS_SMOKE_BROWSER", "true"),)
    assert office.environment == (
        ("JARVIS_SMOKE_BROWSER", "true"),
        ("JARVIS_SMOKE_OFFICE", "true"),
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("arguments", ("-c", "arbitrary python")),
        ("working_directory", "."),
        ("program", "python"),
    ],
)
def test_rehashed_forged_invocations_rejected(field, value):
    expected = plan(boundary="publication")
    groups = list(describe_invocations(expected))
    index = next(i for i, g in enumerate(groups) if g.gate == "frontend_build")
    group = groups[index]
    altered = group.invocations[0].model_copy(update={field: value})
    forged = group.model_copy(update={"invocations": (altered,)})
    forged = forged.model_copy(
        update={
            "invocation_digest": digest(
                forged.model_dump(mode="json", exclude={"invocation_digest"})
            )
        }
    )
    groups[index] = forged
    with pytest.raises(ValueError):
        verify_invocations(expected, groups)


def test_caller_claims_never_enable_execution_or_invoke_callbacks(monkeypatch):
    calls = []
    runner = DisabledValidationRunner()
    monkeypatch.setenv("JARVIS_VALIDATION_EXECUTION_ENABLED", "true")
    for invocation_parameters in [
        dict(),
        dict(approved=True, sandbox_verified=True),
        dict(launch=lambda *args: calls.append(args)),
        dict(plan=plan(), invocations=describe_invocations(plan())),
    ]:
        with pytest.raises(DomainError) as failure:
            runner.execute(**invocation_parameters)
        assert failure.value.code == "VALIDATION_EXECUTION_ISOLATION_UNAVAILABLE"
    assert not calls
    assert not runner.availability().enabled and not runner.availability().isolation_implemented
    with pytest.raises(ValueError):
        RunnerAvailability(gates=runner.availability().gates, enabled=True)


def test_invocation_iterator_is_bounded_before_candidate_materialization():
    expected = plan(boundary="publication")
    item = describe_invocations(expected)[0]
    consumed = []

    def unbounded_candidates():
        while True:
            consumed.append(1)
            yield item

    with pytest.raises(ValueError):
        verify_invocations(expected, unbounded_candidates())
    assert len(consumed) <= 10
