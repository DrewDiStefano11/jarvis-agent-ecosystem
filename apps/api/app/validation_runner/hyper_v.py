"""Fixed native Hyper-V observation/stop/unregister transport, no guest execution.

VM creation remains an explicit operator procedure. No adapter is registered in
the app. A VM/resource observation cannot produce a passing command receipt.
"""

import json
import os
import re
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from app.core.errors import DomainError
from app.models.hyper_v_control import HyperVBinding, HyperVObservation
from app.self_build.git_image import pinned_image
from app.self_build.git_process import limited_process


def refuse(code):
    raise DomainError(code, "Native Hyper-V control failed a required safety check.", 409) from None


class NativeHyperVTransport:
    def __init__(
        self,
        *,
        executable,
        executable_digest,
        script,
        script_digest,
        system_root,
        authority_check,
        media_authority_check=None,
    ):
        # Operator-deployed, pinned runtime files outside candidate authority.
        self.executable = Path(executable)
        self.script = Path(script)
        self.system_root = Path(system_root)
        if not all(path.is_absolute() for path in (self.executable, self.script, self.system_root)):
            refuse("HYPERV_NATIVE_CONFIGURATION_INVALID")
        expected = self.system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
        if self.executable != expected or self.script.suffix.casefold() != ".ps1":
            refuse("HYPERV_NATIVE_CONFIGURATION_INVALID")
        self.executable_digest = executable_digest
        self.script_digest = script_digest
        self.guard = authority_check
        self.media_guard = media_authority_check

    def invoke(self, binding, operation, *, media=None):
        binding = HyperVBinding.model_validate(binding.model_dump(mode="json"))
        if operation not in {
            "inspect",
            "stop",
            "unregister",
            "absence",
            "attach_media",
            "detach_media",
        }:
            refuse("HYPERV_OPERATION_UNAVAILABLE")
        if os.name != "nt":
            refuse("HYPERV_PLATFORM_UNAVAILABLE")
        self.guard(binding, operation)
        media_effect = operation in {"attach_media", "detach_media"}
        if media_effect:
            if (
                self.media_guard is None
                or type(media) is not tuple
                or len(media) != 3
                or not isinstance(media[0], Path)
                or not media[0].is_absolute()
                or str(media[0]).startswith("\\\\")
                or media[0].name != "source.iso"
                or type(media[1]) is not str
                or not re.fullmatch("[a-f0-9]{64}", media[1])
                or type(media[2]) is not int
                or not 1 <= media[2] <= 140000000
            ):
                refuse("HYPERV_MEDIA_AUTHORITY_REQUIRED")
            self.media_guard(binding, operation, media)
        elif media is not None:
            refuse("HYPERV_OPERATION_UNAVAILABLE")
        argv = [
            str(self.executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(self.script),
            "-Operation",
            operation,
            "-VmId",
            str(binding.vm_id),
            "-OwnershipDigest",
            binding.ownership_digest,
            "-HostMachineGuid",
            str(binding.host_machine_guid),
        ]
        if media_effect:
            argv.extend(
                [
                    "-MediaPath",
                    str(media[0]),
                    "-MediaDigest",
                    media[1],
                    "-MediaBytes",
                    str(media[2]),
                ]
            )
        # No inherited authentication, arbitrary PATH, profile or module search.
        env = {
            "SystemRoot": str(self.system_root),
            "WINDIR": str(self.system_root),
            "PATH": str(self.system_root / "System32"),
            "PSModulePath": str(self.system_root / "System32/WindowsPowerShell/v1.0/Modules"),
        }
        chunks, count, error = [], [0], Event()
        lock = Lock()

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

        try:
            # Both image and script (including ancestors) stay pinned through exit.
            with (
                pinned_image(self.script, self.script_digest),
                pinned_image(self.executable, self.executable_digest) as launch,
            ):

                def guard():
                    self.guard(binding, operation)
                    if media_effect:
                        self.media_guard(binding, operation, media)

                with limited_process(
                    argv,
                    cwd=str(self.system_root / "System32"),
                    env=env,
                    authority_check=guard,
                    launch=launch,
                ) as process:
                    threads = [
                        Thread(target=drain, args=(pipe, retain), daemon=True)
                        for pipe, retain in ((process.stdout, True), (process.stderr, False))
                    ]
                    for thread in threads:
                        thread.start()
                    deadline = monotonic() + 30
                    while process.poll() is None:
                        guard()
                        if error.is_set() or monotonic() > deadline:
                            refuse("HYPERV_EFFECT_UNCERTAIN")
                        error.wait(0.1)
                    for thread in threads:
                        thread.join(2)
                    if process.returncode or error.is_set() or any(t.is_alive() for t in threads):
                        refuse("HYPERV_EFFECT_UNCERTAIN")
                    guard()
                    result = json.loads(b"".join(chunks))
                    if not isinstance(result, dict):
                        raise ValueError("invalid native observation")
                    return result
        except DomainError:
            raise
        except (OSError, ValueError, TypeError):
            refuse("HYPERV_EFFECT_UNCERTAIN")


class NativeHyperVAdapter:
    def __init__(self, transport, *, authority_check):
        self.transport = transport
        self.guard = authority_check

    def inspect(self, binding):
        binding = HyperVBinding.model_validate(binding.model_dump(mode="json"))
        self.guard(binding, "inspect")
        payload = self.transport.invoke(binding, "inspect")
        if not isinstance(payload, dict):
            refuse("HYPERV_OBSERVATION_INVALID")
        if (
            str(payload.get("vmId")) != str(binding.vm_id)
            or payload.get("ownershipDigest") != binding.ownership_digest
            or str(payload.get("hostMachineGuid")) != str(binding.host_machine_guid)
        ):
            refuse("HYPERV_OWNER_MISMATCH")
        try:
            result = HyperVObservation(
                binding=binding,
                observed_at=payload["observedAt"],
                state=payload["state"],
                memory_assigned_bytes=payload["memoryAssignedBytes"],
                memory_startup_bytes=payload["memoryStartupBytes"],
                cpu_count=payload["cpuCount"],
                host_vm_uptime_seconds=payload["hostVmUptimeSeconds"],
                host_cpu_usage_percent=payload["hostCpuUsagePercent"],
                connected_network_adapters=payload["connectedNetworkAdapters"],
                checkpoint_count=payload["checkpointCount"],
                dynamic_memory=payload["dynamicMemory"],
                automatic_start=payload["automaticStart"],
            )
        except (ValueError, KeyError, TypeError):
            refuse("HYPERV_OBSERVATION_INVALID")
        self.guard(binding, "inspect")
        return result

    def stop(self, binding):
        binding = HyperVBinding.model_validate(binding.model_dump(mode="json"))
        self.guard(binding, "stop")
        self.transport.invoke(binding, "stop")
        result = self.inspect(binding)
        if result.state != "off":
            refuse("HYPERV_EFFECT_UNCERTAIN")
        return result

    def unregister(self, binding):
        # Never remove a running VM or any host disk/directory. Remaining storage
        # cleanup must be independently reconciled before claiming destruction.
        binding = HyperVBinding.model_validate(binding.model_dump(mode="json"))
        current = self.inspect(binding)
        if current.state != "off":
            refuse("HYPERV_STOP_REQUIRED")
        self.guard(binding, "unregister")
        result = self.transport.invoke(binding, "unregister")
        self.guard(binding, "unregister")
        if not isinstance(result, dict):
            refuse("HYPERV_OBSERVATION_INVALID")
        if (
            result.get("absent") is not True
            or result.get("vmId") != str(binding.vm_id)
            or result.get("ownershipDigest") != binding.ownership_digest
            or result.get("hostMachineGuid") != str(binding.host_machine_guid)
        ):
            refuse("HYPERV_EFFECT_UNCERTAIN")
        return {
            "vm_unregistered": True,
            "storage_cleanup_complete": False,
            "execution_authorized": False,
        }

    def execute(self, *args, **kwargs):
        refuse("VALIDATION_EXECUTION_ISOLATION_UNAVAILABLE")

    def require_absent(self, binding):
        """Fresh successful native inventory lookup, never a replayed removal ack."""
        binding = HyperVBinding.model_validate(binding.model_dump(mode="json"))
        self.guard(binding, "absence")
        result = self.transport.invoke(binding, "absence")
        if not isinstance(result, dict) or (
            result.get("absent") is not True
            or result.get("vmId") != str(binding.vm_id)
            or result.get("ownershipDigest") != binding.ownership_digest
            or result.get("hostMachineGuid") != str(binding.host_machine_guid)
        ):
            refuse("HYPERV_EFFECT_UNCERTAIN")
        self.guard(binding, "absence")
