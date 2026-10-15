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


def test_source_payload_roundtrip_and_noncanonical_order(isolation_bundle):
    from app.validation_runner.source_transfer import freeze_payload, receive_payload

    _, _, capsule, content = isolation_bundle
    calls = []

    def guard():
        calls.append("guard")

    records = [(capsule.files[0].path, "100644", content)]
    payload = freeze_payload(capsule, records, authority_check=guard)
    assert type(payload) is bytes
    assert receive_payload(capsule, payload, authority_check=guard) == tuple(records)
    assert len(calls) >= 6


@pytest.mark.parametrize("damage", ["truncated", "trailing", "content", "magic", "manifest"])
def test_source_payload_rejects_corrupt_or_incomplete_input(isolation_bundle, damage):
    from app.validation_runner.source_transfer import MAGIC, freeze_payload, receive_payload

    _, _, capsule, content = isolation_bundle

    def guard():
        pass

    payload = freeze_payload(
        capsule, [(capsule.files[0].path, "100644", content)], authority_check=guard
    )
    if damage == "truncated":
        payload = payload[:-1]
    elif damage == "trailing":
        payload += b"forged success"
    elif damage == "content":
        payload = payload[:-1] + b"!"
    elif damage == "magic":
        payload = b"!" + payload[1:]
    else:
        index = len(MAGIC) + 4
        payload = payload[:index] + b"!" + payload[index + 1 :]
    with pytest.raises(ValueError):
        receive_payload(capsule, payload, authority_check=guard)


@pytest.mark.parametrize(
    "binding", ["workspace_id", "attempt_id", "approval_id", "lease_fingerprint"]
)
def test_source_payload_rejects_cross_authority_substitution(isolation_bundle, binding):
    from app.models.validation_isolation import SourceCapsule
    from app.validation_runner.source_transfer import freeze_payload, receive_payload

    _, owner, capsule, content = isolation_bundle
    payload = freeze_payload(
        capsule, [(capsule.files[0].path, "100644", content)], authority_check=lambda: None
    )
    value = "f" * 64 if binding == "lease_fingerprint" else "foreign"
    changed = capsule.model_dump(mode="json", exclude={"capsule_digest"})
    changed["owner"] = owner.model_copy(update={binding: value}).model_dump(mode="json")
    other = SourceCapsule.model_validate({**changed, "capsule_digest": digest(changed)})
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        receive_payload(other, payload, authority_check=lambda: None)


@pytest.mark.parametrize("bad", ["duplicate", "missing", "mutable", "mode", "unexpected"])
def test_source_freeze_never_exposes_partial_invalid_transfer(isolation_bundle, bad):
    from app.validation_runner.source_transfer import freeze_payload

    _, _, capsule, content = isolation_bundle
    record = (capsule.files[0].path, "100644", content)
    records = {
        "duplicate": [record, record],
        "missing": [],
        "mutable": [(record[0], record[1], bytearray(content))],
        "mode": [(record[0], "120000", content)],
        "unexpected": [(".ssh/id_rsa", "100644", content)],
    }[bad]
    with pytest.raises(ValueError):
        freeze_payload(capsule, records, authority_check=lambda: None)


def test_native_source_snapshot_rechecks_authority_and_freezes_bytes(isolation_bundle, tmp_path):
    from app.validation_runner.source_transfer import export_snapshot, receive_payload

    plan, owner, capsule, content = isolation_bundle
    path = tmp_path / capsule.files[0].path
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    calls = []

    def guard(root, checked):
        assert root == tmp_path and checked == capsule
        calls.append("guard")

    payload = export_snapshot(
        tmp_path,
        capsule,
        plan,
        owner=owner,
        journal_digest=capsule.journal_digest,
        native_inventory_digest=capsule.inventory_digest,
        authority_check=guard,
    )
    path.write_bytes(b"!" * len(content))
    assert receive_payload(capsule, payload, authority_check=lambda: None)[0][2] == content
    assert len(calls) >= 5
    with pytest.raises(ValueError, match="SOURCE_CHANGED|TOOL_IO_LIMIT"):
        export_snapshot(
            tmp_path,
            capsule,
            plan,
            owner=owner,
            journal_digest=capsule.journal_digest,
            native_inventory_digest=capsule.inventory_digest,
            authority_check=guard,
        )


