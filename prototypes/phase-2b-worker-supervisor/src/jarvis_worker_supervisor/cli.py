from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from jarvis_simulated_worker.scenarios import WorkerScenario

from .config import SupervisorConfig
from .crash_loop import reset_crash_loop
from .database import Database
from .emergency_stop import apply_emergency_stop
from .enums import TERMINAL_WORKER_STATES, SupervisorState
from .reporting import generate_status_report
from .shutdown import request_graceful_shutdown
from .supervisor import Supervisor
from .watchdog import Watchdog

LOGGER = logging.getLogger(__name__)


def init_db(runtime_dir: str, *, initialize: bool = False) -> Database:
    database = Database(Path(runtime_dir) / "state" / "supervisor.db")
    if initialize:
        database.initialize()
    else:
        database.verify_schema()
    return database


def _add_runtime_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-dir", required=True)


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    _add_runtime_argument(parser)
    parser.add_argument(
        "--scenario",
        choices=[scenario.value for scenario in WorkerScenario],
        default=WorkerScenario.HEALTHY.value,
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--maximum-restarts", type=int)


def _load_config(args: argparse.Namespace) -> SupervisorConfig:
    values: dict[str, Any] = {}
    if args.config:
        try:
            values = json.loads(args.config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to load configuration: {exc}") from exc
        if not isinstance(values, dict):
            raise ValueError("Configuration must be a JSON object.")
    values["runtime_dir"] = args.runtime_dir
    values["scenario"] = WorkerScenario(args.scenario)
    if args.maximum_restarts is not None:
        values["maximum_restarts"] = args.maximum_restarts
    try:
        return SupervisorConfig(**values)
    except TypeError as exc:
        raise ValueError(f"Unknown or missing configuration field: {exc}") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jarvis Worker Supervisor")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    _add_runtime_argument(commands.add_parser("init"))
    _add_run_arguments(commands.add_parser("start"))
    _add_run_arguments(commands.add_parser("run"))
    _add_runtime_argument(commands.add_parser("status"))
    _add_runtime_argument(commands.add_parser("stop"))
    _add_runtime_argument(commands.add_parser("pause"))
    _add_runtime_argument(commands.add_parser("resume"))
    _add_runtime_argument(commands.add_parser("emergency-stop"))
    _add_runtime_argument(commands.add_parser("emergency-resume"))
    _add_runtime_argument(commands.add_parser("reset-crash-loop"))

    simulate = commands.add_parser("simulate")
    _add_run_arguments(simulate)
    simulate.add_argument("--duration-seconds", type=float, default=10.0)

    report = commands.add_parser("report")
    _add_runtime_argument(report)
    report.add_argument("--format", choices=["text", "json"], default="text")
    report.add_argument("--output", type=Path)

    clean = commands.add_parser("clean")
    _add_runtime_argument(clean)
    clean.add_argument("--yes", action="store_true")
    return parser


def _command_state_change(
    database: Database,
    *,
    state: SupervisorState,
    event_type: str,
    **changes: object,
) -> None:
    current = database.get_supervisor_state("main")
    if not current:
        raise RuntimeError("Supervisor is not initialized.")
    database.update_supervisor("main", status=state, **changes)
    database.append_event(
        event_type,
        worker_instance_id=current["current_worker_instance_id"],
        previous_state=current["status"],
        new_state=state.value,
    )


def _run_watchdog(database: Database, config: SupervisorConfig) -> int:
    supervisor = Supervisor(database, config)
    Watchdog(supervisor).start()
    return 0 if supervisor.state["status"] != SupervisorState.FAILED.value else 1


def _simulate(database: Database, config: SupervisorConfig, duration: float) -> int:
    if duration <= 0:
        raise ValueError("duration-seconds must be positive.")
    supervisor = Supervisor(database, config)
    supervisor.prepare_startup()
    supervisor.recover()
    deadline = time.monotonic() + duration
    observed_worker = None
    while time.monotonic() < deadline:
        supervisor.tick()
        state = supervisor.state
        observed_worker = observed_worker or state["current_worker_instance_id"]
        if observed_worker:
            worker = database.get_worker_instance(observed_worker)
            if worker and worker["status"] in TERMINAL_WORKER_STATES:
                break
        time.sleep(min(0.05, config.watchdog_interval_seconds))
    supervisor.request_shutdown()
    shutdown_deadline = (
        time.monotonic() + config.graceful_shutdown_seconds + config.forced_shutdown_seconds + 1
    )
    while (
        supervisor.state["status"] != SupervisorState.STOPPED.value
        and time.monotonic() < shutdown_deadline
    ):
        supervisor.tick()
        time.sleep(min(0.05, config.watchdog_interval_seconds))
    supervisor.release_lease()
    return 0 if supervisor.state["status"] == SupervisorState.STOPPED.value else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime = Path(args.runtime_dir).expanduser().resolve()

    try:
        if args.command == "init":
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / "state").mkdir(exist_ok=True)
            (runtime / "logs").mkdir(exist_ok=True)
            database = init_db(str(runtime), initialize=True)
            database.ensure_supervisor("main")
            database.update_supervisor(
                "main",
                status=SupervisorState.STOPPED,
                stopped_at=time.time(),
            )
            print("Initialized runtime.", flush=True)
            return 0

        if not runtime.is_dir():
            raise RuntimeError("Runtime directory is not initialized.")
        database = init_db(str(runtime))

        if args.command in {"start", "run"}:
            return _run_watchdog(database, _load_config(args))
        if args.command == "simulate":
            return _simulate(
                database,
                _load_config(args),
                args.duration_seconds,
            )
        if args.command in {"status", "report"}:
            report = generate_status_report(database, "main")
            output = (
                json.dumps(report, indent=2, sort_keys=True)
                if args.command == "status" or args.format == "json"
                else _text_report(report)
            )
            if args.command == "report" and args.output:
                args.output.write_text(output, encoding="utf-8")
            else:
                print(output)
            return 0
        if args.command == "stop":
            current = database.get_supervisor_state("main")
            if not current:
                raise RuntimeError("Supervisor is not initialized.")
            _command_state_change(
                database,
                state=SupervisorState.STOPPING,
                event_type="supervisor.shutdown_requested",
            )
            if current["current_worker_instance_id"]:
                request_graceful_shutdown(str(runtime), current["current_worker_instance_id"])
            else:
                database.update_supervisor(
                    "main", status=SupervisorState.STOPPED, stopped_at=time.time()
                )
            return 0
        if args.command == "pause":
            _command_state_change(
                database,
                state=SupervisorState.PAUSED,
                event_type="supervisor.paused",
                paused=True,
            )
            return 0
        if args.command == "resume":
            current = database.get_supervisor_state("main")
            target = (
                SupervisorState.WAITING_FOR_READINESS
                if current and current["current_worker_instance_id"]
                else SupervisorState.IDLE
            )
            _command_state_change(
                database,
                state=target,
                event_type="supervisor.resumed",
                paused=False,
            )
            return 0
        if args.command == "emergency-stop":
            apply_emergency_stop(database, "main", str(runtime))
            return 0
        if args.command == "emergency-resume":
            current = database.get_supervisor_state("main")
            target = (
                SupervisorState.PAUSED if current and current["paused"] else SupervisorState.IDLE
            )
            _command_state_change(
                database,
                state=target,
                event_type="supervisor.emergency_resumed",
                emergency_stop=False,
            )
            return 0
        if args.command == "reset-crash-loop":
            reset_crash_loop(database, "main")
            return 0
        if args.command == "clean":
            if not args.yes:
                raise RuntimeError("Refusing to clean without --yes.")
            current = database.get_supervisor_state("main")
            worker = (
                database.get_worker_instance(current["current_worker_instance_id"])
                if current
                else None
            )
            if worker and worker["status"] not in TERMINAL_WORKER_STATES:
                raise RuntimeError("Cannot clean while a worker is active.")
            database.delete_runtime_state()
            shutil.rmtree(runtime / "logs", ignore_errors=True)
            (runtime / "state").mkdir(parents=True, exist_ok=True)
            (runtime / "logs").mkdir(parents=True, exist_ok=True)
            return 0
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        LOGGER.exception("command_failed command=%s", args.command)
        return 1
    return 2


def _text_report(report: dict[str, Any]) -> str:
    supervisor = report.get("supervisor", {})
    metrics = report.get("metrics", {})
    return "\n".join(
        [
            f"health: {report.get('health', 'unknown')}",
            f"state: {supervisor.get('state', 'unknown')}",
            f"active workers: {metrics.get('activeWorkers', 0)}",
            f"restart count: {metrics.get('restart_count', 0)}",
            f"recovery count: {metrics.get('recovery_count', 0)}",
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
