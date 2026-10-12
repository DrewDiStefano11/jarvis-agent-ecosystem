"""Native owned workspace namespaces. Markers prove ownership, never authority."""

import json
import os
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path

from app.core.errors import DomainError
from app.self_build.atomic_directory import pinned_target_directory, supports_atomic_directory
from app.self_build.policy import digest
from app.tool_execution.filesystem import open_directory, open_file, read_bytes, workspace_lock

KEY = re.compile(r"jarvis-[a-f0-9]{32}\Z")
MARKER = "owner.json"


def fail(code, message):
    raise DomainError(code, message, 409)


@contextmanager
def owned_namespace(
    policy,
    workspace_key,
    ownership,
    *,
    authority_check,
    validate_existing_target=None,
    before_target_claim=None,
):
    """Claim/verify one generated namespace under a registered, pinned root.

    The caller must persist creation intent and its native checkpoint first, and
    supply a live authority fence. This internal primitive grants no file/Git tool.
    Never adopt an existing unmarked namespace, overwrite another marker, or delete
    artifacts on failure. Marker identity includes a private durable operation nonce.
    """
    if (
        not isinstance(workspace_key, str)
        or not KEY.fullmatch(workspace_key)
        or not callable(authority_check)
    ):
        fail("SELF_BUILD_NAMESPACE_INVALID", "A generated key and native fence are required.")
    if (
        not isinstance(ownership, dict)
        or set(ownership) != {"workspaceId", "operationId", "planHash", "nonce"}
        or not all(isinstance(value, str) for value in ownership.values())
        or ownership["workspaceId"] != workspace_key
        or not re.fullmatch(r"[a-f0-9]{64}", ownership.get("planHash", ""))
        or not re.fullmatch(r"[a-f0-9]{64}", ownership.get("nonce", ""))
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", ownership.get("operationId", ""))
    ):
        fail("SELF_BUILD_NAMESPACE_INVALID", "Persist bounded native ownership before creation.")
    if not supports_atomic_directory():
        fail(
            "SELF_BUILD_NAMESPACE_PLATFORM_UNAVAILABLE", "Atomic target ownership requires Windows."
        )
    encoded = json.dumps(ownership, sort_keys=True, separators=(",", ":")).encode()
    policy = policy.checked()
    authority_check()
    try:
        with ExitStack() as stack:
            root_path = Path(policy.worktree_root)
            root = open_directory(stack, Path(root_path.anchor), internal=True)
            for leaf in root_path.parts[1:]:
                root = open_directory(stack, root.path / leaf, root, leaf, internal=True)
            private_key = ".jarvis-owner-" + workspace_key
            authority_check()
            try:
                os.stat(root.name(private_key), follow_symlinks=False, **root.kwargs)
            except FileNotFoundError:
                try:
                    os.stat(root.name(workspace_key), follow_symlinks=False, **root.kwargs)
                except FileNotFoundError:
                    pass
                else:
                    fail(
                        "SELF_BUILD_OWNERSHIP_CONFLICT",
                        "An existing target cannot receive a new ownership claim.",
                    )
            try:
                os.mkdir(root.name(private_key), 0o700, **root.kwargs)
                created = True
            except FileExistsError:
                created = False
            private = open_directory(
                stack, root.path / private_key, root, private_key, internal=True
            )
            if created:
                authority_check()
                with open_file(
                    private, MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, internal=True
                ) as fd:
                    position = 0
                    while position < len(encoded):
                        position += os.write(fd, encoded[position:])
                    os.fsync(fd)
            try:
                with open_file(private, MARKER, os.O_RDONLY, internal=True) as fd:
                    observed = read_bytes(fd, 8192)
            except FileNotFoundError:
                fail(
                    "SELF_BUILD_OWNERSHIP_UNCERTAIN",
                    "An unmarked namespace requires operator recovery.",
                )
            if observed != encoded:
                fail(
                    "SELF_BUILD_OWNERSHIP_CONFLICT",
                    "The generated namespace belongs to different intent.",
                )
            with workspace_lock(private):
                authority_check()
                target = root.path / workspace_key
                claim = None
                try:
                    with open_file(private, "target.json", os.O_RDONLY, internal=True) as fd:
                        claim = json.loads(read_bytes(fd, 4096))
                except FileNotFoundError:
                    pass
                except (ValueError, UnicodeError):
                    fail("SELF_BUILD_OWNERSHIP_UNCERTAIN", "Target claim integrity failed.")
                if claim is None:
                    if callable(before_target_claim):
                        before_target_claim(stack, root, target)
                    authority_check()
                    try:
                        target, info = pinned_target_directory(
                            stack, root, workspace_key, create=True
                        )
                    except FileExistsError:
                        fail(
                            "SELF_BUILD_OWNERSHIP_CONFLICT",
                            "A foreign target appeared before atomic claim.",
                        )
                    claim = dict(
                        workspaceId=workspace_key,
                        ownershipDigest=digest(ownership),
                        device=info.st_dev,
                        inode=info.st_ino,
                    )
                    encoded_claim = json.dumps(
                        claim, sort_keys=True, separators=(",", ":")
                    ).encode()
                    with open_file(
                        private, "target.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, internal=True
                    ) as fd:
                        position = 0
                        while position < len(encoded_claim):
                            position += os.write(fd, encoded_claim[position:])
                        os.fsync(fd)
                else:
                    target, info = pinned_target_directory(stack, root, workspace_key, create=False)
                    if claim != dict(
                        workspaceId=workspace_key,
                        ownershipDigest=digest(ownership),
                        device=info.st_dev,
                        inode=info.st_ino,
                    ):
                        fail(
                            "SELF_BUILD_OWNERSHIP_CONFLICT",
                            "Target identity differs from its durable native claim.",
                        )
                    if callable(validate_existing_target):
                        validate_existing_target(stack, root, target)
                authority_check()
                yield stack, root, target, digest(ownership)
    except DomainError:
        raise
    except OSError:
        fail(
            "SELF_BUILD_NAMESPACE_UNSAFE",
            "The namespace is linked, locked or changed during access.",
        )