def test_source_snapshot_foreign_root_and_revocation_deny_before_export(isolation_bundle, tmp_path):
    from app.validation_runner.source_transfer import export_snapshot

    plan, owner, capsule, _ = isolation_bundle

    def denied(root, checked):
        raise DomainError("AUTHORITY_REVOKED", "Scope revoked", 403)

    with pytest.raises(DomainError, match="Scope revoked"):
        export_snapshot(
            tmp_path,
            capsule,
            plan,
            owner=owner,
            journal_digest=capsule.journal_digest,
            native_inventory_digest=capsule.inventory_digest,
            authority_check=denied,
        )


def test_source_transfer_lease_loss_before_return_denies_payload(isolation_bundle):
    from app.validation_runner.source_transfer import freeze_payload

    _, _, capsule, content = isolation_bundle
    calls = []

    def guard():
        calls.append(1)
        if len(calls) == 3:
            raise DomainError("LEASE_EXPIRED", "Lease expired", 409)

    with pytest.raises(DomainError):
        freeze_payload(capsule, [(capsule.files[0].path, "100644", content)], authority_check=guard)


def test_linux_guest_import_refuses_windows_and_no_host_files(isolation_bundle, tmp_path):
    import sys

    from app.validation_runner.source_transfer import import_linux_guest

    if sys.platform == "linux":
        pytest.skip("Windows-specific unavailable adapter check")
    _, _, capsule, _ = isolation_bundle
    with pytest.raises(ValueError, match="GUEST_IMPORT_UNAVAILABLE"):
        import_linux_guest(tmp_path, capsule, b"invalid", authority_check=lambda: None)
    assert not tuple(tmp_path.iterdir())


def test_native_source_snapshot_refuses_hard_link(isolation_bundle, tmp_path):
    import os

    from app.validation_runner.source_transfer import export_snapshot

    plan, owner, capsule, content = isolation_bundle
    foreign = tmp_path / "foreign"
    foreign.write_bytes(content)
    path = tmp_path / capsule.files[0].path
    path.parent.mkdir(parents=True)
    os.link(foreign, path)
    with pytest.raises(DomainError):
        export_snapshot(
            tmp_path,
            capsule,
            plan,
            owner=owner,
            journal_digest=capsule.journal_digest,
            native_inventory_digest=capsule.inventory_digest,
            authority_check=lambda *_: None,
        )


def test_source_freeze_rejects_duplicate_before_consuming_unbounded_input(isolation_bundle):
    from app.validation_runner.source_transfer import freeze_payload

    _, _, capsule, content = isolation_bundle
    count = []

    def endless():
        while True:
            count.append(1)
            yield capsule.files[0].path, "100644", content

    with pytest.raises(ValueError, match="TRANSFER_PATH"):
        freeze_payload(capsule, endless(), authority_check=lambda: None)
    assert len(count) == 2


