from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.core.errors import DomainError
from app.models.tool_execution import ToolScope, ToolStep
from app.tool_execution import filesystem
from app.tool_execution.filesystem import MARKER, TOOLS, WorkspaceToolRegistry


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "bounded-workspace"
    root.mkdir()
    (root / MARKER).write_text(json.dumps({"schemaVersion": "1.0", "workspaceId": "demo"}))
    (root / "inputs").mkdir()
    (root / "inputs" / "brief.txt").write_text("Useful input café", encoding="utf-8")
    registry = WorkspaceToolRegistry(json.dumps({"demo": str(root)}))
    scope = ToolScope(
        workspaceId="demo",
        allowedTools=list(TOOLS),
        readPrefixes=["inputs"],
        writePrefixes=["reports"],
    )
    return root, registry, scope


def assert_error(code, action):
    with pytest.raises(DomainError) as raised:
        action()
    assert raised.value.code == code
    return raised.value


def read(registry, scope, path="inputs/brief.txt"):
    return registry.execute(ToolStep(tool="workspace.read", path=path), scope)


def write(
    registry,
    scope,
    path="reports/result.txt",
    content="result",
    expected=None,
    tool="workspace.write",
):
    return registry.execute(
        ToolStep(tool=tool, path=path, content=content, expectedContentHash=expected), scope
    )


def change_marker(root, **fields):
    marker = json.loads((root / MARKER).read_text())
    marker.update(fields)
    (root / MARKER).write_text(json.dumps(marker))


def test_workspace_discovery_exposes_alias_and_scope_without_root_or_side_effect(workspace):
    root, registry, _ = workspace
    result = registry.workspaces()
    assert len(result) == 1 and result[0].ready
    assert result[0].readPrefixes == ["inputs"]
    assert result[0].writePrefixes == ["reports"]
    assert result[0].allowedTools == list(TOOLS)
    assert str(root) not in result[0].model_dump_json()
    assert not (root / filesystem.LOCK).exists()


def test_real_utf8_read_and_filtered_listing(workspace):
    root, registry, scope = workspace
    for name in [".env", "credentials.json", "client.key"]:
        (root / "inputs" / name).write_text("private")
    (root / "inputs" / "folder").mkdir()
    result = read(registry, scope)
    assert result.content == "Useful input café"
    assert result.contentHash == hashlib.sha256(result.content.encode()).hexdigest()
    assert result.byteCount == len(result.content.encode())
    listed = registry.execute(ToolStep(tool="workspace.list", path="inputs"), scope)
    assert listed.entries == ["brief.txt", "folder/"]


def test_write_report_hash_replay_and_reviewed_replacement(workspace):
    root, registry, scope = workspace
    first = write(
        registry, scope, path="reports/week/summary.md", content="# Output", tool="workspace.report"
    )
    target = root / "reports/week/summary.md"
    before = target.stat().st_mtime_ns
    replay = write(
        registry, scope, path="reports/week/summary.md", content="# Output", tool="workspace.report"
    )
    assert first.written and not replay.written
    assert replay.contentHash == first.contentHash
    assert target.stat().st_mtime_ns == before
    assert_error(
        "TOOL_WRITE_CONFLICT",
        lambda: write(registry, scope, path="reports/week/summary.md", content="replacement"),
    )
    assert target.read_text() == "# Output"
    replaced = write(
        registry,
        scope,
        path="reports/week/summary.md",
        content="replacement",
        expected=first.contentHash,
    )
    assert replaced.written and target.read_text() == "replacement"
    assert_error(
        "TOOL_WRITE_CONFLICT",
        lambda: write(registry, scope, path="reports/missing.txt", expected=first.contentHash),
    )
    assert not (root / "reports/missing.txt").exists()


def test_interrupted_atomic_replace_keeps_old_file_and_cleans_temporary(workspace, monkeypatch):
    root, registry, scope = workspace
    original = write(registry, scope)

    def interrupted(*args, **kwargs):
        raise OSError("interrupted before commit")

    monkeypatch.setattr(filesystem.os, "replace", interrupted)
    error = assert_error(
        "TOOL_PATH_UNSAFE",
        lambda: write(registry, scope, content="new", expected=original.contentHash),
    )
    assert str(root) not in error.message
    assert (root / "reports/result.txt").read_text() == "result"
    assert sorted(p.name for p in (root / "reports").iterdir()) == ["result.txt"]


@pytest.mark.parametrize(
    "path",
    [
        "../private",
        "/etc/passwd",
        "C:/private.txt",
        "C:private.txt",
        "//server/share",
        "inputs\\brief.txt",
        "inputs/../brief.txt",
        "inputs//brief.txt",
        "inputs/./brief.txt",
        "inputs/file:stream",
        "inputs/file.",
        "inputs/file ",
        "inputs/CON.txt",
        "inputs/lpt1",
        "inputs/COM¹.txt",
        "inputs/hello\0.txt",
    ],
)
def test_paths_reject_traversal_devices_and_windows_aliases(workspace, path):
    _, registry, scope = workspace
    assert_error("TOOL_PATH_INVALID", lambda: read(registry, scope, path))


