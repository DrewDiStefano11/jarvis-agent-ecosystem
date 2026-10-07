"""Read-only native Git observation. No user/model command or executable arguments."""

import os
import re
import stat
import subprocess
from contextlib import ExitStack
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import urlsplit

from app.core.errors import DomainError
from app.self_build.git_image import pinned_image
from app.self_build.policy import digest
from app.tool_execution.filesystem import check_stat, open_directory, repository_parts

COMMIT = re.compile(r"[a-f0-9]{40}\Z")
MAX_OUTPUT = 1024 * 1024
MAX_FILES = 4096


def fail(code, message):
    raise DomainError(code, message, 409)


def _remote_identity(value):
    """Discard credential-bearing/ambiguous remotes before emitting any metadata."""
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        remote = urlsplit(value)
        if (
            remote.scheme not in {"https", "ssh"}
            or remote.hostname != "github.com"
            or remote.password
            or remote.query
            or remote.fragment
            or remote.port is not None
            or remote.username not in ({None} if remote.scheme == "https" else {None, "git"})
        ):
            fail(
                "SELF_BUILD_GIT_REMOTE_INVALID",
                "A credential-free registered GitHub origin is required.",
            )
        path = remote.path.removeprefix("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}", path):
        fail("SELF_BUILD_GIT_REMOTE_INVALID", "A canonical GitHub repository origin is required.")
    return "github.com/" + path.lower()


def remote_identity(value):
    try:
        return _remote_identity(value)
    except (ValueError, UnicodeError):
        fail(
            "SELF_BUILD_GIT_REMOTE_INVALID",
            "A canonical credential-free GitHub origin is required.",
        )


def windows_implementation_path(path):
    """Supported real Git for Windows locations; cmd/bin launchers are unavailable."""
    return (
        path.name.casefold() == "git.exe"
        and path.parent.name.casefold() == "bin"
        and path.parent.parent.name.casefold() in {"mingw32", "mingw64"}
    )


