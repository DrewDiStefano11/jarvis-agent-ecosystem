import argparse
import sys
import json
import time
from pathlib import Path
from .config import SupervisorConfig
from .database import Database
from .supervisor import Supervisor
from .watchdog import Watchdog
from .enums import SupervisorState
from .reporting import generate_status_report
from .crash_loop import reset_crash_loop
from .emergency_stop import apply_emergency_stop
from .errors import SupervisorOwnershipConflict
from jarvis_simulated_worker.scenarios import WorkerScenario

def init_db(runtime_dir: str):
    db_path = Path(runtime_dir) / "state" / "supervisor.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return Database(str(db_path))

def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis Worker Supervisor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_p = subparsers.add_parser("init")
    init_p.add_argument("--runtime-dir", required=True)

    start_p = subparsers.add_parser("start")
    start_p.add_argument("--runtime-dir", required=True)
    start_p.add_argument("--scenario", required=True)

    run_p = subparsers.add_parser("run")
    run_p.add_argument("--runtime-dir", required=True)
    run_p.add_argument("--scenario", required=True)
    run_p.add_argument("--maximum-restarts", type=int, default=5)

    status_p = subparsers.add_parser("status")
    status_p.add_argument("--runtime-dir", required=True)

    stop_p = subparsers.add_parser("stop")
    stop_p.add_argument("--runtime-dir", required=True)

    pause_p = subparsers.add_parser("pause")
    pause_p.add_argument("--runtime-dir", required=True)

    resume_p = subparsers.add_parser("resume")
    resume_p.add_argument("--runtime-dir", required=True)

    estop_p = subparsers.add_parser("emergency-stop")
    estop_p.add_argument("--runtime-dir", required=True)

    eresume_p = subparsers.add_parser("emergency-resume")
    eresume_p.add_argument("--runtime-dir", required=True)

    reset_cl_p = subparsers.add_parser("reset-crash-loop")
    reset_cl_p.add_argument("--runtime-dir", required=True)

    sim_p = subparsers.add_parser("simulate")
    sim_p.add_argument("--runtime-dir", required=True)
    sim_p.add_argument("--scenario", required=True)

    report_p = subparsers.add_parser("report")
    report_p.add_argument("--runtime-dir", required=True)
    report_p.add_argument("--format", choices=["text", "json"], default="text")
    report_p.add_argument("--output")

    clean_p = subparsers.add_parser("clean")
    clean_p.add_argument("--runtime-dir", required=True)

    args = parser.parse_args()

    runtime = Path(args.runtime_dir)
    if not runtime.exists():
        if args.command == "init":
            runtime.mkdir(parents=True)
        else:
            print("Runtime not initialized.", file=sys.stderr)
            return 2

    db = init_db(args.runtime_dir)
    scenario_arg = getattr(args, 'scenario', 'healthy')
    try:
        scenario = WorkerScenario(scenario_arg)
    except ValueError:
        scenario = WorkerScenario.HEALTHY

    config = SupervisorConfig(runtime_dir=args.runtime_dir, scenario=scenario)

    if args.command == "init":
        print("Initialized runtime.", flush=True)
        return 0

    elif args.command == "start":
        sup = Supervisor(db, config)
        if not sup.acquire_lease():
            print("Supervisor ownership conflict.")
            return 3
        sup.start_worker()
        print("Started worker.", flush=True)
        return 0

    elif args.command == "run":
        config.maximum_restarts = args.maximum_restarts
        sup = Supervisor(db, config)
        if not sup.acquire_lease():
            print("Supervisor ownership conflict.")
            return 3
        wd = Watchdog(sup)
        try:
            wd.start()
        except KeyboardInterrupt:
            wd.stop()
        return 0

    elif args.command == "status":
        rep = generate_status_report(db, "main")
        print(json.dumps(rep, indent=2))
        return 0

    elif args.command == "stop":
        state = db.get_supervisor_state("main")
        if state:
            db.set_supervisor_state("main", SupervisorState.STOPPING, state['paused'], state['emergency_stop'], state['crash_loop_detected'], state['restart_attempt_count'], state['current_worker_instance_id'], state['current_worker_pid'], state['current_worker_start_token'])
            # Trigger tick
            sup = Supervisor(db, config)
            sup.tick()
        return 0

    elif args.command == "pause":
        state = db.get_supervisor_state("main")
        if state:
            db.set_supervisor_state("main", SupervisorState(state['status']), True, state['emergency_stop'], state['crash_loop_detected'], state['restart_attempt_count'], state['current_worker_instance_id'], state['current_worker_pid'], state['current_worker_start_token'])
        return 0

    elif args.command == "resume":
        state = db.get_supervisor_state("main")
        if state:
            db.set_supervisor_state("main", SupervisorState(state['status']), False, state['emergency_stop'], state['crash_loop_detected'], state['restart_attempt_count'], state['current_worker_instance_id'], state['current_worker_pid'], state['current_worker_start_token'])
        return 0

    elif args.command == "emergency-stop":
        apply_emergency_stop(db, "main")
        # Trigger tick
        sup = Supervisor(db, config)
        sup.tick()
        return 0

    elif args.command == "emergency-resume":
        state = db.get_supervisor_state("main")
        if state:
            db.set_supervisor_state("main", SupervisorState(state['status']), state['paused'], False, state['crash_loop_detected'], state['restart_attempt_count'], state['current_worker_instance_id'], state['current_worker_pid'], state['current_worker_start_token'])
        return 0

    elif args.command == "reset-crash-loop":
        reset_crash_loop(db, "main")
        return 0

    elif args.command == "simulate":
        # Simplified simulation wrapper for testing without wd loop blocking
        sup = Supervisor(db, config)
        sup.start_worker()
        for _ in range(50):
            sup.tick()
            time.sleep(1)
        return 0

    elif args.command == "report":
        rep = generate_status_report(db, "main")
        out = json.dumps(rep, indent=2) if args.format == "json" else str(rep)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)
        else:
            print(out)
        return 0

    elif args.command == "clean":
        state = db.get_supervisor_state("main")
        worker_id = state['current_worker_instance_id'] if state else None

        if worker_id:
            w = db.get_worker_instance(worker_id)
            if w and w['status'] not in ('stopped', 'crashed', 'killed'):
                print("Cannot clean: worker is active.", file=sys.stderr)
                return 6
        import shutil
        if (runtime / "state").exists():
            shutil.rmtree(runtime / "state")
        if (runtime / "logs").exists():
            shutil.rmtree(runtime / "logs")
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "state").mkdir(parents=True, exist_ok=True)
        (runtime / "logs").mkdir(parents=True, exist_ok=True)
        return 0

    return 2

if __name__ == "__main__":
    sys.exit(main())