@pytest.mark.parametrize(
    "path",
    [
        "inputs/.env",
        "inputs/.git/config",
        "inputs/credentials.json",
        "inputs/credentials.toml",
        "inputs/secrets.yaml",
        "inputs/SECRETS",
        "inputs/client.pem",
        "inputs/id_ed25519",
    ],
)
def test_hidden_and_credential_paths_are_denied(workspace, path):
    _, registry, scope = workspace
    assert_error("TOOL_PATH_DENIED", lambda: read(registry, scope, path))


def test_explicit_invocation_grant_cannot_expand_outer_marker(workspace):
    root, registry, scope = workspace
    for field, prefixes in [
        ("readPrefixes", ["."]),
        ("writePrefixes", ["inputs"]),
        ("readPrefixes", ["inputs-other"]),
    ]:
        widened = scope.model_copy(update={field: prefixes})
        assert_error("TOOL_SCOPE_DENIED", lambda widened=widened: registry.validate_scope(widened))
    assert_error("TOOL_PATH_DENIED", lambda: read(registry, scope, "inputs-other/brief.txt"))
    read_only = scope.model_copy(update={"allowedTools": ["workspace.read"]})
    assert_error("TOOL_NOT_AUTHORIZED", lambda: write(registry, read_only))
    assert_error("TOOL_PATH_DENIED", lambda: read(registry, scope, "reports/result.txt"))
    change_marker(root, allowedTools=["workspace.read"])
    assert_error("TOOL_SCOPE_DENIED", lambda: registry.validate_scope(scope))
    assert_error("TOOL_SCOPE_DENIED", lambda: write(registry, scope))
    assert not (root / "reports").exists()


def test_narrowed_custom_prefixes_and_marker_revocation_are_rechecked(workspace):
    root, registry, scope = workspace
    change_marker(root, writePrefixes=["reports/week"], readPrefixes=["inputs"])
    narrow = scope.model_copy(update={"writePrefixes": ["reports/week/summary"]})
    registry.validate_step(
        ToolStep(tool="workspace.write", path="reports/week/summary/a.txt", content="a"), narrow
    )
    assert write(registry, narrow, path="reports/week/summary/a.txt").written
    change_marker(root, writePrefixes=[])
    assert_error(
        "TOOL_SCOPE_DENIED", lambda: write(registry, narrow, path="reports/week/summary/b.txt")
    )
    assert not (root / "reports/week/summary/b.txt").exists()


def test_marked_root_and_alias_are_required(workspace):
    root, registry, scope = workspace
    (root / MARKER).unlink()
    assert registry.workspaces()[0].reasonCode == "TOOL_WORKSPACE_UNMARKED"
    assert_error("TOOL_WORKSPACE_UNMARKED", lambda: read(registry, scope))
    assert not (root / MARKER).exists()
    (root / MARKER).write_text(json.dumps({"schemaVersion": "1.0", "workspaceId": "other"}))
    assert_error("TOOL_WORKSPACE_MARKER_INVALID", lambda: read(registry, scope))
    assert_error(
        "TOOL_WORKSPACE_UNAVAILABLE",
        lambda: read(registry, scope.model_copy(update={"workspaceId": "unknown"})),
    )


@pytest.mark.parametrize("configured", ["not JSON", "[]", '{"bad/name":"path"}', '{"demo": 123}'])
def test_invalid_configuration_fails_closed(configured):
    assert_error("TOOL_CONFIG_INVALID", lambda: WorkspaceToolRegistry(configured))


@pytest.mark.parametrize(
    "root",
    [
        "relative/path",
        str(Path.home()),
        str(Path.home().parent),
        str(Path.home().anchor),
        "//server/share/path",
    ],
)
def test_machine_roots_and_relative_or_network_roots_are_unavailable(root):
    registry = WorkspaceToolRegistry(json.dumps({"demo": root}))
    assert registry.workspaces()[0].reasonCode == "TOOL_WORKSPACE_UNSAFE"


def test_read_write_and_listing_bounds(workspace):
    root, registry, scope = workspace
    narrow = scope.model_copy(update={"maximumBytes": 3})
    assert_error("TOOL_IO_LIMIT", lambda: read(registry, narrow))
    assert_error("TOOL_IO_LIMIT", lambda: write(registry, narrow, content="éé"))
    assert_error(
        "TOOL_IO_LIMIT",
        lambda: registry.execute(ToolStep(tool="workspace.list", path="inputs"), narrow),
    )
    assert not (root / "reports").exists()
    for number in range(100):
        (root / "inputs" / f"{number}.txt").write_text("")
    assert_error(
        "TOOL_IO_LIMIT",
        lambda: registry.execute(ToolStep(tool="workspace.list", path="inputs"), scope),
    )


