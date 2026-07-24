import sys
import time

from .heartbeat import emit_heartbeat
from .shutdown import check_shutdown_file, setup_signals
from .state import mark_ready


def run_scenario(scenario_name: str, runtime_dir: str, instance_id: str, start_token: str) -> int:
    # First, setup signals
    ignore_shutdown = scenario_name == "ignore-shutdown"
    setup_signals(ignore=ignore_shutdown)

    print(f"Worker {instance_id} starting scenario {scenario_name}", flush=True)

    if scenario_name == "crash-immediately":
        print("Crashing immediately", flush=True)
        sys.exit(1)

    if scenario_name == "hang-before-ready":
        print("Hanging before ready", flush=True)
        while not check_shutdown_file(runtime_dir, instance_id):
            time.sleep(0.1)
        return 0

    # Report ready
    mark_ready(runtime_dir, instance_id, start_token, scenario_name)
    print("Marked ready", flush=True)

    if scenario_name == "crash-after-ready":
        print("Crashing after ready", flush=True)
        sys.exit(1)

    if scenario_name == "hang-after-ready":
        print("Hanging after ready", flush=True)
        while not check_shutdown_file(runtime_dir, instance_id):
            time.sleep(0.1)
        return 0

    if scenario_name == "complete-and-exit":
        emit_heartbeat(runtime_dir, instance_id, start_token, 1)
        time.sleep(0.1)
        print("Completing and exiting", flush=True)
        return 0

    if scenario_name == "exit-code":
        mark_ready(runtime_dir, instance_id, start_token, scenario_name)
        sys.exit(42)

    seq = 1
    while True:
        if check_shutdown_file(runtime_dir, instance_id):
            if scenario_name == "slow-shutdown":
                time.sleep(5)
            if not ignore_shutdown:
                print("Shutting down cleanly", flush=True)
                return 0

        if scenario_name != "stop-heartbeats":
            emit_heartbeat(runtime_dir, instance_id, start_token, seq)

        if scenario_name == "log-flood":
            print(f"Log flood line {seq} " * 10, flush=True)
            print(f"Error flood line {seq} " * 10, file=sys.stderr, flush=True)

        if scenario_name == "crash-after-heartbeats" and seq >= 3:
            print("Crashing after heartbeats", flush=True)
            sys.exit(1)

        time.sleep(0.1)
        seq += 1
