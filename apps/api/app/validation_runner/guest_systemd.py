"""Protected Linux Hyper-V guest service manager execution and kernel telemetry.

Candidate code runs as a dynamic UID in a separate root/mount/network namespace.
The root service reads systemd/cgroup observations independently of stdout. This
module is not deployed or registered and emits no authenticated host receipts.
It is never an alternative to the required Hyper-V VM/acceptance boundary.
"""

import os
import re
import select
import sys
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from app.core.errors import DomainError
from app.development_validation.planner import digest
from app.self_build.git_image import pinned_image
from app.self_build.git_process import limited_process
from app.tool_execution.filesystem import open_directory, open_file, read_bytes
from app.validation_runner.catalog import describe_invocations
from app.validation_runner.isolation import verify_capsule

UNIT = re.compile(r"jarvis-validation-[a-f0-9]{32}\.service")
SHOW_FIELDS = (
    "LoadState",
    "ActiveState",
    "SubState",
    "InvocationID",
    "ExecMainPID",
    "ExecMainCode",
    "ExecMainStatus",
    "ExecMainStartTimestampMonotonic",
    "ExecMainExitTimestampMonotonic",
    "ControlGroup",
    "Result",
    "DynamicUser",
    "NoNewPrivileges",
    "PrivateNetwork",
    "PrivateDevices",
    "ProtectControlGroups",
    "RootDirectory",
    "RemainAfterExit",
    "User",
)


def refuse():
    raise DomainError(
        "GUEST_MEASUREMENT_UNAVAILABLE", "Protected guest execution requires reconciliation.", 409
    ) from None


def guest_path(value):
    if type(value) is not str or not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", value):
        refuse()
    if any(part in {".", ".."} for part in value.split("/")):
        refuse()
    return value


class NativeSystemdControl:
    def __init__(
        self,
        *,
        systemd_run,
        systemd_run_digest,
        systemctl,
        systemctl_digest,
        authority_check,
        cleanup_authority_check,
        command_authority_check,
    ):
        self.programs = {
            "run": (Path(systemd_run), systemd_run_digest),
            "ctl": (Path(systemctl), systemctl_digest),
        }
        if any(not path.is_absolute() for path, _ in self.programs.values()):
            refuse()
        self.guard = authority_check
        self.cleanup_guard = cleanup_authority_check
        self.command_guard = command_authority_check

    def invoke(self, program, arguments, *, expected_return_codes=(0,), maintenance_unit=None):
        if sys.platform != "linux" or os.geteuid() != 0:
            refuse()
        if program == "ctl":
            if maintenance_unit is None and not (
                len(arguments) == 4
                and arguments[:2] == ["--no-pager", "show"]
                and UNIT.fullmatch(arguments[2])
                and arguments[3] == "--property=" + ",".join(SHOW_FIELDS)
            ):
                refuse()
        elif program != "run" or maintenance_unit is not None:
            refuse()
        guard = self.guard
        if maintenance_unit is not None:
            if (
                program != "ctl"
                or arguments != ["stop", maintenance_unit]
                or not UNIT.fullmatch(maintenance_unit)
            ):
                refuse()

            def guard():
                self.cleanup_guard(maintenance_unit)
        elif program == "run":

            def guard():
                self.guard()
                # Must compare the exact generated command against the original
                # authenticated host request; no model-selected root CLI exists.
                self.command_guard(tuple(arguments))

        guard()
        path, image_digest = self.programs[program]
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "SYSTEMD_PAGER": "cat",
            "SYSTEMD_COLORS": "0",
            "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/run/dbus/system_bus_socket",
        }
        chunks, count, error, lock = [], [0], Event(), Lock()

        def drain(pipe, retain):
            try:
                while chunk := pipe.read(4096):
                    with lock:
                        count[0] += len(chunk)
                        if count[0] > 65536:
                            error.set()
                            return
                        if retain:
                            chunks.append(chunk)
            except OSError:
                error.set()

        with pinned_image(path, image_digest) as launch:
            with limited_process(
                [str(path), *arguments], cwd="/", env=env, authority_check=guard, launch=launch
            ) as process:
                threads = [
                    Thread(target=drain, args=(pipe, retain), daemon=True)
                    for pipe, retain in ((process.stdout, True), (process.stderr, False))
                ]
                for thread in threads:
                    thread.start()
                deadline = monotonic() + 10
                while process.poll() is None:
                    guard()
                    if error.is_set() or monotonic() > deadline:
                        refuse()
                    error.wait(0.05)
                for thread in threads:
                    thread.join(1)
                if (
                    process.returncode not in expected_return_codes
                    or error.is_set()
                    or any(t.is_alive() for t in threads)
                ):
                    refuse()
                guard()
                return b"".join(chunks)

    def show(self, unit):
        if not UNIT.fullmatch(unit):
            refuse()
        raw = self.invoke(
            "ctl",
            ["--no-pager", "show", unit, "--property=" + ",".join(SHOW_FIELDS)],
            expected_return_codes=(0, 4),
        )
        fields = {}
        for line in raw.decode("utf-8", errors="strict").splitlines():
            name, separator, value = line.partition("=")
            if not separator or name not in SHOW_FIELDS or name in fields:
                refuse()
            fields[name] = value
        if fields.get("LoadState") == "not-found":
            return {"LoadState": "not-found"}
        if fields.keys() != set(SHOW_FIELDS):
            refuse()
        return fields

    def stop(self, unit):
        if not UNIT.fullmatch(unit):
            refuse()
        self.invoke("ctl", ["stop", unit], maintenance_unit=unit)