@pytest.mark.parametrize("data", [b"\xff\xfe", b"text\x00binary"])
def test_only_text_is_read(workspace, data):
    root, registry, scope = workspace
    (root / "inputs/brief.txt").write_bytes(data)
    assert_error("TOOL_CONTENT_NOT_TEXT", lambda: read(registry, scope))


def test_report_formats_are_bounded(workspace):
    root, registry, scope = workspace
    assert_error(
        "TOOL_REPORT_FORMAT",
        lambda: write(registry, scope, path="reports/run.exe", tool="workspace.report"),
    )
    assert not (root / "reports").exists()


def test_hardlinks_are_denied_for_read_and_replacement(workspace, tmp_path):
    root, registry, scope = workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("private outside")
    os.link(outside, root / "inputs/hardlink.txt")
    (root / "reports").mkdir()
    os.link(outside, root / "reports/hardlink.txt")
    assert_error("TOOL_PATH_UNSAFE", lambda: read(registry, scope, "inputs/hardlink.txt"))
    assert_error("TOOL_PATH_UNSAFE", lambda: write(registry, scope, path="reports/hardlink.txt"))
    assert outside.read_text() == "private outside"


def directory_link(target, link):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_real_directory_junction_or_symlink_cannot_escape(workspace, tmp_path):
    root, registry, scope = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private outside")
    directory_link(outside, root / "inputs/shortcut")
    directory_link(outside, root / "reports")
    assert_error("TOOL_PATH_UNSAFE", lambda: read(registry, scope, "inputs/shortcut/private.txt"))
    assert_error("TOOL_PATH_UNSAFE", lambda: write(registry, scope))
    assert not (outside / "result.txt").exists()
    listed = registry.execute(ToolStep(tool="workspace.list", path="inputs"), scope)
    assert listed.entries == ["brief.txt"]


def test_configured_root_itself_cannot_be_a_junction_or_symlink(workspace, tmp_path):
    root, _, _ = workspace
    link = tmp_path / "linked-workspace"
    directory_link(root, link)
    registry = WorkspaceToolRegistry(json.dumps({"demo": str(link)}))
    assert registry.workspaces()[0].reasonCode == "TOOL_PATH_UNSAFE"


@pytest.mark.skipif(os.name != "nt", reason="Windows hidden/system file attributes")
def test_windows_hidden_directory_is_denied(workspace):
    root, registry, scope = workspace
    inputs = root / "inputs"
    set_attributes = ctypes.windll.kernel32.SetFileAttributesW
    set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    set_attributes.restype = ctypes.c_int
    assert set_attributes(str(inputs), 0x2)
    try:
        assert_error("TOOL_PATH_DENIED", lambda: read(registry, scope))
    finally:
        assert set_attributes(str(inputs), 0x80)


def test_workspace_lock_prevents_overlapping_invocations(workspace):
    root, registry, scope = workspace
    with registry.access("demo") as (_, directory, _):
        with filesystem.workspace_lock(directory):
            assert_error("TOOL_WORKSPACE_BUSY", lambda: write(registry, scope))
    assert not (root / "reports").exists()
    assert write(registry, scope).written


def test_file_symlink_cannot_escape_even_when_swapped_after_metadata_check(
    workspace, tmp_path, monkeypatch
):
    root, registry, scope = workspace
    outside = tmp_path / "private.txt"
    outside.write_text("private outside")
    target = root / "inputs/brief.txt"
    probe = root / "inputs/probe.txt"
    try:
        probe.symlink_to(outside)
    except OSError:
        pytest.skip(
            "This Windows account lacks file symlink privileges; real junctions are separately tested"
        )
    probe.unlink()
    actual_open = os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(path).name == "brief.txt":
            swapped = True
            target.unlink()
            target.symlink_to(outside)
        return actual_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(filesystem.os, "open", swap_before_open)
    assert_error("TOOL_PATH_UNSAFE", lambda: read(registry, scope))
    assert swapped
    assert outside.read_text() == "private outside"


def test_marker_link_is_not_trusted(workspace, tmp_path):
    root, registry, _ = workspace
    marker = root / MARKER
    backup = tmp_path / "operator-marker.json"
    marker.rename(backup)
    os.link(backup, marker)
    assert registry.workspaces()[0].reasonCode == "TOOL_PATH_UNSAFE"


def test_disappearing_input_does_not_leak_absolute_path(workspace):
    root, registry, scope = workspace
    (root / "inputs/brief.txt").unlink()
    error = assert_error("TOOL_PATH_NOT_FOUND", lambda: read(registry, scope))
    assert str(root) not in error.message
