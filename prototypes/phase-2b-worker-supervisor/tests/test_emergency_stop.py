import unittest
from helpers import TempRuntimeTestCase
from jarvis_worker_supervisor.emergency_stop import apply_emergency_stop
from jarvis_worker_supervisor.enums import SupervisorState

class TestEmergencyStop(TempRuntimeTestCase):
    def test_apply_emergency_stop(self):
        db = self.get_db()
        db.set_supervisor_state("main", SupervisorState.IDLE, False, False, False, 0)
        apply_emergency_stop(db, "main")

        s = db.get_supervisor_state("main")
        self.assertTrue(s['emergency_stop'])
