import unittest
from helpers import TempRuntimeTestCase
from jarvis_simulated_worker.heartbeat import emit_heartbeat
from jarvis_worker_supervisor.heartbeat import check_heartbeat

class TestHeartbeat(TempRuntimeTestCase):
    def test_heartbeat_roundtrip(self):
        r_dir = str(self.runtime_path)
        emit_heartbeat(r_dir, "iid", "tok", 1)
        hb = check_heartbeat(r_dir, "iid", "tok")
        self.assertIsNotNone(hb)
        self.assertEqual(hb['sequence_number'], 1)

    def test_wrong_token_heartbeat(self):
        r_dir = str(self.runtime_path)
        emit_heartbeat(r_dir, "iid", "tok", 1)
        hb = check_heartbeat(r_dir, "iid", "wrong-tok")
        self.assertIsNone(hb)