@pytest.mark.parametrize(
    "failure",
    [None, "unexpected", "occupied", "unprotected", "revoked", "name_limit", "unicode_name"],
)
def test_guest_import_descriptor_operations_and_failure_quarantine(
    isolation_bundle, tmp_path, monkeypatch, failure
):
    """Deterministic syscall fixture, never real guest acceptance."""
    import os
    import stat
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.validation_runner import source_transfer as transfer

    plan, owner, capsule, content = isolation_bundle
    if failure == "unicode_name":
        from app.validation_runner.isolation import build_capsule

        files = [capsule.files[0], capsule.files[0].model_copy(update={"path": "😀" * 70 + ".py"})]
        capsule = build_capsule(
            plan,
            owner=owner,
            journal_digest=capsule.journal_digest,
            native_inventory_digest=digest([file.model_dump(mode="json") for file in files]),
            files=files,
        )
    payload = transfer.freeze_payload(
        capsule,
        [(file.path, "100644", content) for file in capsule.files],
        authority_check=lambda: None,
    )
    root = tmp_path / "private-guest"
    paths, infos, children, data, operations = {}, {}, {}, {}, []

    def directory(path, *, private=False):
        fd = len(infos) + 1
        paths[path] = fd
        infos[fd] = SimpleNamespace(
            st_uid=0, st_dev=1, st_mode=stat.S_IFDIR | (0o700 if private else 0o755)
        )
        children[fd] = set()
        return fd

    for path in (*reversed(root.parents), root):
        directory(path, private=path == root)
    if failure == "unprotected":
        infos[paths[root]].st_mode |= 0o002

    def open_directory(stack, path, parent=None, leaf=None, **kwargs):
        if parent is not None:
            assert path == parent.path / leaf and "/" not in leaf and "\\" not in leaf
        return SimpleNamespace(path=path, fd=paths[path])

    def mkdir(leaf, *, mode, dir_fd):
        operations.append("mkdir")
        if failure == "occupied":
            raise FileExistsError("Unexpected pre-existing entry")
        parent = next(path for path, fd in paths.items() if fd == dir_fd)
        directory(parent / leaf, private=True)
        children[dir_fd].add(leaf)

    @contextmanager
    def open_file(parent, leaf, flags, **kwargs):
        key = (parent.fd, leaf)
        if flags & os.O_CREAT:
            assert flags & os.O_EXCL
            fd = len(infos) + 1
            infos[fd] = SimpleNamespace(st_uid=0, st_dev=1, st_mode=stat.S_IFREG | 0o600)
            data[key] = [fd, b""]
            children[parent.fd].add(leaf)
        yield data[key][0]

    def write(fd, chunk):
        operations.append("write")
        entry = next(value for value in data.values() if value[0] == fd)
        entry[1] += chunk
        return len(chunk)

    def chmod(fd, mode):
        infos[fd].st_mode = stat.S_IFMT(infos[fd].st_mode) | mode

    def listdir(fd):
        result = set(children[fd])
        if failure == "unexpected" and data and fd == paths[root]:
            result.add("forged-result.json")
        return list(result)

    def guard():
        if failure == "revoked" and "write" in operations:
            raise DomainError("LEASE_EXPIRED", "Lease expired", 409)

    monkeypatch.setattr(transfer.sys, "platform", "linux")
    monkeypatch.setattr(transfer, "open_directory", open_directory)
    monkeypatch.setattr(transfer, "open_file", open_file)
    monkeypatch.setattr(
        transfer, "read_bytes", lambda fd, maximum: next(v[1] for v in data.values() if v[0] == fd)
    )
    monkeypatch.setattr(
        transfer,
        "os",
        SimpleNamespace(
            geteuid=lambda: 0,
            fstat=lambda fd: infos[fd],
            fpathconf=lambda fd, key: 3 if failure == "name_limit" else 255,
            mkdir=mkdir,
            write=write,
            fchmod=chmod,
            fsync=lambda fd: operations.append("fsync"),
            listdir=listdir,
            O_WRONLY=os.O_WRONLY,
            O_CREAT=os.O_CREAT,
            O_EXCL=os.O_EXCL,
            O_RDONLY=os.O_RDONLY,
        ),
    )
    if failure:
        with pytest.raises((ValueError, FileExistsError, DomainError)):
            transfer.import_linux_guest(root, capsule, payload, authority_check=guard)
        if failure in {"occupied", "unprotected", "name_limit", "unicode_name"}:
            assert "write" not in operations
        if failure in {"name_limit", "unicode_name"}:
            assert "mkdir" not in operations
        # No cleanup effects are available: uncertainty cannot erase evidence.
    else:
        assert (
            transfer.import_linux_guest(root, capsule, payload, authority_check=guard)
            == capsule.capsule_digest
        )
        entry = next(iter(data.values()))
        assert entry[1] == content and stat.S_IMODE(infos[entry[0]].st_mode) == 0o444
        assert stat.S_IMODE(infos[paths[root]].st_mode) == 0o555


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


