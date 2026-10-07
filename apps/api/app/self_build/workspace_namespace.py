"""Native owned workspace namespaces. Markers prove ownership, never authority."""

import json
import os
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path

from app.core.errors import DomainError
from app.self_build.policy import digest
from app.tool_execution.filesystem import open_directory, open_file, read_bytes, workspace_lock

KEY = re.compile(r"jarvis-[a-f0-9]{32}\Z")
MARKER = "owner.json"


def fail(code, message):
    raise DomainError(code, message, 409)


@contextmanager
def owned_namespace(policy, workspace_key, ownership, *, authority_check):
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
                # The target remains absent until the structured Git driver creates it.
                yield stack, root, root.path / workspace_key, digest(ownership)
    except DomainError:
        raise
    except OSError:
        fail(
            "SELF_BUILD_NAMESPACE_UNSAFE",
            "The namespace is linked, locked or changed during access.",
        )
