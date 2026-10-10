"""Private structured native Git operations under persisted creation authority."""

import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from app.core.errors import DomainError
from app.self_build.git_image import pinned_image
from app.self_build.git_namespace_watch import namespace_watch
from app.self_build.git_observer import COMMIT, pinned_metadata_file
from app.self_build.owned_process import owned_process
from app.self_build.policy import digest
from app.self_build.workspace_namespace import owned_namespace
from app.tool_execution.filesystem import open_directory, open_file, read_bytes


def supports_mutation():
    return os.name == "nt"


def fail(code, message):
    raise DomainError(code, message, 409)


@contextmanager
def pinned_object_store(policy, authority_check):
    """Git mutations may change their own registration/index, never the object store.

    Keep existing object bytes immutable and observe absent object/alternate names
    throughout each fixed command, including writes to the dedicated index.
    """
    with ExitStack() as stack:
        primary_path = Path(policy.primary_root)
        directory = open_directory(stack, Path(primary_path.anchor), internal=True)
        for leaf in primary_path.parts[1:]:
            directory = open_directory(stack, directory.path / leaf, directory, leaf, internal=True)
        common = open_directory(stack, directory.path / ".git", directory, ".git", internal=True)
        objects = open_directory(stack, common.path / "objects", common, "objects", internal=True)
        check = stack.enter_context(namespace_watch(objects.path))
        count = 0

        def walk_error(error):
            raise error

        for parent, directories, files in os.walk(
            objects.path, followlinks=False, onerror=walk_error
        ):
            authority_check()
            count += len(directories) + len(files)
            if count > 16384:
                fail("SELF_BUILD_GIT_METADATA_LIMIT", "Object storage exceeds its bounded scope.")
            for name in directories:
                open_directory(stack, Path(parent) / name, internal=True)
            for name in files:
                stack.enter_context(pinned_metadata_file(Path(parent) / name))
        check()
        yield check
        check()