def unit_arguments(
    unit,
    invocation,
    *,
    executable,
    rootfs,
    source,
    stdout,
    stderr,
    memory_bytes,
    process_count,
    cpu_count,
    timeout_seconds,
    disk_bytes,
    output_paths=(),
    dependencies=(),
):
    """Fixed security properties; no request-selected property/command passthrough."""
    if not UNIT.fullmatch(unit):
        refuse()
    for path in (executable, rootfs, source, stdout, stderr):
        guest_path(path)
    if not (
        type(memory_bytes) is int
        and 1 <= memory_bytes <= 8589934592
        and type(process_count) is int
        and 1 <= process_count <= 256
        and type(cpu_count) is int
        and 1 <= cpu_count <= 4
        and type(timeout_seconds) is int
        and 1 <= timeout_seconds <= 3600
        and type(disk_bytes) is int
        and 1 <= disk_bytes <= 68719476736
    ):
        refuse()
    if len(output_paths) > 8 or len(dependencies) > 4:
        refuse()
    cwd = "/workspace" + (
        "" if invocation.working_directory == "." else "/" + invocation.working_directory
    )
    guest_path(cwd)
    writable = []
    for relative in output_paths:
        from app.development_validation.planner import ignored_output
        from app.models.development_validation import checked_path

        checked_path(relative)
        if relative not in {
            ".local",
            ".pytest_cache",
            "apps/api/.pytest_cache",
            "apps/web/dist",
            "apps/web/node_modules/.tmp",
        } or not ignored_output(relative + "/__jarvis_output__"):
            refuse()
        writable.append("/workspace/" + relative)
    readonly = [source + ":/workspace"]
    for source_path, destination in dependencies:
        guest_path(source_path)
        guest_path(destination)
        # Only the prebuilt dependency subtree may cover the approved source tree.
        if destination != "/workspace/apps/web/node_modules":
            refuse()
        readonly.append(source_path + ":" + destination)
    quota = max(1, disk_bytes // (len(writable) + 1))
    temporary = [f"{path}:rw,nosuid,nodev,mode=1777,size={quota}" for path in ("/tmp", *writable)]
    properties = {
        "User": unit.removesuffix(".service"),
        "DynamicUser": "yes",
        "NoNewPrivileges": "yes",
        "CapabilityBoundingSet": "",
        "AmbientCapabilities": "",
        "PrivateNetwork": "yes",
        "PrivateDevices": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "ProtectControlGroups": "yes",
        "ProtectKernelTunables": "yes",
        "ProtectKernelModules": "yes",
        "ProtectClock": "yes",
        "ProtectProc": "invisible",
        "ProcSubset": "pid",
        "RestrictSUIDSGID": "yes",
        "RestrictNamespaces": "yes",
        "SystemCallArchitectures": "native",
        "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
        "RootDirectory": rootfs,
        "BindReadOnlyPaths": " ".join(readonly),
        "TemporaryFileSystem": " ".join(temporary),
        "StandardInput": "null",
        "StandardOutput": "file:" + stdout,
        "StandardError": "file:" + stderr,
        "CPUAccounting": "yes",
        "MemoryAccounting": "yes",
        "TasksAccounting": "yes",
        "MemoryMax": str(memory_bytes),
        "TasksMax": str(process_count),
        "CPUQuota": str(cpu_count * 100) + "%",
        "RuntimeMaxSec": str(timeout_seconds),
        "Restart": "no",
        "KillMode": "control-group",
        "KillSignal": "SIGKILL",
        "TimeoutStopSec": "1",
    }
    args = [
        "--no-block",
        "--remain-after-exit",
        "--service-type=exec",
        "--expand-environment=no",
        "--unit=" + unit,
        "--working-directory=" + cwd,
    ]
    args += ["--property=" + key + "=" + value for key, value in properties.items()]
    env = {
        "PATH": "/opt/jarvis/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        **dict(invocation.environment),
    }
    args += ["--setenv=" + key + "=" + value for key, value in env.items()]
    # systemd expands percent specifiers separately from environment expansion.
    # Doubling them preserves literal catalog arguments containing percent signs.
    return [*args, "--", executable, *(value.replace("%", "%%") for value in invocation.arguments)]


def kernel_cgroup_metrics(unit, *, memory_bytes, process_count, cpu_count):
    """Read native cgroup-v2 state outside the candidate namespace, through FDs."""
    if not UNIT.fullmatch(unit):
        refuse()
    root = Path("/sys/fs/cgroup/system.slice") / unit
    with ExitStack() as stack:
        parent = None
        for path in (*reversed(root.parents), root):
            parent = open_directory(
                stack, path, parent=parent, leaf=path.name if parent else None, internal=True
            )

        def read(name):
            with open_file(parent, name, os.O_RDONLY, internal=True) as fd:
                return read_bytes(fd, 4096).decode("ascii", errors="strict").strip()

        def number(value):
            if not re.fullmatch(r"[0-9]{1,20}", value):
                refuse()
            return int(value)

        def pairs(value):
            result = {}
            for line in value.splitlines():
                name, separator, count = line.partition(" ")
                if not separator or name in result:
                    refuse()
                result[name] = number(count)
            return result

        actual_memory, actual_pids = number(read("memory.max")), number(read("pids.max"))
        quota, period = [number(value) for value in read("cpu.max").split()]
        if (
            actual_memory > memory_bytes
            or actual_pids > process_count
            or not period
            or quota > period * cpu_count
        ):
            refuse()
        cpu = pairs(read("cpu.stat"))
        events = pairs(read("cgroup.events"))
        memory_events = pairs(read("memory.events"))
        pid_events = pairs(read("pids.events"))
        return {
            "cpu_time_ms": number(str(cpu["usage_usec"])) // 1000,
            "peak_memory_bytes": number(read("memory.peak")),
            "populated": events["populated"],
            "oom_kill": memory_events["oom_kill"],
            "process_limit_events": pid_events["max"],
        }


class ProtectedGuestExecutor:
    def __init__(
        self,
        control,
        *,
        rootfs,
        source_root,
        spool_root,
        programs,
        limits,
        authority_check,
        source_check,
        claim_execution,
        output_paths=(),
        dependencies=(),
        reserve_guest_memory_bytes=1073741824,
    ):
        self.control = control
        self.rootfs, self.source, self.spool = map(Path, (rootfs, source_root, spool_root))
        self.programs, self.limits = programs, limits
        self.guard, self.source_check, self.claim = authority_check, source_check, claim_execution
        self.output_paths, self.dependencies = output_paths, dependencies
        from app.models.validation_isolation import IsolationLimits

        self.limits = IsolationLimits.model_validate(limits.model_dump(mode="json"))
        if (
            type(reserve_guest_memory_bytes) is not int
            or not 1 <= reserve_guest_memory_bytes < self.limits.memory_bytes
        ):
            refuse()
        self.command_memory = self.limits.memory_bytes - reserve_guest_memory_bytes

    def execute(self, plan, capsule, owner, command_run_id, gate):
        if sys.platform != "linux" or os.geteuid() != 0:
            refuse()
        # This guard must authenticate the real guest/boot/enrollment and current
        # host-issued command/lease. A guest-provided boolean is not sufficient.
        self.guard()
        roots = (self.rootfs, self.source, self.spool)
        for root in roots:
            guest_path(str(root))
        if any(a == b or a.is_relative_to(b) for a in roots for b in roots if a is not b):
            refuse()
        verify_capsule(
            capsule,
            plan,
            owner=owner,
            journal_digest=capsule.journal_digest,
            native_inventory_digest=capsule.inventory_digest,
        )
        groups = describe_invocations(plan)
        selected = next((item for item in groups if item.gate == gate), None)
        if selected is None or gate == "repository_integrity":
            refuse()  # Host-native source integrity needs its separate trusted gate.
        self.source_check(self.source, capsule, plan)
        self.claim(owner, command_run_id, selected)
        observations = []
        deadline = monotonic() + selected.command.timeout_seconds
        for index, invocation in enumerate(selected.invocations):
            self.guard()
            unit = (
                "jarvis-validation-"
                + digest(
                    [
                        owner.model_dump(mode="json"),
                        command_run_id,
                        selected.invocation_digest,
                        index,
                    ]
                )[:32]
                + ".service"
            )
            observations.append(self._run(unit, invocation, selected.command, deadline))
        self.source_check(self.source, capsule, plan)
        self.guard()
        # These are native observations only. The separately authenticated host
        # collector must verify provenance before creating any qualifying receipt.
        return {
            "capsule_digest": capsule.capsule_digest,
            "execution_plan_hash": plan.execution_plan_hash,
            "invocation_digest": selected.invocation_digest,
            "observations": tuple(observations),
            "outcome": "unmeasured",
            "execution_authorized": False,
        }

    def _run(self, unit, invocation, command, deadline):
        program = self.programs.get(invocation.program)
        if type(program) is not tuple or len(program) != 2:
            refuse()
        executable, executable_digest = program
        guest_path(executable)
        import pwd

        try:
            pwd.getpwnam(unit.removesuffix(".service"))
        except KeyError:
            pass
        else:
            refuse()  # A preexisting static user must never replace the dynamic UID.
        with ExitStack() as stack:
            physical = self.rootfs / executable.removeprefix("/")
            ancestor = None
            for path in (*reversed(physical.parent.parents), physical.parent):
                ancestor = open_directory(
                    stack,
                    path,
                    parent=ancestor,
                    leaf=path.name if ancestor else None,
                    internal=True,
                )
                info = os.stat(path, follow_symlinks=False)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    refuse()
            with open_file(ancestor, physical.name, os.O_RDONLY, internal=True) as fd:
                info = os.fstat(fd)
                if info.st_uid != 0 or info.st_mode & 0o022 or not info.st_mode & 0o001:
                    refuse()
                if sha256(read_bytes(fd, 67108864)).hexdigest() != executable_digest:
                    refuse()
            for path in (*reversed(self.spool.parents), self.spool):
                open_directory(stack, path, internal=True)
                info = os.stat(path, follow_symlinks=False)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    refuse()
            spool = self.spool / unit
            os.mkdir(spool, mode=0o700)  # Existing claim/artifacts never get reused.
            pipes, keepers, hashes, retained = [], [], [sha256(), sha256()], 0
            for name in ("stdout", "stderr"):
                path = spool / name
                os.mkfifo(path, mode=0o600)
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                stack.callback(os.close, fd)
                pipes.append(fd)
                keepers.append(os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW))

            def close_keeper(fd):
                if fd in keepers:
                    keepers.remove(fd)
                    os.close(fd)

            for fd in keepers:
                stack.callback(close_keeper, fd)
            remaining = int(deadline - monotonic())
            if remaining < 1:
                refuse()
            args = unit_arguments(
                unit,
                invocation,
                executable=executable,
                rootfs=str(self.rootfs),
                source=str(self.source),
                stdout=str(spool / "stdout"),
                stderr=str(spool / "stderr"),
                memory_bytes=min(command.memory_limit_bytes, self.command_memory),
                process_count=self.limits.process_count,
                cpu_count=self.limits.cpu_count,
                timeout_seconds=remaining,
                disk_bytes=self.limits.disk_bytes,
                output_paths=self.output_paths,
                dependencies=self.dependencies,
            )
            if self.control.show(unit)["LoadState"] != "not-found":
                refuse()
            launched = False
            try:
                self.guard()
                launched = True  # Even lost launch acknowledgments need native stop/reconciliation.
                self.control.invoke("run", args)
                eof = set()
                while True:
                    self.guard()
                    if monotonic() > deadline:
                        refuse()
                    ready, _, _ = select.select([fd for fd in pipes if fd not in eof], [], [], 0.05)
                    for fd in ready:
                        content = os.read(fd, 65536)
                        if not content:
                            eof.add(fd)
                        else:
                            retained += len(content)
                            if retained > command.output_limit_bytes:
                                refuse()
                            hashes[pipes.index(fd)].update(content)
                    data = self.control.show(unit)
                    if data["ExecMainExitTimestampMonotonic"] not in {"", "0"}:
                        metrics = kernel_cgroup_metrics(
                            unit,
                            memory_bytes=min(command.memory_limit_bytes, self.command_memory),
                            process_count=self.limits.process_count,
                            cpu_count=self.limits.cpu_count,
                        )
                        if metrics["populated"] != 0:
                            refuse()
                        if any(
                            data[key] != "yes"
                            for key in (
                                "DynamicUser",
                                "NoNewPrivileges",
                                "PrivateNetwork",
                                "PrivateDevices",
                                "ProtectControlGroups",
                                "RemainAfterExit",
                            )
                        ) or data["RootDirectory"] != str(self.rootfs):
                            refuse()
                        if data["User"] != unit.removesuffix(".service"):
                            refuse()
                        if (
                            not re.fullmatch("[a-f0-9]{32}", data["InvocationID"])
                            or not data["ExecMainPID"].isdigit()
                            or int(data["ExecMainPID"]) <= 0
                        ):
                            refuse()
                        # RemainAfterExit preserves manager and kernel counters;
                        # stop happens only after measuring. Never inspect stdout.
                        self.control.stop(unit)
                        launched = False
                        # Manager-held FIFO writers close when the unit is freed.
                        for keeper in tuple(keepers):
                            close_keeper(keeper)
                        break
                while len(eof) < len(pipes):
                    self.guard()
                    if monotonic() > deadline:
                        refuse()
                    ready, _, _ = select.select([fd for fd in pipes if fd not in eof], [], [], 0.05)
                    for fd in ready:
                        content = os.read(fd, 65536)
                        if not content:
                            eof.add(fd)
                        else:
                            retained += len(content)
                            if retained > command.output_limit_bytes:
                                refuse()
                            hashes[pipes.index(fd)].update(content)
                return {
                    "unit": unit,
                    "invocation_id": data["InvocationID"],
                    "pid": int(data["ExecMainPID"]),
                    "exit_code": int(data["ExecMainStatus"])
                    if data["ExecMainCode"] == "1"
                    else None,
                    "native_result": data["Result"],
                    **metrics,
                    "stdout_digest": hashes[0].hexdigest(),
                    "stderr_digest": hashes[1].hexdigest(),
                    "output_bytes": retained,
                }
            finally:
                if launched:
                    # Protected supervisor cleanup authority must survive execution
                    # revocation. If it cannot stop, the host must stop the VM and
                    # keep its capacity quarantined. No fabricated success returns.
                    self.control.stop(unit)