class GitObserver:
    def __init__(self, executable, executable_hash, *, inactivity_seconds=30, authority_check=None):
        self.executable = Path(executable)
        self.executable_hash = executable_hash
        self.inactivity_seconds = inactivity_seconds
        self.authority_check = authority_check
        if (
            not self.executable.is_absolute()
            or self.executable.name.lower() not in {"git", "git.exe"}
            or not re.fullmatch(r"[a-f0-9]{64}", executable_hash)
            or (os.name == "nt" and not windows_implementation_path(self.executable))
        ):
            fail(
                "SELF_BUILD_GIT_TOOL_INVALID",
                "Configure an absolute pinned Git implementation; Windows requires mingw32/bin or mingw64/bin/git.exe.",
            )

    def _tool(self, policy):
        try:
            for parent in reversed(self.executable.parents):
                check_stat(parent.lstat(), directory=True, internal=True)
            info = self.executable.lstat()
            # Installed Git may have vendor-managed hard links. The trusted
            # external executable is pinned by content; repository files keep
            # the stricter single-link workspace rule.
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("unsafe executable")
            resolved = self.executable.resolve(strict=True)
            if any(
                resolved == Path(root) or Path(root) in resolved.parents
                for root in (policy.primary_root, policy.worktree_root)
            ):
                raise ValueError("repository executable")
            # Bytes are verified only through pinned_image's bounded non-following
            # descriptor. A preliminary pathname read could block on a swapped FIFO.
        except (OSError, ValueError, DomainError):
            fail(
                "SELF_BUILD_GIT_TOOL_INVALID",
                "The configured Git tool identity changed or is unsafe.",
            )
        return str(resolved)

    def _read(self, policy, operation, base_sha):
        if self.authority_check is not None:
            self.authority_check()
        alternate_state = self._alternates_state(policy)
        executable = self._tool(policy)
        with pinned_image(executable, self.executable_hash) as launch:
            result = self._read_image(policy, operation, base_sha, executable, launch)
        if self._alternates_state(policy) != alternate_state:
            fail("SELF_BUILD_GIT_STATE_CHANGED", "Object-store metadata changed during the read.")
        return result

    def _alternates_state(self, policy):
        # Check before and after every fixed read, including info directory change
        # times so a transient create/read/remove does not disappear from coherence.
        try:
            with ExitStack() as stack:
                path = Path(policy.primary_root) / ".git" / "objects" / "info"
                directory = open_directory(stack, path, internal=True)
                parent_info = os.fstat(directory.fd) if directory.fd is not None else path.lstat()
                parent_state = (
                    parent_info.st_dev,
                    parent_info.st_ino,
                    parent_info.st_mtime_ns,
                    parent_info.st_ctime_ns,
                )
                try:
                    info = os.stat(
                        directory.name("alternates"), follow_symlinks=False, **directory.kwargs
                    )
                except FileNotFoundError:
                    return parent_state, None
                check_stat(info, internal=True)
                if info.st_size:
                    fail(
                        "SELF_BUILD_GIT_METADATA_UNSAFE",
                        "External Git object stores are unavailable.",
                    )
                return parent_state, (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
        except OSError:
            fail(
                "SELF_BUILD_GIT_METADATA_INVALID", "Object-store metadata is unavailable or unsafe."
            )

    def _read_image(self, policy, operation, base_sha, executable, launch):
        # Fixed built-in read families; no shell, aliases, status hooks, filters or transport.
        commands = {
            "root": ["rev-parse", "--show-toplevel"],
            "common": ["rev-parse", "--git-common-dir"],
            "head": ["rev-parse", "--verify", "HEAD^{commit}"],
            "base": ["rev-parse", "--verify", base_sha + "^{commit}"],
            "tree": ["rev-parse", "--verify", base_sha + "^{tree}"],
            "origin_tip": [
                "rev-parse",
                "--verify",
                "refs/remotes/origin/" + policy.base_branch + "^{commit}",
            ],
            "remote": ["config", "--no-includes", "--local", "--get", "remote.origin.url"],
            "inventory": ["ls-tree", "-r", "--full-tree", "-z", base_sha],
        }
        if self.authority_check is not None:
            self.authority_check()
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
        argv = [
            executable,
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "protocol.allow=never",
            *commands[operation],
        ]
        try:
            process = subprocess.Popen(
                argv,
                cwd=policy.primary_root,
                env=environment,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                **launch,
            )
        except OSError:
            fail("SELF_BUILD_GIT_UNAVAILABLE", "The configured native Git tool could not start.")
        lock, overflow = Lock(), Event()
        buffers = [bytearray(), bytearray()]
        last_activity = [monotonic()]
        errors = Event()

        def drain(stream, index, limit):
            try:
                while chunk := stream.read1(8192):
                    with lock:
                        last_activity[0] = monotonic()
                        if len(buffers[index]) + len(chunk) > limit:
                            overflow.set()
                        elif not overflow.is_set():
                            buffers[index].extend(chunk)
            except OSError:
                errors.set()
            finally:
                stream.close()

        threads = [
            Thread(target=drain, args=(process.stdout, 0, MAX_OUTPUT), daemon=True),
            Thread(target=drain, args=(process.stderr, 1, 65536), daemon=True),
        ]
        for thread in threads:
            thread.start()
        reason = None
        last_authority_check = monotonic()
        try:
            while process.poll() is None or any(thread.is_alive() for thread in threads):
                if self.authority_check is not None and monotonic() - last_authority_check >= 0.5:
                    self.authority_check()
                    last_authority_check = monotonic()
                with lock:
                    inactive = monotonic() - last_activity[0] > self.inactivity_seconds
                if overflow.is_set() or errors.is_set() or inactive:
                    reason = (
                        "SELF_BUILD_GIT_OUTPUT_LIMIT"
                        if overflow.is_set()
                        else "SELF_BUILD_GIT_INACTIVE"
                        if inactive
                        else "SELF_BUILD_GIT_READ_FAILED"
                    )
                    process.kill()
                    break
                threads[0].join(0.02)
                threads[1].join(0.02)
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=1)
        if overflow.is_set():
            reason = "SELF_BUILD_GIT_OUTPUT_LIMIT"
        elif errors.is_set():
            reason = "SELF_BUILD_GIT_READ_FAILED"
        if reason:
            fail(reason, "Native Git observation stopped at its bounded read boundary.")
        if process.returncode != 0:
            fail("SELF_BUILD_GIT_READ_FAILED", "Required local repository metadata is unavailable.")
        return bytes(buffers[0])

    def inspect(self, policy, base_sha):
        policy = policy.checked()
        root = Path(policy.primary_root)
        pinned = []
        try:
            with ExitStack() as stack:
                for path in (root, root / ".git"):
                    open_directory(stack, path, internal=True)
                    info = path.stat()
                    pinned.append((path, info.st_dev, info.st_ino))
                count = 0

                def walk_error(error):
                    raise error

                for directory, directories, files in os.walk(
                    root / ".git", followlinks=False, onerror=walk_error
                ):
                    count += len(directories) + len(files)
                    if count > 16384:
                        fail(
                            "SELF_BUILD_GIT_METADATA_LIMIT",
                            "Git metadata exceeds the bounded inspection scope.",
                        )
                    parent = Path(directory)
                    for name in directories:
                        path = parent / name
                        open_directory(stack, path, internal=True)
                        info = path.stat()
                        pinned.append((path, info.st_dev, info.st_ino))
                    for name in files:
                        path = parent / name
                        check_stat(path.lstat(), internal=True)
                        if (
                            path == root / ".git" / "objects" / "info" / "alternates"
                            and path.stat().st_size
                        ):
                            fail(
                                "SELF_BUILD_GIT_METADATA_UNSAFE",
                                "External Git object stores are unavailable.",
                            )
                result = self._inspect(policy, base_sha)
                for path, device, inode in pinned:
                    info = path.lstat()
                    check_stat(info, directory=True, internal=True)
                    if (info.st_dev, info.st_ino) != (device, inode):
                        fail(
                            "SELF_BUILD_GIT_STATE_CHANGED",
                            "Repository metadata directories changed during observation.",
                        )
                return result
        except OSError:
            fail("SELF_BUILD_GIT_METADATA_INVALID", "Repository metadata is unavailable or unsafe.")

    def _inspect(self, policy, base_sha):
        if not COMMIT.fullmatch(base_sha):
            fail("SELF_BUILD_GIT_BASE_INVALID", "An exact local base commit is required.")
        policy = policy.checked()
        root = Path(policy.primary_root)
        try:
            check_stat((root / ".git").lstat(), directory=True, internal=True)
            observed_root = Path(
                self._read(policy, "root", base_sha).decode("utf-8").strip()
            ).resolve(strict=True)
            common = Path(self._read(policy, "common", base_sha).decode("utf-8").strip())
            common = (
                (root / common).resolve(strict=True)
                if not common.is_absolute()
                else common.resolve(strict=True)
            )
            if observed_root != root or common != root / ".git":
                fail(
                    "SELF_BUILD_GIT_ROOT_MISMATCH",
                    "The registered primary must be an ordinary repository root.",
                )
            identity = remote_identity(
                self._read(policy, "remote", base_sha).decode("utf-8").strip()
            )
            if identity != policy.repository_identity:
                fail(
                    "SELF_BUILD_GIT_IDENTITY_MISMATCH",
                    "Git origin differs from the registered repository.",
                )
            commits = {
                name: self._read(policy, name, base_sha).decode("ascii").strip()
                for name in ("head", "base", "tree", "origin_tip")
            }
            if (
                any(not COMMIT.fullmatch(value) for value in commits.values())
                or commits["base"] != base_sha
            ):
                fail("SELF_BUILD_GIT_BASE_INVALID", "Required Git objects are inconsistent.")
            raw = self._read(policy, "inventory", base_sha)
            entries, paths = [], set()
            for item in raw.split(b"\0"):
                if not item:
                    continue
                if len(entries) >= MAX_FILES:
                    fail(
                        "SELF_BUILD_GIT_TREE_LIMIT", "The base tree exceeds the bounded inventory."
                    )
                metadata, path_bytes = item.split(b"\t", 1)
                mode, kind, blob = metadata.decode("ascii").split(" ")
                path = path_bytes.decode("utf-8")
                repository_parts(path)
                if (
                    path.casefold() in paths
                    or mode not in {"100644", "100755"}
                    or kind != "blob"
                    or not COMMIT.fullmatch(blob)
                ):
                    fail(
                        "SELF_BUILD_GIT_TREE_UNSAFE",
                        "Base tree contains unsupported or ambiguous entries.",
                    )
                paths.add(path.casefold())
                entries.append([mode, blob, path])
            # Repeat volatile refs: never report a mixed observation as coherent.
            if (
                identity
                != remote_identity(self._read(policy, "remote", base_sha).decode("utf-8").strip())
                or commits["head"] != self._read(policy, "head", base_sha).decode("ascii").strip()
                or commits["origin_tip"]
                != self._read(policy, "origin_tip", base_sha).decode("ascii").strip()
            ):
                fail(
                    "SELF_BUILD_GIT_STATE_CHANGED",
                    "Repository references changed during observation.",
                )
            policy.checked()
            return dict(
                repository_identity=identity,
                base_sha=base_sha,
                base_tree_sha=commits["tree"],
                primary_head_sha=commits["head"],
                origin_tracking_sha=commits["origin_tip"],
                base_matches_origin_tracking_ref=commits["origin_tip"] == base_sha,
                file_count=len(entries),
                inventory_digest=digest(entries),
                tool_digest=self.executable_hash,
            )
        except (OSError, ValueError, UnicodeError):
            fail(
                "SELF_BUILD_GIT_METADATA_INVALID",
                "Required repository metadata is unsafe or malformed.",
            )