class CheckoutDriver:
    """Caller supplies persisted preparation/native checkpoint and a live fence.

    This driver cannot approve a plan, adopt a lease, mark a workspace ready or
    materialize source. Its single mutation creates the exact generated branch
    and a no-checkout linked worktree. Uncertain artifacts are preserved.
    """

    def __init__(self, observer, authority_check):
        if not callable(authority_check):
            raise ValueError("native authority callback required")
        self.observer = observer
        self.authority_check = authority_check
        if observer is not None:
            observer.authority_check = authority_check

    def invoke(self, policy, operation, plan, target, *, blob=None):
        if not supports_mutation():
            fail("SELF_BUILD_CHECKOUT_PLATFORM_UNAVAILABLE", "Native commands require Windows.")
        self.authority_check()
        try:
            with pinned_object_store(policy, self.authority_check) as check_objects:
                result = self._invoke(policy, operation, plan, target, blob=blob)
                check_objects()
                return result
        except OSError:
            fail(
                "SELF_BUILD_GIT_METADATA_INVALID",
                "Native object storage is unavailable or changed.",
            )

    def _invoke(self, policy, operation, plan, target, *, blob=None):
        if operation == "blob" and (not isinstance(blob, str) or not COMMIT.fullmatch(blob)):
            fail("SELF_BUILD_GIT_OPERATION_INVALID", "An approved exact blob object is required.")
        metadata = Path(policy.primary_root) / ".git" / "worktrees" / plan.workspace_id
        commands = {
            "create": [
                "worktree",
                "add",
                "--no-checkout",
                "-b",
                plan.workspace.branch,
                str(target),
                plan.workspace.base_sha,
            ],
            "registrations": ["worktree", "list", "--porcelain", "-z"],
            "inventory": ["ls-tree", "-r", "-l", "--full-tree", "-z", plan.workspace.base_sha],
            "blob": ["cat-file", "blob", blob],
            "index": [
                "--git-dir=" + str(metadata),
                "--work-tree=" + str(target),
                "read-tree",
                plan.workspace.base_sha,
            ],
            "index_inventory": [
                "--git-dir=" + str(metadata),
                "--work-tree=" + str(target),
                "ls-files",
                "--stage",
                "-z",
            ],
        }
        if operation not in commands:
            fail("SELF_BUILD_GIT_OPERATION_INVALID", "Unsupported native registration operation.")
        self.authority_check()
        before = self.observer._alternates_state(policy)
        executable = self.observer._tool(policy)
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }
        environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_NO_REPLACE_OBJECTS="1",
            GIT_TERMINAL_PROMPT="0",
            LC_ALL="C",
        )
        if operation in {"index", "index_inventory"}:
            environment.update(
                GIT_COMMON_DIR=str(Path(policy.primary_root) / ".git"),
                GIT_OBJECT_DIRECTORY=str(Path(policy.primary_root) / ".git" / "objects"),
            )
        output_limit = plan.maximum_file_bytes if operation == "blob" else 1048576
        argv = [
            executable,
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "protocol.allow=never",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            *commands[operation],
        ]
        buffers = [bytearray(), bytearray()]
        lock, overflow, errors = Lock(), Event(), Event()
        last = [monotonic()]
        threads = []

        def drain(stream, index, maximum):
            try:
                while chunk := stream.read1(8192):
                    with lock:
                        last[0] = monotonic()
                        if len(buffers[index]) + len(chunk) > maximum:
                            overflow.set()
                        elif not overflow.is_set():
                            buffers[index].extend(chunk)
            except (OSError, ValueError):
                errors.set()

        try:
            with pinned_image(executable, self.observer.executable_hash) as launch:
                with owned_process(
                    argv,
                    cwd=policy.primary_root,
                    env=environment,
                    authority_check=self.authority_check,
                    launch=launch,
                ) as process:
                    threads = [
                        Thread(target=drain, args=(process.stdout, 0, output_limit), daemon=True),
                        Thread(target=drain, args=(process.stderr, 1, 65536), daemon=True),
                    ]
                    for thread in threads:
                        thread.start()
                    checked = monotonic()
                    while process.poll() is None or any(thread.is_alive() for thread in threads):
                        if monotonic() - checked >= 0.2:
                            self.authority_check()
                            checked = monotonic()
                        with lock:
                            inactive = monotonic() - last[0] > self.observer.inactivity_seconds
                        if overflow.is_set():
                            fail(
                                "SELF_BUILD_GIT_OUTPUT_LIMIT",
                                "Native registration output exceeded its bound.",
                            )
                        if errors.is_set():
                            fail(
                                "SELF_BUILD_GIT_READ_FAILED",
                                "Native registration output could not be measured.",
                            )
                        if inactive:
                            fail(
                                "SELF_BUILD_GIT_INACTIVE",
                                "Native registration stopped producing output.",
                            )
                        for thread in threads:
                            thread.join(0.02)
                    if overflow.is_set():
                        fail(
                            "SELF_BUILD_GIT_OUTPUT_LIMIT",
                            "Native registration output exceeded its bound.",
                        )
                    if errors.is_set():
                        fail(
                            "SELF_BUILD_GIT_READ_FAILED",
                            "Native registration output is incomplete.",
                        )
                    if process.returncode != 0:
                        fail(
                            "SELF_BUILD_GIT_CREATION_UNCERTAIN",
                            "Native registration did not acknowledge creation; preserve artifacts for recovery.",
                        )
        except OSError:
            fail("SELF_BUILD_GIT_UNAVAILABLE", "The configured native Git tool could not run.")
        finally:
            for thread in threads:
                if thread.ident is not None:
                    thread.join(1)
        self.authority_check()
        if before != self.observer._alternates_state(policy):
            fail("SELF_BUILD_GIT_STATE_CHANGED", "Git object metadata changed during registration.")
        return bytes(buffers[0])

    def verify(self, stack, root, target, policy, plan, common):
        checkout = open_directory(stack, target, root, target.name, internal=True)
        with open_file(checkout, ".git", os.O_RDONLY, internal=True) as fd:
            pointer = read_bytes(fd, 4096).decode("utf-8").strip()
        expected = Path(policy.primary_root) / ".git" / "worktrees" / plan.workspace_id
        if not pointer.startswith("gitdir: ") or Path(pointer[8:]) != expected:
            fail(
                "SELF_BUILD_GIT_REGISTRATION_INVALID",
                "Linked worktree pointer differs from its generated registration.",
            )
        registrations = open_directory(
            stack, common.path / "worktrees", common, "worktrees", internal=True
        )
        directory = open_directory(stack, expected, registrations, plan.workspace_id, internal=True)

        def read(leaf):
            with open_file(directory, leaf, os.O_RDONLY, internal=True) as fd:
                return read_bytes(fd, 4096).decode("utf-8").strip()

        if (
            read("HEAD") != "ref: refs/heads/" + plan.workspace.branch
            or read("commondir") != "../.."
            or Path(read("gitdir")) != target / ".git"
        ):
            fail(
                "SELF_BUILD_GIT_REGISTRATION_INVALID",
                "Linked worktree native metadata does not match owned intent.",
            )
        raw = self.invoke(policy, "registrations", plan, target)
        records, current = [], {}
        for field in raw.split(b"\0"):
            if not field:
                if current:
                    records.append(current)
                    current = {}
                continue
            key, separator, value = field.partition(b" ")
            if key in current:
                fail(
                    "SELF_BUILD_GIT_REGISTRATION_INVALID", "Ambiguous linked worktree registration."
                )
            current[key] = value if separator else b""
        if current:
            records.append(current)
        matches = [
            item
            for item in records
            if b"worktree" in item
            and os.path.normcase(os.path.normpath(os.fsdecode(item[b"worktree"])))
            == os.path.normcase(str(target))
        ]
        if (
            len(matches) != 1
            or matches[0].get(b"HEAD") != plan.workspace.base_sha.encode()
            or matches[0].get(b"branch") != ("refs/heads/" + plan.workspace.branch).encode()
            or b"prunable" in matches[0]
        ):
            fail(
                "SELF_BUILD_GIT_REGISTRATION_INVALID",
                "Git does not acknowledge the exact generated branch and base.",
            )
        self.authority_check()
        return dict(
            workspace_id=plan.workspace_id,
            base_sha=plan.workspace.base_sha,
            branch=plan.workspace.branch,
            registration_digest=digest(
                [plan.workspace_id, plan.workspace.base_sha, plan.workspace.branch]
            ),
        )

    def create(self, policy, plan, owner):
        if not supports_mutation():
            fail(
                "SELF_BUILD_CHECKOUT_PLATFORM_UNAVAILABLE",
                "Native mutation requires Windows deny-replacement directory handles; this platform remains read-only.",
            )
        policy = policy.checked()
        if (
            digest(plan.model_dump(mode="json", exclude={"plan_hash"})) != plan.plan_hash
            or owner.get("planHash") != plan.plan_hash
            or policy.repository_identity != plan.workspace.repository_identity
            or digest(policy.model_dump()) != plan.workspace.policy_digest
            or plan.tool_sha256 != self.observer.executable_hash
            or plan.tool_identity_digest
            != digest([str(self.observer.executable), self.observer.executable_hash])
        ):
            fail(
                "SELF_BUILD_PLAN_CHANGED",
                "Native registration requires the exact prepared policy, tool and intent.",
            )
        self.authority_check()
        observed = self.observer.inspect(policy, plan.workspace.base_sha)
        if (
            observed["base_tree_sha"] != plan.base_tree_sha
            or observed["inventory_digest"] != plan.inventory_digest
            or observed["file_count"] != plan.file_count
        ):
            fail(
                "SELF_BUILD_REINSPECTION_REQUIRED",
                "Native registration requires the approved measured inventory.",
            )
        with owned_namespace(
            policy, plan.workspace_id, owner, authority_check=self.authority_check
        ) as (stack, root, target, owner_digest):
            # Hold both primary namespace identities through the mutation. The
            # workspace root and target are already pinned by owned_namespace.
            primary_path = Path(policy.primary_root)
            primary = open_directory(stack, Path(primary_path.anchor), internal=True)
            for leaf in primary_path.parts[1:]:
                primary = open_directory(stack, primary.path / leaf, primary, leaf, internal=True)
            common = open_directory(stack, primary.path / ".git", primary, ".git", internal=True)
            try:
                os.mkdir(common.name("worktrees"), 0o700, **common.kwargs)
            except FileExistsError:
                pass
            registrations = open_directory(
                stack, common.path / "worktrees", common, "worktrees", internal=True
            )
            try:
                os.stat(root.name(target.name), follow_symlinks=False, **root.kwargs)
            except FileNotFoundError:
                self.authority_check()
                try:
                    os.stat(
                        registrations.name(plan.workspace_id),
                        follow_symlinks=False,
                        **registrations.kwargs,
                    )
                except FileNotFoundError:
                    pass
                else:
                    fail(
                        "SELF_BUILD_GIT_CREATION_UNCERTAIN",
                        "An existing generated registration requires explicit recovery.",
                    )
                try:
                    os.mkdir(root.name(target.name), 0o700, **root.kwargs)
                except FileExistsError:
                    fail(
                        "SELF_BUILD_OWNERSHIP_CONFLICT",
                        "The target appeared before exclusive creation.",
                    )
                open_directory(stack, target, root, target.name, internal=True)
                self.invoke(policy, "create", plan, target)
            result = self.verify(stack, root, target, policy, plan, common)
            result["ownership_digest"] = owner_digest
            return result
