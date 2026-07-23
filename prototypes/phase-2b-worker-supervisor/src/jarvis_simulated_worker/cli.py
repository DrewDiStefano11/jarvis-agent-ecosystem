import argparse
import sys
import os
from .worker import run_scenario
from .scenarios import WorkerScenario

def main() -> int:
    parser = argparse.ArgumentParser(description="Simulated Jarvis Worker")
    parser.add_argument("--runtime-dir", required=True, help="Path to runtime directory")
    parser.add_argument("--scenario", required=True, choices=[s.value for s in WorkerScenario], help="Scenario to run")
    parser.add_argument("--instance-id", required=True, help="Worker instance ID")
    parser.add_argument("--start-token", required=True, help="Process start token")

    args = parser.parse_args()

    return run_scenario(
        scenario_name=args.scenario,
        runtime_dir=args.runtime_dir,
        instance_id=args.instance_id,
        start_token=args.start_token
    )
