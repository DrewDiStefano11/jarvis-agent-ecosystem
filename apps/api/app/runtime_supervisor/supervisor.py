from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

from app.runtime_supervisor.backup import BackupCancelled, BackupError, create_backup, last_backup
from app.runtime_supervisor.config import SupervisorConfig
from app.runtime_supervisor.frontend_build import validate_frontend_build
from app.runtime_supervisor.health import HealthResult, probe_http
from app.runtime_supervisor.io import (
    atomic_write_json,
    ensure_supervisor_homes,
    read_json,
    utc_now,
)
from app.runtime_supervisor.logging_utils import (
    child_output_logger,
    component_logger,
    configure_logging,
)
from app.runtime_supervisor.ownership import SingletonLock, process_identity
from app.runtime_supervisor.windows_console import ensure_hidden_console
from app.runtime_supervisor.windows_job import WindowsJob


@dataclass(frozen=True)
class ProcessDefinition:
    name: str
    argv: tuple[str, ...]
    cwd: Path
    enabled: bool
    required: bool
    health_url: str | None = None
    health_json: bool = False


@dataclass
class ManagedProcess:
    definition: ProcessDefinition
    process: subprocess.Popen[str] | None = None
    process_identity: str | None = None
    state: str = "disabled"
    health: str = "disabled"
    health_detail: str | None = None
    restart_count: int = 0
    consecutive_failures: int = 0
    health_failures: int = 0
    next_restart_at: float = 0
    started_at: float | None = None
    started_at_utc: str | None = None
    last_failure: str | None = None
    last_exit_code: int | None = None
    termination_failed: bool = False
    reader_threads: list[threading.Thread] = field(default_factory=list)
    failure_history: list[str] = field(default_factory=list)


ProcessFactory = Callable[..., subprocess.Popen[str]]
Clock = Callable[[], float]
Probe = Callable[..., HealthResult]
FORCED_TERMINATION_SECONDS = 5
SHUTDOWN_CONFIRMATION_OVERHEAD_SECONDS = 10


