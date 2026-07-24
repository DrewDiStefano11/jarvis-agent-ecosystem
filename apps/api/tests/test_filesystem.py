from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.filesystem import (
    InvalidSandboxPathError,
    LocalFilesystemSandbox,
    SandboxConfiguration,
    SandboxFileTooLargeError,
    SandboxOperationError,
    SandboxPathExistsError,
    SandboxPathNotFoundError,
    SandboxReadOnlyError,
    SandboxViolationError,
)
from app.main import create_app


def configuration(
    root: Path,
    *,
    maximum_file_size: int = 1024,
    allowed_extensions: str = "",
    restricted_directories: str = "",
    temporary_directory: str = ".sandbox-tmp",
    read_only: bool = False,
) -> SandboxConfiguration:
    return SandboxConfiguration.build(
        root=root,
        maximum_file_size=maximum_file_size,
        allowed_extensions=allowed_extensions,
        restricted_directories=restricted_directories,
        temporary_directory=temporary_directory,
        read_only=read_only,
    )


def sandbox(root: Path, **overrides: object) -> LocalFilesystemSandbox:
    return LocalFilesystemSandbox(configuration(root, **overrides))


def test_write_read_metadata_and_explicit_overwrite(tmp_path: Path) -> None:
    filesystem = sandbox(tmp_path / "sandbox")

    created = filesystem.write_file("notes.txt", b"first")

    assert created.path == "notes.txt"
    assert created.size == 5
    assert created.is_file is True
    assert filesystem.read_file("notes.txt") == b"first"
    assert filesystem.exists("notes.txt") is True
    with pytest.raises(SandboxPathExistsError):
        filesystem.write_file("notes.txt", b"implicit overwrite")

    modified = filesystem.write_file("notes.txt", b"second", overwrite=True)

    assert modified.size == 6
    assert filesystem.read_file("notes.txt") == b"second"


def test_append_creates_and_extends_files_with_size_enforcement(tmp_path: Path) -> None:
    filesystem = sandbox(tmp_path / "sandbox", maximum_file_size=6)

    filesystem.append_file("events.log", b"abc")
    metadata = filesystem.append_file("events.log", b"def")

    assert metadata.size == 6
    assert filesystem.read_file("events.log") == b"abcdef"
    with pytest.raises(SandboxFileTooLargeError):
        filesystem.append_file("events.log", b"g")
    assert filesystem.read_file("events.log") == b"abcdef"


def test_nested_directories_unicode_names_listing_and_deletion(tmp_path: Path) -> None:
    filesystem = sandbox(tmp_path / "sandbox")

    filesystem.create_directory("work/深い", parents=True)
    filesystem.write_file("work/深い/résumé.txt", b"hello", create_parents=True)

    entries = filesystem.list_directory("work/深い")
    assert [entry.path for entry in entries] == ["work/深い/résumé.txt"]
    assert filesystem.metadata("work/深い").is_directory is True

    filesystem.delete_file("work/深い/résumé.txt")
    filesystem.delete_directory("work", recursive=True)
    assert filesystem.exists("work") is False


def test_non_recursive_directory_delete_rejects_nonempty_directory(tmp_path: Path) -> None:
    filesystem = sandbox(tmp_path / "sandbox")
    filesystem.write_file("nested/item.txt", b"value", create_parents=True)

    with pytest.raises(SandboxOperationError):
        filesystem.delete_directory("nested")

    assert filesystem.read_file("nested/item.txt") == b"value"


def test_copy_move_and_rename_preserve_content(tmp_path: Path) -> None:
    filesystem = sandbox(tmp_path / "sandbox")
    filesystem.create_directory("archive")
    filesystem.write_file("source.txt", b"payload")

    copied = filesystem.copy("source.txt", "copy.txt")
    moved = filesystem.move("copy.txt", "archive/moved.txt")
    renamed = filesystem.rename("archive/moved.txt", "renamed.txt")

    assert copied.path == "copy.txt"
    assert moved.path == "archive/moved.txt"
    assert renamed.path == "archive/renamed.txt"
    assert filesystem.read_file("source.txt") == b"payload"
    assert filesystem.read_file("archive/renamed.txt") == b"payload"
    assert filesystem.exists("copy.txt") is False