@pytest.mark.parametrize("count", [129, 4096])
def test_directory_pin_budget_refuses_export_and_import_before_filesystem_effects(
    isolation_bundle, tmp_path, monkeypatch, count
):
    from app.validation_runner import source_transfer as transfer
    from app.validation_runner.isolation import build_capsule

    plan, owner, original, content = isolation_bundle
    files = [original.files[0]] + [
        original.files[0].model_copy(update={"path": f"dir-{index:04d}/source.py"})
        for index in range(count - 1)
    ]
    capsule = build_capsule(
        plan,
        owner=owner,
        journal_digest=original.journal_digest,
        native_inventory_digest=digest([file.model_dump(mode="json") for file in files]),
        files=files,
    )
    payload = transfer.freeze_payload(
        capsule,
        [(file.path, file.mode, content) for file in files],
        authority_check=lambda: None,
    )
    monkeypatch.setattr(
        transfer, "open_directory", lambda *a, **kw: pytest.fail("No pins before preflight")
    )
    with pytest.raises(ValueError, match="DIRECTORY_PIN_LIMIT"):
        transfer.export_snapshot(
            tmp_path,
            capsule,
            plan,
            owner=owner,
            journal_digest=capsule.journal_digest,
            native_inventory_digest=capsule.inventory_digest,
            authority_check=lambda *a: None,
        )
    monkeypatch.setattr(transfer.sys, "platform", "linux")
    monkeypatch.setattr(transfer.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(ValueError, match="DIRECTORY_PIN_LIMIT"):
        transfer.import_linux_guest(tmp_path, capsule, payload, authority_check=lambda: None)


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


@pytest.fixture
def hyperv_binding():
    from app.models.hyper_v_control import HyperVBinding

    return HyperVBinding(
        host_identity="test-host",
        host_machine_guid="aaaaaaaa-1234-1234-1234-123456789abc",
        vm_id="12345678-1234-1234-1234-123456789abc",
        ownership_digest="a" * 64,
        provisioning_digest="b" * 64,
        capsule_digest="c" * 64,
    )


def test_native_hyperv_mock_measures_vm_only_and_does_not_invent_guest_success(hyperv_binding):
    from datetime import UTC, datetime

    from app.validation_runner.hyper_v import NativeHyperVAdapter

    calls = []

    class Transport:
        def invoke(self, binding, operation):
            calls.append(operation)
            return dict(
                vmId=str(binding.vm_id),
                ownershipDigest=binding.ownership_digest,
                hostMachineGuid=str(binding.host_machine_guid),
                observedAt=datetime.now(UTC).isoformat(),
                state="off",
                memoryAssignedBytes=0,
                memoryStartupBytes=2147483648,
                cpuCount=1,
                hostVmUptimeSeconds=0,
                hostCpuUsagePercent=0,
                connectedNetworkAdapters=0,
                checkpointCount=0,
                dynamicMemory=False,
                automaticStart="nothing",
            )

    adapter = NativeHyperVAdapter(Transport(), authority_check=lambda binding, operation: None)
    measured = adapter.inspect(hyperv_binding)
    assert measured.command_outcome == "unmeasured" and not measured.execution_authorized
    assert adapter.stop(hyperv_binding).state == "off"
    assert calls == ["inspect", "stop", "inspect"]
    with pytest.raises(DomainError):
        adapter.execute(hyperv_binding)


def test_native_hyperv_refuses_foreign_vm_and_live_authority_loss(hyperv_binding):
    from app.validation_runner.hyper_v import NativeHyperVAdapter

    class Foreign:
        def invoke(self, binding, operation):
            return dict(vmId="foreign", ownershipDigest=binding.ownership_digest)

    adapter = NativeHyperVAdapter(Foreign(), authority_check=lambda binding, operation: None)
    with pytest.raises(DomainError) as caught:
        adapter.inspect(hyperv_binding)
    assert caught.value.code == "HYPERV_OWNER_MISMATCH"

    def revoked(binding, operation):
        raise DomainError("EMERGENCY_STOP_ACTIVE", "Stopped.", 409)

    adapter.guard = revoked
    with pytest.raises(DomainError) as caught:
        adapter.stop(hyperv_binding)
    assert caught.value.code == "EMERGENCY_STOP_ACTIVE"


@pytest.mark.parametrize("payload", [[], "private-native-message"])
def test_native_hyperv_malformed_response_is_sanitized(hyperv_binding, payload):
    from app.validation_runner.hyper_v import NativeHyperVAdapter

    class Transport:
        def invoke(self, binding, operation):
            return payload

    adapter = NativeHyperVAdapter(Transport(), authority_check=lambda binding, operation: None)
    with pytest.raises(DomainError) as caught:
        adapter.inspect(hyperv_binding)
    assert caught.value.code == "HYPERV_OBSERVATION_INVALID"
    assert "private-native-message" not in str(caught.value)


def test_real_hyperv_operator_enrolled_vm_configuration_only():
    """Opt-in read-only real VM check. No creation/start/source execution occurs."""
    import json
    import os
    from pathlib import Path

    from app.models.hyper_v_control import HyperVBinding
    from app.validation_runner.hyper_v import NativeHyperVAdapter, NativeHyperVTransport

    location = os.environ.get("JARVIS_HYPERV_READONLY_ACCEPTANCE")
    if not location:
        pytest.skip("Requires operator-enrolled VM and protected native transport config")
    data = Path(location).read_bytes()
    if len(data) > 16384:
        pytest.fail("Native acceptance configuration exceeds bound")
    config = json.loads(data)
    binding = HyperVBinding.model_validate(config["binding"])

    def read_only(current, operation):
        if current != binding or operation != "inspect":
            raise DomainError("HYPERV_ACCEPTANCE_READ_ONLY", "Read-only inspection required.", 409)

    transport = NativeHyperVTransport(**config["transport"], authority_check=read_only)
    result = NativeHyperVAdapter(transport, authority_check=read_only).inspect(binding)
    assert result.state == "off"
    assert result.connected_network_adapters == 0 and result.checkpoint_count == 0
    assert not result.dynamic_memory and result.automatic_start == "nothing"
    assert result.command_outcome == "unmeasured" and not result.execution_authorized


@pytest.mark.parametrize(
    "operation",
    [
        "inspect",
        "stop",
        "unregister",
        "foreign-owner",
        "absence",
        "absence-present",
        "absence-error",
    ],
)
def test_native_powershell_control_with_mock_hyperv_cmdlets(operation):
    """Exercise the actual fixed script, with no real Hyper-V invocation."""
    import json
    import os
    import subprocess
    from pathlib import Path

    if os.name != "nt":
        pytest.skip("Native Windows PowerShell mock protocol test")
    executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = Path(__file__).resolve().parents[3] / "scripts/hyper-v-validation-control.ps1"
    quoted = str(script).replace("'", "''")
    code = (
        """
$global:removed = $false
$global:fakeVm = [pscustomobject]@{ Id=[Guid]'12345678-1234-1234-1234-123456789abc';
    Notes='jarvis-validation-v1:"""
        + "a" * 64
        + """'; State='Off'; MemoryAssigned=[long]0;
    AutomaticStartAction='Nothing'; Uptime=[TimeSpan]::Zero; CPUUsage=0 }
function Import-Module { }
function Get-ItemProperty { @{ MachineGuid='aaaaaaaa-1234-1234-1234-123456789abc' } }
function Get-VM { if (-not $global:removed) { $global:fakeVm } }
function Get-VMNetworkAdapter { }
function Get-VMSnapshot { }
function Get-VMProcessor { @{ Count=1 } }
function Get-VMMemory { @{ Startup=[long]2147483648; DynamicMemoryEnabled=$false } }
function Stop-VM { $global:fakeVm.State='Off' }
function Remove-VM { $global:removed=$true }
"""
    )
    if operation == "foreign-owner":
        code += "$global:fakeVm.Notes='foreign'\n"
    elif operation == "absence":
        code += "$global:removed=$true\n"
    elif operation == "absence-error":
        code += "function Get-VM { throw 'native inventory unavailable' }\n"
    action = (
        "inspect"
        if operation == "foreign-owner"
        else "absence"
        if operation.startswith("absence")
        else operation
    )
    code += (
        "& '"
        + quoted
        + "' -Operation "
        + action
        + " -VmId 12345678-1234-1234-1234-123456789abc -OwnershipDigest "
        + "a" * 64
        + " -HostMachineGuid aaaaaaaa-1234-1234-1234-123456789abc"
    )
    completed = subprocess.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if operation in {"foreign-owner", "absence-present", "absence-error"}:
        assert completed.returncode != 0
        assert b"HYPERV_NATIVE_CONTROL_UNAVAILABLE" in completed.stderr
    else:
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        observed = json.loads(completed.stdout)
        if operation == "unregister":
            assert observed["absent"] and not observed["storageCleanupComplete"]
        elif operation == "absence":
            assert observed["absent"]
        else:
            assert observed["state"] == "off" and observed["connectedNetworkAdapters"] == 0


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


@pytest.mark.parametrize("valid", [True, False])
def test_native_hyperv_absence_requires_fresh_successful_binding(hyperv_binding, valid):
    from app.validation_runner.hyper_v import NativeHyperVAdapter

    calls = []

    class Transport:
        def invoke(self, binding, operation):
            assert operation == "absence"
            calls.append(operation)
            return {
                "absent": valid,
                "vmId": str(binding.vm_id),
                "hostMachineGuid": str(binding.host_machine_guid),
                "ownershipDigest": binding.ownership_digest,
            }

    adapter = NativeHyperVAdapter(Transport(), authority_check=lambda *_: None)
    if valid:
        adapter.require_absent(hyperv_binding)
        adapter.require_absent(hyperv_binding)
        assert calls == ["absence", "absence"]
    else:
        with pytest.raises(DomainError):
            adapter.require_absent(hyperv_binding)


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "extra",
        "identity",
        "acl",
        "running",
        "revoked",
        "interrupted",
        "hardlink",
        "ads",
        "nested",
    ],
)
def test_native_registered_storage_cleanup_fixture(
    isolation_bundle, hyperv_binding, tmp_path, monkeypatch, failure
):
    """Real Windows fixture handles/deletion; ACL and VM guards are mocks."""
    import os

    from app.models.validation_cleanup import (
        OwnedStorageEntry,
        StorageIdentity,
        StorageRegistration,
    )
    from app.validation_runner import cleanup
    from app.validation_runner.cleanup import cleanup_registered_storage

    if os.name != "nt":
        pytest.skip("Native Windows cleanup fixture")
    _, owner, capsule, _ = isolation_bundle
    root = tmp_path / "dedicated"
    root.mkdir()
    allocation = root / ("jarvis-validation-" + "a" * 32)
    allocation.mkdir()
    file_parent = allocation
    if failure == "nested":
        file_parent = allocation / "VirtualMachines"
        file_parent.mkdir()
    file = file_parent / "guest.vhdx"
    file.write_bytes(b"owned temporary fixture only")
    service_sid = "S-1-5-21-1-2-3-4567"

    def acl(fd, expected_sid):
        if failure == "acl" or expected_sid != service_sid:
            cleanup.refuse()

    monkeypatch.setattr(cleanup, "private_acl", acl)

    def identity(path):
        info = path.stat()
        return StorageIdentity(volume_id=info.st_dev, file_id=info.st_ino)

    record = StorageRegistration(
        binding=hyperv_binding,
        owner=owner,
        journal_digest=capsule.journal_digest,
        dedicated_root_identity=identity(root),
        allocation_identity=identity(allocation),
        allocation_name=allocation.name,
        service_sid=service_sid,
        entries=(
            (
                OwnedStorageEntry(
                    path="VirtualMachines", kind="directory", identity=identity(file_parent)
                ),
            )
            if failure == "nested"
            else ()
        )
        + (
            OwnedStorageEntry(
                path=file.relative_to(allocation).as_posix(), kind="file", identity=identity(file)
            ),
        ),
    )
    if failure == "extra":
        (allocation / "unexpected.bin").write_bytes(b"never delete unknown content")
    elif failure == "hardlink":
        os.link(file, tmp_path / "external-link")
    elif failure == "ads":
        type(file)(str(file) + ":forged").write_bytes(b"unexpected alternate stream")
    elif failure == "identity":
        record = record.model_copy(
            update={
                "entries": (
                    record.entries[0].model_copy(
                        update={
                            "identity": record.entries[0].identity.model_copy(
                                update={"file_id": 1}
                            ),
                        }
                    ),
                )
            }
        )
    guard_calls = []

    def guard(*_):
        guard_calls.append(1)
        if failure == "revoked" and len(guard_calls) >= 3:
            raise DomainError("CLEANUP_REVOKED", "Cleanup permission revoked", 409)
        if failure == "interrupted" and len(guard_calls) == 4:
            raise DomainError("CLEANUP_CRASH", "Simulated interruption", 409)

    def absent(_):
        if failure == "running":
            raise DomainError("VM_REGISTERED", "VM still registered", 409)

    if failure and failure != "nested":
        with pytest.raises(DomainError):
            cleanup_registered_storage(
                root, record, authority_check=guard, require_vm_absent=absent
            )
        if failure == "interrupted":
            assert not file.exists() and allocation.exists()
            outcome = cleanup_registered_storage(
                root, record, authority_check=guard, require_vm_absent=absent
            )
            assert outcome["registered_storage_absent"] and not allocation.exists()
        else:
            assert file.exists() and allocation.exists()
    else:
        outcome = cleanup_registered_storage(
            root, record, authority_check=guard, require_vm_absent=absent
        )
        assert outcome["registered_storage_absent"] and not outcome["capacity_released"]
        assert root.exists() and not allocation.exists()
        repeated = cleanup_registered_storage(
            root, record, authority_check=guard, require_vm_absent=absent
        )
        assert repeated["registered_storage_absent"] and not repeated["capacity_released"]


