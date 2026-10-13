"""Private Windows change verifier. Fixed reads only, no staging or commit effects.

The caller must provide a validated ready creation record, its private owner and
a live runtime fence. The final callback runs while source/metadata remain pinned.
"""

import json
import os
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

from app.models import git_changes as contracts
from app.models import source_change as source_contracts
from app.models.git_changes import FileChange, FileIdentity, GitChangeObservation, NativeArtifact
from app.models.source_change import source_path
from app.self_build.checkout_driver import CheckoutDriver, fail, pin_read_tree, supports_mutation
from app.self_build.creation_service import CREATION_POLICY_DIGEST, ownership
from app.self_build.git_namespace_watch import namespace_watch
from app.self_build.git_observer import COMMIT, pinned_metadata_file
from app.self_build.materialization import blob_digest, inventory
from app.self_build.policy import digest
from app.self_build.workspace_namespace import owned_namespace
from app.tool_execution.filesystem import (
    open_directory,
    open_file,
    read_bytes,
    relative_parts,
    repository_parts,
)

MAX_NODES = 65538
INTERNAL = {".git", ".jarvis-workspace.json"}
VERIFICATION_POLICY_DIGEST = sha256(
    (
        Path(__file__).read_text(encoding="utf-8")
        + Path(contracts.__file__).read_text(encoding="utf-8")
        + Path(source_contracts.__file__).read_text(encoding="utf-8")
        + CREATION_POLICY_DIGEST
    ).encode()
).hexdigest()


def identity(content, mode):
    return FileIdentity(
        mode=mode,
        blob_sha=blob_digest(content),
        sha256=sha256(content).hexdigest(),
        byte_count=len(content),
    )


def path_nodes(paths, *, artifacts=()):
    """Check every component, including file/directory and parent-case collisions."""
    nodes = {}
    for path, is_file in paths:
        if path in artifacts and is_file:
            segments = relative_parts(path)
        else:
            source_path(path)
            segments = repository_parts(path)
        if path not in artifacts and any(
            part.casefold().startswith(".jarvis-") for part in segments
        ):
            fail("SELF_BUILD_CHANGE_PROTECTED", "Private workspace paths cannot be source changes.")
        if len(segments) > 16:
            fail("SELF_BUILD_CHANGE_LIMIT", "Repository path depth exceeds its bound.")
        for length in range(1, len(segments) + 1):
            name = "/".join(segments[:length])
            node = (name, is_file if length == len(segments) else False)
            prior = nodes.get(name.casefold())
            if prior is not None and prior != node:
                fail("SELF_BUILD_CHANGE_COLLISION", "Repository path components collide.")
            nodes[name.casefold()] = node


def index_entries(raw):
    entries = []
    try:
        if raw and not raw.endswith(b"\0"):
            raise ValueError("truncated index")
        for record in raw.split(b"\0"):
            if not record:
                continue
            header, name = record.split(b"\t", 1)
            mode, blob, stage = header.decode("ascii").split()
            path = name.decode("utf-8")
            if stage != "0" or mode not in {"100644", "100755"} or not COMMIT.fullmatch(blob):
                raise ValueError("ambiguous index")
            repository_parts(path)
            entries.append([mode, blob, path])
            if len(entries) > 4096:
                raise ValueError("index limit")
        if len({item[2].casefold() for item in entries}) != len(entries):
            raise ValueError("duplicate index")
        path_nodes((item[2], True) for item in entries)
        return entries
    except (ValueError, UnicodeError):
        fail("SELF_BUILD_CHANGE_INDEX_INVALID", "The dedicated index is ambiguous or malformed.")


