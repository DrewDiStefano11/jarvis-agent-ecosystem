import unittest
from helpers import TempRuntimeTestCase
from jarvis_worker_supervisor.enums import SupervisorState, WorkerState

class TestSchema(TempRuntimeTestCase):
    def test_database_init(self):
        db = self.get_db()
        # Should execute SCHEMA correctly
        db.set_supervisor_state("main", SupervisorState.IDLE, False, False, False, 0)
        state = db.get_supervisor_state("main")
        self.assertIsNotNone(state)
        self.assertEqual(state['status'], "idle")

    def test_repeated_initialization(self):
        db1 = self.get_db()
        db1.set_supervisor_state("main", SupervisorState.IDLE, False, False, False, 0)
        # Init again
        db2 = self.get_db()
        state = db2.get_supervisor_state("main")
        self.assertEqual(state['status'], "idle")
