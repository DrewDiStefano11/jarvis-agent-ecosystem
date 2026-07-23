import unittest
from helpers import TempRuntimeTestCase
from jarvis_simulated_worker.state import mark_ready
from jarvis_worker_supervisor.readiness import check_readiness

class TestReadiness(TempRuntimeTestCase):
    def test_readiness_roundtrip(self):
        r_dir = str(self.runtime_path)
        mark_ready(r_dir, "iid", "tok", "healthy")
        r = check_readiness(r_dir, "iid", "tok")
        self.assertIsNotNone(r)
        self.assertEqual(r['scenario'], "healthy")

    def test_readiness_timeout_simulate(self):
        r = check_readiness(str(self.runtime_path), "iid", "tok")
        self.assertIsNone(r)
