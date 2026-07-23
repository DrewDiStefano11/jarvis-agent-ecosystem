import unittest
from helpers import TempRuntimeTestCase
from jarvis_worker_supervisor.crash_loop import check_crash_loop, reset_crash_loop
from jarvis_worker_supervisor.enums import SupervisorState
from jarvis_worker_supervisor.config import SupervisorConfig
from jarvis_simulated_worker.scenarios import WorkerScenario
import time

class TestCrashLoop(TempRuntimeTestCase):
    def test_crash_loop_detection(self):
        db = self.get_db()
        c = SupervisorConfig(runtime_dir=".", scenario=WorkerScenario.CRASH_IMMEDIATELY, maximum_restarts=3, restart_window_seconds=60.0)

        db.set_supervisor_state("main", SupervisorState.IDLE, False, False, False, 3)
        with db._get_connection() as conn:
            conn.execute("UPDATE supervisor_state SET restart_window_started_at = ? WHERE id = 'main'", (time.time(),))

        self.assertTrue(check_crash_loop(db, "main", c))

    def test_reset_crash_loop(self):
        db = self.get_db()
        c = SupervisorConfig(runtime_dir=".", scenario=WorkerScenario.CRASH_IMMEDIATELY, maximum_restarts=3, restart_window_seconds=60.0)

        db.set_supervisor_state("main", SupervisorState.CRASH_LOOP, False, False, True, 3)
        reset_crash_loop(db, "main")

        s = db.get_supervisor_state("main")
        self.assertFalse(s['crash_loop_detected'])
        self.assertEqual(s['restart_attempt_count'], 0)