def test_missing_paths_have_deterministic_errors(tmp_path: Path) -> None:
    filesystem = sandbox(tmp_path / "sandbox")

    with pytest.raises(SandboxPathNotFoundError) as read_error:
        filesystem.read_file("missing.txt")
    with pytest.raises(SandboxPathNotFoundError):
        filesystem.metadata("missing.txt")
    with pytest.raises(SandboxPathNotFoundError):
        filesystem.delete_file("missing.txt")

    assert read_error.value.code == "FILESYSTEM_PATH_NOT_FOUND"
    assert read_error.value.status_code == 404


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../escape.txt",
        "folder/../../escape.txt",
        "/absolute.txt",
        "C:\\escape.txt",
        "\\\\server\\share\\escape.txt",
        "folder//file.txt",
        "folder/./file.txt",
        "NUL.txt",
        "trailing-dot.",
        "wild*.txt",
        "null\x00byte.txt",
    ],
)
def test_invalid_and_traversal_paths_are_rejected(tmp_path: Path, unsafe_path: str) -> None:
    filesystem = sandbox(tmp_path / "sandbox")

    with pytest.raises((InvalidSandboxPathError, SandboxViolationError)):
        filesystem.write_file(unsafe_path, b"blocked", create_parents=True)

    assert not (tmp_path / "escape.txt").exists()


def test_restricted_and_internal_directories_are_inaccessible(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    filesystem = sandbox(
        root,
        restricted_directories="secrets,system/protected",
        temporary_directory=".staging",
    )

    for path in ("secrets/key.txt", "system/protected/config.txt", ".staging/item.txt"):
        with pytest.raises(SandboxViolationError):
            filesystem.write_file(path, b"blocked", create_parents=True)

    (root / "secrets").mkdir()
    filesystem.create_directory("visible")
    assert [entry.path for entry in filesystem.list_directory(".")] == ["visible"]


def test_allowed_extensions_apply_to_all_file_operations(tmp_path: Path) -> None:
    filesystem = sandbox(tmp_path / "sandbox", allowed_extensions="txt,.JSON")

    filesystem.write_file("valid.TXT", b"text")
    filesystem.write_file("valid.json", b"{}")

    with pytest.raises(SandboxViolationError):
        filesystem.write_file("script.py", b"pass")
    with pytest.raises(SandboxViolationError):
        filesystem.read_file("script.py")


def test_large_external_file_cannot_be_read(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    filesystem = sandbox(root, maximum_file_size=4)
    (root / "external.txt").write_bytes(b"12345")

    with pytest.raises(SandboxFileTooLargeError):
        filesystem.read_file("external.txt")
    with pytest.raises(SandboxFileTooLargeError):
        filesystem.write_file("large.txt", b"12345")


def test_symlinks_are_rejected_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    filesystem = sandbox(root)
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symbolic links is not permitted on this platform")

    with pytest.raises(SandboxViolationError):
        filesystem.read_file("linked/secret.txt")
    with pytest.raises(SandboxViolationError):
        filesystem.list_directory(".")
    with pytest.raises(SandboxViolationError):
        filesystem.delete_directory("linked", recursive=True)

    assert (outside / "secret.txt").read_bytes() == b"secret"


def test_read_only_mode_allows_reads_and_rejects_every_mutation(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / "existing.txt").write_bytes(b"safe")
    filesystem = sandbox(root, read_only=True)

    assert filesystem.read_file("existing.txt") == b"safe"
    mutations = [
        lambda: filesystem.write_file("new.txt", b"x"),
        lambda: filesystem.append_file("existing.txt", b"x"),
        lambda: filesystem.create_directory("new"),
        lambda: filesystem.delete_file("existing.txt"),
        lambda: filesystem.delete_directory("folder"),
        lambda: filesystem.move("existing.txt", "moved.txt"),
    ]
    for mutation in mutations:
        with pytest.raises(SandboxReadOnlyError):
            mutation()


def test_read_only_mode_requires_an_existing_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="read-only sandbox root"):
        sandbox(tmp_path / "missing", read_only=True)


def test_concurrent_appends_are_serialized_without_lost_data(tmp_path: Path) -> None:
    filesystem = sandbox(tmp_path / "sandbox", maximum_file_size=10_000)
    filesystem.write_file("events.log", b"")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: filesystem.append_file("events.log", value), [b"x"] * 200))

    assert filesystem.read_file("events.log") == b"x" * 200