class ChangeVerifier:
    def __init__(self, observer, authority_check):
        self.driver = CheckoutDriver(observer, authority_check)
        self.guard = authority_check

    def inspect(self, policy, record, nonce, *, finalize=None, native_artifacts=()):
        native_artifacts = tuple(NativeArtifact.model_validate(item) for item in native_artifacts)
        if (
            any(item.kind != "source_backup" for item in native_artifacts)
            or len(native_artifacts) > 64
        ):
            fail(
                "SELF_BUILD_CHANGE_PROTECTED", "Only authenticated source backups may be supplied."
            )
        if len({item.path.casefold() for item in native_artifacts}) != len(native_artifacts):
            fail("SELF_BUILD_CHANGE_COLLISION", "Native artifact paths collide.")
        if not supports_mutation():
            fail(
                "SELF_BUILD_CHANGE_PLATFORM_UNAVAILABLE",
                "Stable change inspection requires Windows.",
            )
        plan = record.plan
        if (
            record.state != "ready"
            or record.checkpoint_id is None
            or record.ownership_digest != digest(ownership(record, nonce))
            or digest(plan.model_dump(mode="json", exclude={"plan_hash"})) != plan.plan_hash
            or plan.tool_sha256 != self.driver.observer.executable_hash
            or plan.tool_identity_digest
            != digest([str(self.driver.observer.executable), self.driver.observer.executable_hash])
        ):
            fail(
                "SELF_BUILD_CHANGE_LINEAGE_INVALID",
                "Use validated ready ownership and its pinned tool.",
            )
        self.guard()
        policy = policy.checked()
        if (
            digest(policy.model_dump()) != plan.workspace.policy_digest
            or policy.repository_identity != plan.workspace.repository_identity
        ):
            fail("SELF_BUILD_PLAN_CHANGED", "Registered repository policy changed.")
        try:
            return self._inspect(policy, record, nonce, finalize, native_artifacts)
        except (OSError, ValueError, UnicodeError):
            fail("SELF_BUILD_CHANGE_UNSAFE", "Change inventory is unavailable, unsafe or changed.")

    def _inspect(self, policy, record, nonce, finalize, native_artifacts):
        plan = record.plan
        owner = ownership(record, nonce)
        # Inspection may never claim a missing workspace. Pin all existing
        # ownership files/ancestors before entering the native namespace lock.
        with ExitStack() as existing:
            root_path = Path(policy.worktree_root)
            root = open_directory(existing, Path(root_path.anchor), internal=True)
            for leaf in root_path.parts[1:]:
                root = open_directory(existing, root.path / leaf, root, leaf, internal=True)
            private = open_directory(
                existing,
                root.path / (".jarvis-owner-" + record.workspace_id),
                root,
                ".jarvis-owner-" + record.workspace_id,
                internal=True,
            )
            for leaf in ("owner.json", "target.json"):
                with open_file(private, leaf, os.O_RDONLY, internal=True):
                    pass
            open_directory(
                existing, root.path / record.workspace_id, root, record.workspace_id, internal=True
            )
            with owned_namespace(
                policy,
                record.workspace_id,
                owner,
                authority_check=self.guard,
                validate_existing_target=lambda stack, parent, target: (
                    self.driver.validate_existing_target(stack, parent, target, policy, plan)
                ),
            ) as (stack, parent, target, _):
                source_check = pin_read_tree(stack, target, self.guard, maximum=MAX_NODES)
                metadata_check = pin_read_tree(
                    stack, Path(policy.primary_root) / ".git", self.guard
                )
                # Repeat registration under the metadata pins: an earlier
                # preflight must not stand in for the final measured HEAD.
                self.driver.validate_existing_target(stack, parent, target, policy, plan)
                owner_check = stack.enter_context(namespace_watch(private.path))
                for leaf in ("owner.json", "target.json"):
                    stack.enter_context(pinned_metadata_file(private.path / leaf))
                checkout = open_directory(stack, target, parent, target.name, internal=True)
                with open_file(private, "owner.json", os.O_RDONLY, internal=True) as fd:
                    if json.loads(read_bytes(fd, 8192)) != owner:
                        fail("SELF_BUILD_OWNERSHIP_CONFLICT", "Private ownership changed.")
                with open_file(
                    checkout, ".jarvis-workspace.json", os.O_RDONLY, internal=True
                ) as fd:
                    marker = json.loads(read_bytes(fd, 8192))
                expected_marker = dict(
                    schemaVersion="1.0",
                    workspaceId=record.workspace_id,
                    allowedTools=list(plan.allowed_tools),
                    readPrefixes=list(plan.read_prefixes),
                    writePrefixes=list(plan.write_prefixes),
                    planHash=plan.plan_hash,
                )
                if marker != expected_marker:
                    fail("SELF_BUILD_OWNERSHIP_CONFLICT", "Workspace marker differs from creation.")
                repository = self.driver.observer.inspect(policy, plan.workspace.base_sha)
                if (
                    repository["base_tree_sha"] != plan.base_tree_sha
                    or repository["inventory_digest"] != plan.inventory_digest
                ):
                    fail("SELF_BUILD_CHANGE_LINEAGE_INVALID", "Original Git inventory changed.")
                original = inventory(self.driver, policy, plan, target)
                indexed = index_entries(self.driver.invoke(policy, "index_inventory", plan, target))
                flags = self.driver.invoke(policy, "index_flags", plan, target)
                expected_flags = b"".join(
                    b"H " + item[2].encode("utf-8") + b"\0" for item in indexed
                )
                if flags != expected_flags:
                    fail(
                        "SELF_BUILD_CHANGE_INDEX_INVALID",
                        "Sparse or assume-unchanged index entries require operator recovery.",
                    )
                actual, directories, total = {}, [], 0
                expected_artifacts = {item.path: item for item in native_artifacts}
                measured_artifacts = {}

                def walk_error(error):
                    raise error

                for directory, children, files in os.walk(
                    target, followlinks=False, onerror=walk_error
                ):
                    self.guard()
                    for leaf in children:
                        directories.append((Path(directory) / leaf).relative_to(target).as_posix())
                    for leaf in files:
                        path = (Path(directory) / leaf).relative_to(target).as_posix()
                        if path == ".jarvis-workspace.lock":
                            with open_file(checkout, leaf, os.O_RDONLY, internal=True) as fd:
                                if os.fstat(fd).st_size != 1 or read_bytes(fd, 1) != b"0":
                                    fail(
                                        "SELF_BUILD_CHANGE_UNSAFE", "Invalid coordination metadata."
                                    )
                            continue
                        internal = path in INTERNAL
                        artifact = expected_artifacts.get(path)
                        # Refuse protected/private/deep paths before reading bytes.
                        if not internal and artifact is None:
                            path_nodes([(path, True)])
                            if len(actual) >= 4096:
                                fail("SELF_BUILD_CHANGE_LIMIT", "File inventory exceeds its bound.")
                        with ExitStack() as file_stack:
                            parent_directory = checkout
                            segments = (
                                relative_parts(path)
                                if internal or artifact
                                else repository_parts(path)
                            )
                            for segment in segments[:-1]:
                                parent_directory = open_directory(
                                    file_stack,
                                    parent_directory.path / segment,
                                    parent_directory,
                                    segment,
                                    internal=True,
                                )
                            with open_file(
                                parent_directory, segments[-1], os.O_RDONLY, internal=True
                            ) as fd:
                                maximum = (
                                    artifact.byte_count
                                    if artifact
                                    else 8192
                                    if internal
                                    else plan.maximum_file_bytes
                                )
                                content = read_bytes(fd, maximum)
                        if internal or artifact:
                            content_hash = sha256(content).hexdigest()
                            if artifact:
                                if (
                                    content_hash != artifact.sha256
                                    or len(content) != artifact.byte_count
                                ):
                                    fail(
                                        "SELF_BUILD_CHANGE_PROTECTED",
                                        "Native backup identity differs from history.",
                                    )
                                measured_artifacts[path] = artifact
                            else:
                                kind = {
                                    ".git": "git_pointer",
                                    ".jarvis-workspace.json": "workspace_marker",
                                }[path]
                                measured_artifacts[path] = NativeArtifact(
                                    path=path,
                                    kind=kind,
                                    sha256=content_hash,
                                    byte_count=len(content),
                                    provenance_digest=record.ownership_digest,
                                )
                            continue
                        total += len(content)
                        if total > plan.maximum_total_bytes:
                            fail("SELF_BUILD_CHANGE_LIMIT", "Source bytes exceed their bound.")
                        actual[path] = content
                # Combine all three namespaces so a deleted parent cannot hide
                # an added case alias or a file/directory replacement.
                if not expected_artifacts.keys() <= measured_artifacts.keys():
                    fail(
                        "SELF_BUILD_CHANGE_PROTECTED", "An authenticated native backup is missing."
                    )
                path_nodes(
                    [(item[2], True) for item in original]
                    + [(item[2], True) for item in indexed]
                    + [(path, True) for path in actual]
                    + [(path, False) for path in directories]
                    + [(path, True) for path in measured_artifacts],
                    artifacts=measured_artifacts,
                )
                modes = {item[2]: item[0] for item in indexed}
                before = {}
                for mode, blob, path, size in original:
                    content = self.driver.invoke(policy, "blob", plan, target, blob=blob)
                    if len(content) != size or blob_digest(content) != blob:
                        fail(
                            "SELF_BUILD_CHANGE_UNSAFE",
                            "Original content does not match its Git object.",
                        )
                    before[path] = identity(content, mode)
                after = {
                    path: identity(
                        content, modes.get(path, before[path].mode if path in before else "100644")
                    )
                    for path, content in actual.items()
                }
                changes = []
                for path in sorted(before.keys() | after.keys()):
                    left, right = before.get(path), after.get(path)
                    if left != right:
                        changes.append(
                            FileChange(
                                path=path,
                                before=left,
                                after=right,
                                kind="added"
                                if left is None
                                else "deleted"
                                if right is None
                                else "modified",
                                tracked=path in before or path in modes,
                            )
                        )
                payload = dict(
                    schema_version="1.1",
                    workspace_id=record.workspace_id,
                    creation_operation_id=record.operation_id,
                    creation_plan_hash=plan.plan_hash,
                    ownership_digest=record.ownership_digest,
                    repository_identity=policy.repository_identity,
                    branch=plan.workspace.branch,
                    original_base_sha=plan.workspace.base_sha,
                    head_sha=plan.workspace.base_sha,
                    original_tree_sha=plan.base_tree_sha,
                    tool_sha256=plan.tool_sha256,
                    verification_policy_digest=VERIFICATION_POLICY_DIGEST,
                    mode_source="git_index_windows",
                    index_clean=indexed == [list(item[:3]) for item in original],
                    worktree_clean=not changes,
                    tracked_file_count=len(indexed),
                    untracked_file_count=len(actual.keys() - modes.keys()),
                    inventory_digest=digest(
                        [
                            [path, item.model_dump(mode="json")]
                            for path, item in sorted(after.items())
                        ]
                    ),
                    index_digest=digest(indexed),
                    original_inventory=[
                        dict(path=path, identity=item.model_dump(mode="json"))
                        for path, item in sorted(before.items())
                    ],
                    current_inventory=[
                        dict(path=path, identity=item.model_dump(mode="json"))
                        for path, item in sorted(after.items())
                    ],
                    index_inventory=[
                        dict(mode=mode, blob_sha=blob, path=path) for mode, blob, path in indexed
                    ],
                    internal_inventory=[
                        item.model_dump(mode="json")
                        for _, item in sorted(measured_artifacts.items())
                    ],
                    changes=[item.model_dump(mode="json") for item in changes],
                )
                fingerprint = digest(payload)
                measured = GitChangeObservation(
                    **payload,
                    change_fingerprint=fingerprint,
                    commit_preview_fingerprint=digest(
                        [
                            "git.commit.preview/1.0",
                            fingerprint,
                            plan.workspace.base_sha,
                            payload["changes"],
                        ]
                    ),
                )

                def publication_guard(*, check_authority=True):
                    # A native transaction callback supplies its own in-session
                    # authority fence; never open a nested SQLite writer there.
                    if check_authority:
                        self.guard()
                    source_check()
                    metadata_check()
                    owner_check()
                    if check_authority:
                        self.guard()

                publication_guard()
                result = measured if finalize is None else finalize(measured, publication_guard)
                publication_guard()
                return result
