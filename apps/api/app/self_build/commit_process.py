"""Private fixed Git commit commands; native journal/namespace fencing is mandatory.

This primitive is not a route, tool, worker, approval importer or execution grant.
The caller supplies a live guard that resolves the exact persisted execution
record, operator approval, runtime lease and pinned source/metadata namespace.
"""

import os
import re
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from app.core.errors import DomainError
from app.models.git_execution import NativeCommitRecord
from app.self_build.commit_objects import OBJECT_POLICY_DIGEST
from app.self_build.git_image import pinned_image
from app.self_build.owned_process import owned_process


def refuse(code):
    raise DomainError(
        code, "Native commit command failed its exact bounded fence; preserve artifacts.", 409
    )


class NativeCommitProcess:
    def __init__(self, observer, authority_check):
        if not callable(authority_check):
            raise ValueError("native execution and namespace guard required")
        self.observer = observer
        self.guard = authority_check

    def _validated(self, policy, record, program):
        if os.name != "nt":
            refuse("GIT_COMMIT_PLATFORM_UNAVAILABLE")
        record = NativeCommitRecord.model_validate(record.model_dump(mode="json"))
        if record.intent_checkpoint_id is None:
            refuse("GIT_COMMIT_INTENT_CHECKPOINT_REQUIRED")
        policy = policy.checked()
        plan = record.plan
        proposal = plan.preparation.plan.proposal
        if (
            program.fingerprint != plan.object_program_fingerprint
            or program.compiler_policy_digest != OBJECT_POLICY_DIGEST
            or plan.compiler_policy_digest != OBJECT_POLICY_DIGEST
            or program.preparation_plan_hash != proposal.plan_hash
            or program.observation_fingerprint != proposal.observation_fingerprint
            or program.tree_sha != proposal.prospective_tree_sha
            or program.commit_sha != proposal.prospective_commit_sha
            or len(program.objects) != plan.object_count
            or sum(len(item.payload) for item in program.objects) != plan.object_payload_bytes
            or sha256(program.index_payload).hexdigest() != plan.index_payload_sha256
            or len(program.index_payload) != plan.index_payload_bytes
            or self.observer.executable_hash != proposal.tool_sha256
            or policy.repository_identity != proposal.repository_identity
            or any(item.kind not in {"blob", "tree", "commit"} for item in program.objects)
        ):
            refuse("GIT_COMMIT_PROGRAM_CHANGED")
        return record, policy, proposal

    def update_reference(self, policy, record, program):
        record, policy, proposal = self._validated(policy, record, program)
        target = Path(policy.worktree_root) / proposal.workspace_id
        common = Path(policy.primary_root) / ".git"
        metadata = common / "worktrees" / proposal.workspace_id
        base = ["--git-dir=" + str(metadata), "--work-tree=" + str(target)]
        self.guard(record)
        if record.state != "reference_prepared":
            refuse("GIT_COMMIT_REFERENCE_INTENT_REQUIRED")
        command = base + [
            "update-ref",
            "--no-deref",
            "-m",
            record.operation_id,
            "refs/heads/" + proposal.branch,
            program.commit_sha,
            proposal.parent_sha,
        ]
        output = self._run(policy, record, command, common)
        if output:
            refuse("GIT_COMMIT_EFFECT_ACKNOWLEDGEMENT_INVALID")
        self.guard(record)
        return output

    def object_available(self, policy, record, program, object_index):
        """Read a fixed candidate through Git, including packed original objects.

        Caller retains the complete approved metadata fence. An existing SHA-1
        identity is reusable only with exact kind, size and strong payload bytes;
        this proves content, never inode ownership or permission to delete it.
        """
        record, policy, proposal = self._validated(policy, record, program)
        if type(object_index) is not int or not 0 <= object_index < len(program.objects):
            refuse("GIT_COMMIT_OBJECT_SELECTION_INVALID")
        common = Path(policy.primary_root) / ".git"
        target = Path(policy.worktree_root) / proposal.workspace_id
        base = [
            "--git-dir=" + str(common / "worktrees" / proposal.workspace_id),
            "--work-tree=" + str(target),
        ]
        raw = self._run(
            policy,
            record,
            base
            + [
                "cat-file",
                "--batch-all-objects",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            ],
            common,
            maximum_stdout=2097152,
        )
        available, previous = {}, ""
        for row in raw.splitlines():
            if len(available) >= 16384:
                refuse("GIT_COMMIT_OBJECT_CATALOGUE_LIMIT")
            match = re.fullmatch(
                rb"([a-f0-9]{40}) (blob|tree|commit|tag) (0|[1-9][0-9]{0,12})", row
            )
            if match is None:
                refuse("GIT_COMMIT_OBJECT_CATALOGUE_INVALID")
            sha, kind, size = match.groups()
            name = sha.decode("ascii")
            if name <= previous:
                refuse("GIT_COMMIT_OBJECT_CATALOGUE_INVALID")
            previous = name
            available[name] = (kind.decode("ascii"), int(size))
        item = program.objects[object_index]
        if item.sha not in available:
            self.guard(record)
            return False
        if available[item.sha] != (item.kind, len(item.payload)):
            refuse("GIT_COMMIT_EXISTING_OBJECT_CONFLICT")
        payload = self._run(
            policy,
            record,
            base + ["cat-file", item.kind, item.sha],
            common,
            maximum_stdout=max(1, len(item.payload)),
        )
        if payload != item.payload:
            refuse("GIT_COMMIT_EXISTING_OBJECT_CONFLICT")
        self.guard(record)
        return True

    def reference_value(self, policy, record, program):
        """Independently read only the generated owned branch; never HEAD text."""
        record, policy, proposal = self._validated(policy, record, program)
        common = Path(policy.primary_root) / ".git"
        target = Path(policy.worktree_root) / record.workspace_id
        output = self._run(
            policy,
            record,
            [
                "--git-dir=" + str(common / "worktrees" / record.workspace_id),
                "--work-tree=" + str(target),
                "rev-parse",
                "--verify",
                "--end-of-options",
                "refs/heads/" + proposal.branch,
            ],
            common,
            maximum_stdout=41,
        )
        if re.fullmatch(rb"[a-f0-9]{40}\n", output) is None:
            refuse("GIT_COMMIT_REFERENCE_OBSERVATION_INVALID")
        self.guard(record)
        return output[:40].decode("ascii")

    def _run(self, policy, record, command, common, *, maximum_stdout=65536):
        self.guard(record)
        proposal = record.plan.preparation.plan.proposal
        executable = self.observer._tool(policy)
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }
        committer = proposal.metadata.committer
        environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_NO_REPLACE_OBJECTS="1",
            GIT_TERMINAL_PROMPT="0",
            LC_ALL="C",
            GIT_COMMON_DIR=str(common),
            GIT_OBJECT_DIRECTORY=str(common / "objects"),
            GIT_COMMITTER_NAME=committer.name,
            GIT_COMMITTER_EMAIL=committer.email,
            GIT_COMMITTER_DATE=f"{committer.timestamp} {committer.timezone}",
        )
        argv = [
            executable,
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.sparseCheckout=false",
            "-c",
            "index.sparse=false",
            "-c",
            "core.splitIndex=false",
            "-c",
            "index.version=2",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "protocol.allow=never",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            *command,
        ]
        buffers = [bytearray(), bytearray()]
        lock, overflow, errors = Lock(), Event(), Event()
        last = [monotonic()]

        def drain(stream, index):
            try:
                while chunk := stream.read1(8192):
                    with lock:
                        last[0] = monotonic()
                        if len(buffers[index]) + len(chunk) > (
                            maximum_stdout if index == 0 else 65536
                        ):
                            overflow.set()
                        elif not overflow.is_set():
                            buffers[index].extend(chunk)
            except (OSError, ValueError):
                errors.set()

        threads = []
        try:
            with pinned_image(executable, proposal.tool_sha256) as launch:
                with owned_process(
                    argv,
                    cwd=policy.primary_root,
                    env=environment,
                    authority_check=lambda: self.guard(record),
                    launch=launch,
                ) as process:
                    threads = [
                        Thread(target=drain, args=(process.stdout, 0), daemon=True),
                        Thread(target=drain, args=(process.stderr, 1), daemon=True),
                    ]
                    for thread in threads:
                        thread.start()
                    checked = monotonic()
                    while process.poll() is None or any(thread.is_alive() for thread in threads):
                        if monotonic() - checked >= 0.2:
                            self.guard(record)
                            checked = monotonic()
                        if overflow.is_set() or errors.is_set():
                            refuse("GIT_COMMIT_OUTPUT_INVALID")
                        if monotonic() - last[0] > self.observer.inactivity_seconds:
                            refuse("GIT_COMMIT_PROCESS_INACTIVE")
                        for thread in threads:
                            thread.join(0.02)
                    if process.returncode != 0 or overflow.is_set() or errors.is_set():
                        refuse("GIT_COMMIT_EFFECT_UNCERTAIN")
        except OSError:
            refuse("GIT_COMMIT_PROCESS_UNAVAILABLE")
        finally:
            for thread in threads:
                if thread.ident is not None:
                    thread.join(1)
        return bytes(buffers[0])