def test_concurrent_create_has_exactly_one_winner(tmp_path: Path) -> None:
    first = sandbox(tmp_path / "sandbox")
    second = sandbox(tmp_path / "sandbox")

    def create(value: bytes) -> bytes | None:
        try:
            first.write_file("winner.txt", value)
            return value
        except SandboxPathExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (b"first", b"second")))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert second.read_file("winner.txt") == winners[0]


def test_failed_atomic_overwrite_preserves_original_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filesystem = sandbox(tmp_path / "sandbox")
    filesystem.write_file("important.txt", b"original")

    def fail_replace(_: object, __: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(SandboxOperationError, match="filesystem is full"):
        filesystem.write_file("important.txt", b"replacement", overwrite=True)

    assert filesystem.read_file("important.txt") == b"original"
    assert list((filesystem.root / ".sandbox-tmp").iterdir()) == []


def test_configuration_is_validated_and_normalized_at_settings_startup(
    tmp_path: Path,
) -> None:
    settings = Settings(
        JARVIS_SANDBOX_ROOT=tmp_path / "root",
        JARVIS_SANDBOX_MAXIMUM_FILE_SIZE=25,
        JARVIS_SANDBOX_ALLOWED_EXTENSIONS="TXT,json",
        JARVIS_SANDBOX_RESTRICTED_DIRECTORIES="private,system/cache",
        JARVIS_SANDBOX_TEMPORARY_DIRECTORY=".work",
        JARVIS_SANDBOX_READ_ONLY=False,
    )

    configured = settings.filesystem_sandbox_configuration()
    assert configured.root == (tmp_path / "root").absolute()
    assert configured.maximum_file_size == 25
    assert configured.allowed_extensions == frozenset({".txt", ".json"})
    assert configured.restricted_directories == (("private",), ("system", "cache"))

    with pytest.raises(ValidationError):
        Settings(JARVIS_SANDBOX_MAXIMUM_FILE_SIZE=0)
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(JARVIS_SANDBOX_ROOT="")
    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(
            JARVIS_SANDBOX_RESTRICTED_DIRECTORIES=".work/private",
            JARVIS_SANDBOX_TEMPORARY_DIRECTORY=".work",
        )


def test_application_injects_the_configured_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "injected-root"
    monkeypatch.setenv("JARVIS_SANDBOX_ROOT", str(root))
    application = create_app(
        delay_ms=1,
        database_url=f"sqlite:///{(tmp_path / 'application.db').as_posix()}",
    )

    try:
        assert isinstance(application.state.filesystem_sandbox, LocalFilesystemSandbox)
        assert application.state.filesystem_sandbox.root == root.resolve()
        assert (
            application.state.filesystem_sandbox.configuration.maximum_file_size == 10 * 1024 * 1024
        )
    finally:
        application.state.engine.dispose()


def test_security_logs_include_paths_but_never_file_contents(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    filesystem = sandbox(tmp_path / "sandbox")
    secret = b"do-not-log-this-content"

    with caplog.at_level(logging.INFO, logger="jarvis.filesystem"):
        filesystem.write_file("audit.txt", secret)
        with pytest.raises(InvalidSandboxPathError):
            filesystem.read_file("../outside.txt")

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "do-not-log-this-content" not in messages
    assert {getattr(record, "sandbox_event", None) for record in caplog.records} >= {
        "created",
        "path_rejected",
    }