def test_native_acl_refuses_unenrolled_owner_without_deletion(tmp_path):
    import os

    from app.validation_runner.cleanup import private_acl

    if os.name != "nt":
        pytest.skip("Native Windows ACL inspection")
    file = tmp_path / "unregistered.bin"
    file.write_bytes(b"preserve")
    fd = os.open(file, os.O_RDONLY | os.O_BINARY)
    try:
        with pytest.raises(DomainError):
            private_acl(fd, "S-1-5-21-1-2-3-4567")
    finally:
        os.close(fd)
    assert file.read_bytes() == b"preserve"


@pytest.mark.parametrize("case", ["valid", "owner", "public", "unprotected", "callback", "error"])
def test_cleanup_acl_policy_deterministically_rejects_unsafe_descriptors(monkeypatch, case):
    import ctypes
    import sys
    from types import SimpleNamespace

    from app.validation_runner import cleanup

    service = "S-1-5-21-1-2-3-4567"
    owner = ctypes.create_string_buffer(16)
    descriptor = ctypes.create_string_buffer(16)
    dacl = ctypes.create_string_buffer(8)
    ctypes.c_ushort.from_buffer(dacl, 4).value = 1
    entry = ctypes.create_string_buffer(32)
    entry[0] = b"\x09" if case == "callback" else b"\x00"
    strings, freed = [], []

    def get(handle, kind, flags, owner_out, group_out, dacl_out, sacl_out, descriptor_out):
        owner_out._obj.value = ctypes.addressof(owner)
        dacl_out._obj.value = ctypes.addressof(dacl)
        descriptor_out._obj.value = ctypes.addressof(descriptor)
        return 5 if case == "error" else 0

    def control(pointer, flags_out, revision_out):
        flags_out._obj.value = 0 if case == "unprotected" else 0x1000
        return 1

    def ace(pointer, index, entry_out):
        entry_out._obj.value = ctypes.addressof(entry)
        return 1

    def convert(pointer, output):
        address = pointer.value if hasattr(pointer, "value") else pointer
        sid = service
        if case == "owner" and address == ctypes.addressof(owner):
            sid = "S-1-5-21-9-9-9-9"
        if case == "public" and address == ctypes.addressof(entry) + 8:
            sid = "S-1-1-0"
        text = ctypes.create_unicode_buffer(sid)
        strings.append(text)
        output._obj.value = ctypes.addressof(text)
        return 1

    def free(pointer):
        freed.append(pointer.value)

    advapi = SimpleNamespace(
        GetSecurityInfo=get,
        GetSecurityDescriptorControl=control,
        GetAce=ace,
        ConvertSidToStringSidW=convert,
    )
    kernel = SimpleNamespace(LocalFree=free)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd))
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **_: advapi if name == "advapi32" else kernel, raising=False
    )
    if case == "valid":
        cleanup.private_acl(123, service)
    else:
        with pytest.raises(DomainError):
            cleanup.private_acl(123, service)
    assert ctypes.addressof(descriptor) in freed