def _git_sha(repository: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_process_registry(config: SupervisorConfig) -> list[ProcessDefinition]:
    vite = config.web_directory / "node_modules" / "vite" / "bin" / "vite.js"
    node = str(config.node_executable) if config.node_executable else "node"
    return [
        ProcessDefinition(
            name="api",
            argv=(
                str(config.python_executable),
                "-m",
                "app.runtime_supervisor.api_child",
                "--host",
                config.api_host,
                "--port",
                str(config.api_port),
            ),
            cwd=config.api_directory,
            enabled=True,
            required=True,
            health_url=f"{config.api_url}/api/health",
            health_json=True,
        ),
        ProcessDefinition(
            name="web",
            argv=(
                node,
                str(vite),
                "preview",
                "--host",
                config.web_host,
                "--port",
                str(config.web_port),
                "--strictPort",
            ),
            cwd=config.web_directory,
            enabled=True,
            required=True,
            health_url=f"{config.web_url}/",
        ),
        ProcessDefinition(
            name="autonomous_worker",
            argv=(str(config.python_executable), "-m", "app.autonomous_worker"),
            cwd=config.api_directory,
            enabled=config.worker_enabled,
            required=False,
        ),
    ]


def shutdown_wait_seconds(config: SupervisorConfig) -> float:
    enabled_processes = sum(definition.enabled for definition in build_process_registry(config))
    per_process = config.graceful_shutdown_seconds + FORCED_TERMINATION_SECONDS
    return (
        config.health_interval_seconds
        + enabled_processes * per_process
        + SHUTDOWN_CONFIRMATION_OVERHEAD_SECONDS
    )


class RuntimeSupervisor:
    def __init__(
        self,
        config: SupervisorConfig,
        *,
        process_factory: ProcessFactory = subprocess.Popen,
        clock: Clock = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        probe: Probe = probe_http,
    ) -> None:
        self.config = config
        self.process_factory = process_factory
        self.clock = clock
        self.sleep = sleep
        self.probe = probe
        self.instance_id = str(uuid.uuid4())
        self.started_at = utc_now()
        self.started_monotonic = clock()
        self.git_sha = _git_sha(config.repository)
        self.stop_requested = False
        self.stopping = False
        prior_state = read_json(config.state_path)
        prior_shutdown = prior_state.get("lastCleanSupervisorShutdown") if prior_state else None
        self.last_clean_shutdown = prior_shutdown if isinstance(prior_shutdown, str) else None
        self.last_api_health: HealthResult | None = None
        self.last_system_status: dict[str, object] | None = None
        self.ollama_health = HealthResult(True, "not_required")
        self.processes = {
            definition.name: ManagedProcess(
                definition,
                state="not_running" if definition.enabled else "disabled",
                health="unknown" if definition.enabled else "disabled",
            )
            for definition in build_process_registry(config)
        }
        self.logger: logging.Logger | None = None
        self.job: WindowsJob | None = None
        self.last_backup_attempt: float | None = None
        self.last_backup_failure: str | None = None
        self.backup_thread: threading.Thread | None = None
        self.backup_cancel = threading.Event()
        self.backup_result: queue.SimpleQueue[Exception | None] = queue.SimpleQueue()
        secret_name = re.compile(r"(SECRET|TOKEN|PASSWORD|API_KEY|CREDENTIAL)", re.IGNORECASE)
        self.secret_values = tuple(
            sorted(
                {
                    value
                    for key, value in config.environment.items()
                    if secret_name.search(key) and len(value) >= 4
                },
                key=lambda value: (-len(value), value),
            )
        )

    def run(self) -> int:
        validate_frontend_build(self.config)
        ensure_supervisor_homes(
            self.config.runtime_home,
            self.config.coordination_home,
            self.config.repository,
        )
        self.config.logs_directory.mkdir(parents=True, exist_ok=True)
        self.config.backups_directory.mkdir(parents=True, exist_ok=True)
        self.logger = configure_logging(
            self.config.logs_directory,
            self.config.log_max_bytes,
            self.config.log_backup_count,
        )
        log = component_logger(self.logger, "supervisor")
        lock = SingletonLock(self.config.lock_path)
        if not lock.acquire():
            log.error("startup refused: another supervisor owns this installation")
            return 2
        supervisor_failure: str | None = None
        try:
            ensure_hidden_console()
            self.job = WindowsJob()
            self._install_signal_handlers()
            self.config.stop_request_path.unlink(missing_ok=True)
            log.info("starting instance=%s git_sha=%s", self.instance_id, self.git_sha or "unknown")
            self._write_state("starting")
            self._probe_dependencies()
            self._start_ordered()
            while not self.stop_requested:
                self._read_stop_request()
                if self.stop_requested:
                    break
                self._monitor_once()
                self._periodic_backup()
                self._write_state(self._aggregate_state())
                self.sleep(self.config.health_interval_seconds)
            return 0
        except BaseException as exc:
            log.exception("supervisor failure: %s", exc)
            supervisor_failure = str(exc)[:500]
            return 1
        finally:
            self.stopping = True
            self.backup_cancel.set()
            self._shutdown_all()
            self._drain_periodic_backup()
            self._refresh_application_shutdown_metadata()
            if supervisor_failure is None:
                self.last_clean_shutdown = utc_now()
                self._write_state("stopped")
            else:
                self._write_state("failed", supervisor_failure=supervisor_failure)
            if self.job is not None:
                self.job.close()
            lock.release()
            if supervisor_failure is None:
                log.info("supervisor stopped cleanly")
            else:
                log.error("supervisor stopped after failure")

    def _install_signal_handlers(self) -> None:
        def request_stop(_signum: int, _frame: object) -> None:
            self.stop_requested = True

        for item in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(item, request_stop)
            except (OSError, ValueError):
                pass

    def _probe_dependencies(self) -> None:
        if self.config.ollama_relevant:
            self.ollama_health = self.probe(self.config.ollama_url, timeout=2)
        else:
            self.ollama_health = HealthResult(True, "not_required")

    def _start_ordered(self) -> None:
        self._start_process(self.processes["api"])
        if not self._wait_available(self.processes["api"]):
            return
        self._start_process(self.processes["web"])
        if not self._wait_available(self.processes["web"]):
            return
        worker = self.processes["autonomous_worker"]
        if worker.definition.enabled:
            self._start_process(worker)
        self._monitor_once()

    def _wait_available(self, managed: ManagedProcess) -> bool:
        deadline = self.clock() + self.config.startup_timeout_seconds
        while self.clock() < deadline and not self.stop_requested:
            self._read_stop_request()
            if self.stop_requested:
                return False
            process = managed.process
            if process is None:
                return False
            if process.poll() is not None:
                managed.last_exit_code = process.poll()
                managed.process = None
                self._record_failure(managed, "process exited during startup")
                return False
            health = self._probe_managed(managed)
            if health.available:
                managed.health = health.status
                managed.health_detail = health.detail
                managed.state = "running"
                return True
            self.sleep(min(0.25, self.config.health_interval_seconds))
        if self.stop_requested:
            return False
        if self._terminate(managed, reason="health startup timeout"):
            managed.process = None
        self._record_failure(managed, "health startup timeout")
        return False

    def _dependencies_ready(self, name: str) -> bool:
        api = self.processes["api"]
        api_ready = (
            api.process is not None
            and api.process.poll() is None
            and api.health in {"healthy", "degraded"}
        )
        if name == "web":
            return api_ready
        if name == "autonomous_worker":
            web = self.processes["web"]
            return (
                api_ready
                and web.process is not None
                and web.process.poll() is None
                and web.health == "healthy"
            )
        return True

    def _start_process(self, managed: ManagedProcess) -> None:
        if not managed.definition.enabled or self.stopping:
            return
        now = self.clock()
        if now < managed.next_restart_at:
            return
        log = component_logger(self.logger, managed.definition.name)  # type: ignore[arg-type]
        environment = dict(self.config.environment)
        environment.setdefault("PYTHONUTF8", "1")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = self.process_factory(
                list(managed.definition.argv),
                cwd=managed.definition.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                creationflags=creationflags,
            )
            managed.process = process
            managed.process_identity = process_identity(process.pid)
            if self.job is not None and os.name == "nt":
                try:
                    self.job.assign(process._handle)  # type: ignore[attr-defined]
                except OSError as exc:
                    reason = f"could not assign Windows Job Object: {exc}"
                    log.error("%s", reason)
                    if self._terminate(managed, reason=reason):
                        managed.process = None
                    self._record_failure(managed, f"start failed: {reason}")
                    return
            managed.state = "running"
            managed.health = "starting"
            managed.termination_failed = False
            managed.health_failures = 0
            managed.started_at = now
            managed.started_at_utc = utc_now()
            managed.reader_threads = []
            child_output_logger(
                self.config.logs_directory,
                managed.definition.name,
                self.config.log_max_bytes,
                self.config.log_backup_count,
            )
            for label, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                if stream is not None:
                    thread = threading.Thread(
                        target=self._forward_output,
                        args=(managed.definition.name, label, stream),
                        daemon=True,
                    )
                    thread.start()
                    managed.reader_threads.append(thread)
            log.info("started pid=%s restart_count=%s", process.pid, managed.restart_count)
        except (OSError, ValueError) as exc:
            self._record_failure(managed, f"start failed: {exc}")

    def _forward_output(self, component: str, stream_name: str, stream: TextIO) -> None:
        log = child_output_logger(
            self.config.logs_directory,
            component,
            self.config.log_max_bytes,
            self.config.log_backup_count,
        )
        try:
            for line in stream:
                text = line.rstrip("\r\n")
                if text:
                    for secret in self.secret_values:
                        text = text.replace(secret, "[REDACTED]")
                    log.info("%s: %s", stream_name, text)
        finally:
            stream.close()

    def _probe_managed(self, managed: ManagedProcess) -> HealthResult:
        url = managed.definition.health_url
        if url is None:
            return HealthResult(managed.process is not None, "healthy")
        result = self.probe(url, expect_json=managed.definition.health_json, timeout=2)
        if managed.definition.name == "api":
            self.last_api_health = result
        return result

    def _monitor_once(self) -> None:
        now = self.clock()
        for managed in self.processes.values():
            if not managed.definition.enabled:
                continue
            process = managed.process
            if process is None:
                if self._dependencies_ready(managed.definition.name):
                    self._start_process(managed)
                continue
            exit_code = process.poll()
            if exit_code is not None:
                managed.last_exit_code = exit_code
                managed.process = None
                self._record_failure(managed, f"process exited with code {exit_code}")
                continue
            if managed.termination_failed:
                continue
            if (
                managed.started_at is not None
                and now - managed.started_at >= self.config.backoff_reset_seconds
            ):
                managed.consecutive_failures = 0
            health = self._probe_managed(managed)
            managed.health = health.status
            managed.health_detail = health.detail
            if health.available:
                managed.health_failures = 0
                managed.state = "running"
            else:
                managed.health_failures += 1
                if managed.health_failures >= self.config.health_failure_limit:
                    if self._terminate(managed, reason="health endpoint unavailable"):
                        managed.process = None
                    self._record_failure(managed, "health endpoint unavailable")
        self._load_system_status()
        self._probe_dependencies()
        self._record_known_good()

    def _record_failure(self, managed: ManagedProcess, reason: str) -> None:
        managed.state = "failed"
        managed.health = "failed"
        managed.last_failure = f"{utc_now()} {reason}"[:600]
        managed.failure_history.append(managed.last_failure)
        managed.failure_history = managed.failure_history[-20:]
        managed.restart_count += 1
        managed.consecutive_failures += 1
        exponent = min(managed.consecutive_failures - 1, 30)
        delay = min(
            self.config.restart_initial_seconds * (2**exponent),
            self.config.restart_maximum_seconds,
        )
        managed.next_restart_at = self.clock() + delay
        component_logger(self.logger, managed.definition.name).warning(  # type: ignore[arg-type]
            "%s; retry in %.1fs", reason, delay
        )

    def _terminate(self, managed: ManagedProcess, *, reason: str) -> bool:
        process = managed.process
        if process is None:
            return True
        if process.poll() is not None:
            managed.last_exit_code = process.poll()
            managed.termination_failed = False
            managed.state = "stopped"
            managed.health = "stopped"
            return True
        log = component_logger(self.logger, managed.definition.name)  # type: ignore[arg-type]
        log.info("graceful stop requested: %s", reason)
        try:
            if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=self.config.graceful_shutdown_seconds)
        except (subprocess.TimeoutExpired, OSError):
            log.warning("grace period expired; forcing termination")
            try:
                process.kill()
                process.wait(timeout=FORCED_TERMINATION_SECONDS)
            except (subprocess.TimeoutExpired, OSError):
                log.error("process did not exit after forced termination")
        managed.last_exit_code = process.poll()
        if managed.last_exit_code is None:
            managed.termination_failed = True
            managed.state = "failed"
            managed.health = "failed"
            return False
        managed.termination_failed = False
        managed.state = "stopped"
        managed.health = "stopped"
        return True

    def _shutdown_all(self) -> None:
        for name in ("autonomous_worker", "web", "api"):
            self._terminate(self.processes[name], reason="supervisor shutdown")

    def _refresh_application_shutdown_metadata(self) -> None:
        if not self.config.database_path.is_file():
            return
        database_uri = f"{self.config.database_path.as_uri()}?mode=ro"
        try:
            with closing(sqlite3.connect(database_uri, uri=True, timeout=5)) as connection:
                row = connection.execute(
                    "SELECT last_clean_shutdown FROM system_state WHERE id = 1"
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            component_logger(self.logger, "supervisor").warning(  # type: ignore[arg-type]
                "could not refresh application shutdown metadata: %s", exc
            )
            return
        if not row or not row[0]:
            return
        raw = str(row[0])
        try:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            value = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            value = raw
        status = dict(self.last_system_status or {})
        status["lastCleanShutdown"] = value
        self.last_system_status = status

    def _read_stop_request(self) -> None:
        request = read_json(self.config.stop_request_path)
        if request and request.get("instanceId") == self.instance_id:
            self.stop_requested = True
            component_logger(self.logger, "supervisor").info(  # type: ignore[arg-type]
                "authenticated operator stop request received"
            )

    def _load_system_status(self) -> None:
        result = self.probe(f"{self.config.api_url}/api/system/status", expect_json=True, timeout=2)
        if result.available and result.payload:
            self.last_system_status = result.payload
            worker = self.processes["autonomous_worker"]
            if worker.definition.enabled:
                value = result.payload.get("autonomousWorker")
                if isinstance(value, dict) and isinstance(value.get("status"), str):
                    worker.health = str(value["status"])

    def _record_known_good(self) -> None:
        required_healthy = all(
            item.process is not None and item.process.poll() is None and item.health == "healthy"
            for item in self.processes.values()
            if item.definition.required
        )
        worker = self.processes["autonomous_worker"]
        worker_ready = not worker.definition.enabled or (
            worker.process is not None
            and worker.process.poll() is None
            and worker.health == "healthy"
        )
        if not required_healthy or not worker_ready:
            return
        current = read_json(self.config.runtime_home / "known-good.json") or {}
        current.update(
            {
                "repository": str(self.config.repository),
                "currentGitSha": self.git_sha,
                "startupTimestamp": self.started_at,
                "successfulHealthTimestamp": utc_now(),
                "lastKnownHealthySha": self.git_sha,
            }
        )
        atomic_write_json(self.config.runtime_home / "known-good.json", current)

    def _periodic_backup(self) -> None:
        self._collect_periodic_backup()
        interval = self.config.backup_interval_hours
        if (
            interval == 0
            or self.stop_requested
            or (self.backup_thread is not None and self.backup_thread.is_alive())
            or (
                self.last_backup_attempt is not None
                and self.clock() - self.last_backup_attempt < 3600
            )
        ):
            return
        record = last_backup(self.config)
        due = True
        if record and isinstance(record.get("createdAt"), str):
            try:
                created = datetime.fromisoformat(str(record["createdAt"]).replace("Z", "+00:00"))
                due = datetime.now(UTC) - created >= timedelta(hours=interval)
            except ValueError:
                pass
        if not due:
            return
        self.last_backup_attempt = self.clock()
        self.backup_cancel.clear()
        self.backup_thread = threading.Thread(
            target=self._run_periodic_backup,
            name="jarvis-periodic-backup",
            daemon=False,
        )
        self.backup_thread.start()

    def _run_periodic_backup(self) -> None:
        try:
            create_backup(self.config, cancel_requested=self.backup_cancel.is_set)
            self.backup_result.put(None)
        except (BackupError, OSError, sqlite3.Error) as exc:
            self.backup_result.put(exc)

    def _drain_periodic_backup(self) -> None:
        thread = self.backup_thread
        if thread is not None:
            thread.join()
        self._collect_periodic_backup()

    def _collect_periodic_backup(self) -> None:
        thread = self.backup_thread
        if thread is None or thread.is_alive():
            return
        thread.join(timeout=0)
        self.backup_thread = None
        try:
            failure = self.backup_result.get_nowait()
        except queue.Empty:
            return
        if failure is None:
            self.last_backup_failure = None
            component_logger(self.logger, "backup").info("periodic SQLite backup completed")  # type: ignore[arg-type]
        elif isinstance(failure, BackupCancelled):
            component_logger(self.logger, "backup").info("periodic backup cancelled for shutdown")  # type: ignore[arg-type]
        else:
            self.last_backup_failure = f"{utc_now()} {failure}"[:600]
            component_logger(self.logger, "backup").error("periodic backup failed: %s", failure)  # type: ignore[arg-type]

    def _aggregate_state(self) -> str:
        enabled = [item for item in self.processes.values() if item.definition.enabled]
        if any(item.state == "failed" for item in enabled):
            return "degraded"
        if any(item.health not in {"healthy", "starting"} for item in enabled):
            return "degraded"
        if all(item.process is not None and item.process.poll() is None for item in enabled):
            return "running"
        return "starting"

    def _process_payload(self, managed: ManagedProcess) -> dict[str, object]:
        process = managed.process
        return {
            "configured": True,
            "enabled": managed.definition.enabled,
            "required": managed.definition.required,
            "processState": managed.state,
            "pid": process.pid if process is not None and process.poll() is None else None,
            "processIdentity": managed.process_identity if process is not None else None,
            "healthState": managed.health,
            "healthDetail": managed.health_detail,
            "restartCount": managed.restart_count,
            "consecutiveFailures": managed.consecutive_failures,
            "nextRestartInSeconds": max(0, managed.next_restart_at - self.clock()),
            "lastFailure": managed.last_failure,
            "failureHistory": managed.failure_history,
            "lastExitCode": managed.last_exit_code,
            "startedAt": managed.started_at_utc,
        }

    def _write_state(self, supervisor_state: str, **extra: object) -> None:
        try:
            disk = shutil.disk_usage(self.config.runtime_home)
        except OSError:
            disk = None
        emergency = None
        app_status = None
        last_app_shutdown = None
        if self.last_system_status:
            emergency = self.last_system_status.get("emergencyStop")
            app_status = self.last_system_status.get("status")
            last_app_shutdown = self.last_system_status.get("lastCleanShutdown")
        state: dict[str, object] = {
            "schemaVersion": 1,
            "supervisorState": supervisor_state,
            "pid": os.getpid(),
            "processIdentity": process_identity(os.getpid()),
            "instanceId": self.instance_id,
            "repository": str(self.config.repository),
            "gitSha": self.git_sha,
            "runtimeHome": str(self.config.runtime_home),
            "coordinationHome": str(self.config.coordination_home),
            "startedAt": self.started_at,
            "uptimeSeconds": max(0, self.clock() - self.started_monotonic),
            "shutdownWaitSeconds": shutdown_wait_seconds(self.config),
            "updatedAt": utc_now(),
            "processes": {
                name: self._process_payload(managed) for name, managed in self.processes.items()
            },
            "apiHealth": {
                "available": self.last_api_health.available if self.last_api_health else False,
                "status": self.last_api_health.status if self.last_api_health else "unknown",
                "applicationStatus": app_status,
            },
            "webHealth": {"status": self.processes["web"].health},
            "worker": {
                "enabled": self.config.worker_enabled,
                "status": self.processes["autonomous_worker"].health,
            },
            "ollama": {
                "required": self.config.ollama_relevant,
                "available": self.ollama_health.available,
                "status": self.ollama_health.status,
            },
            "emergencyStop": emergency,
            "lastApplicationCleanShutdown": last_app_shutdown,
            "lastCleanSupervisorShutdown": self.last_clean_shutdown,
            "backup": {
                "lastSuccess": last_backup(self.config),
                "lastFailure": self.last_backup_failure,
            },
            "disk": {
                "freeBytes": disk.free if disk else None,
                "warning": bool(disk and disk.free < self.config.disk_warning_bytes),
                "critical": bool(disk and disk.free < self.config.disk_critical_bytes),
            },
            "logsDirectory": str(self.config.logs_directory),
            "backupsDirectory": str(self.config.backups_directory),
            "knownGood": read_json(self.config.runtime_home / "known-good.json"),
        }
        state.update(extra)
        atomic_write_json(self.config.state_path, state)
