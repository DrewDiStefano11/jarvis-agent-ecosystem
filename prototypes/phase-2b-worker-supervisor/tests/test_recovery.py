import unittest
from helpers import TempRuntimeTestCase
from jarvis_worker_supervisor.recovery import recover_supervisor_state
from jarvis_worker_supervisor.enums import SupervisorState, WorkerState

class TestRecovery(TempRuntimeTestCase):
    def test_recovery_noop(self):
        db = self.get_db()
        db.set_supervisor_state("main", SupervisorState.IDLE, False, False, False, 0)
        # Shouldn't crash
        recover_supervisor_state(db, "main")
