import unittest
import time
from helpers import TempRuntimeTestCase
from jarvis_worker_supervisor.supervisor import Supervisor
from jarvis_worker_supervisor.config import SupervisorConfig
from jarvis_worker_supervisor.enums import SupervisorState
from jarvis_simulated_worker.scenarios import WorkerScenario

class TestEndToEndScenarios(TempRuntimeTestCase):
    def run_scenario_loop(self, scenario: WorkerScenario, iterations: int = 15):
        db = self.get_db()
        c = SupervisorConfig(runtime_dir=str(self.runtime_path), scenario=scenario, readiness_timeout_seconds=2, maximum_restarts=1)
        sup = Supervisor(db, c)

        sup.start_worker()
        for _ in range(iterations):
            sup.tick()
            time.sleep(0.5)

        return db, sup

    def test_immediate_crash_restart(self):
        db, _ = self.run_scenario_loop(WorkerScenario.CRASH_IMMEDIATELY, iterations=5)
        s = db.get_supervisor_state("main")

        # It should crash, increment attempt count, and possibly hit crash loop or be idle waiting for backoff
        self.assertGreater(s['restart_attempt_count'], 0)

    def test_readiness_timeout(self):
        db, _ = self.run_scenario_loop(WorkerScenario.HANG_BEFORE_READY, iterations=10)
        s = db.get_supervisor_state("main")
        self.assertIn(s['status'], [SupervisorState.STOPPING.value, SupervisorState.IDLE.value, SupervisorState.CRASH_LOOP.value])
