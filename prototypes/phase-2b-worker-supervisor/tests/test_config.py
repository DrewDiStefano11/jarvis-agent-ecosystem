import unittest
from jarvis_worker_supervisor.config import SupervisorConfig
from jarvis_simulated_worker.scenarios import WorkerScenario

class TestConfig(unittest.TestCase):
    def test_defaults(self):
        c = SupervisorConfig(runtime_dir="test", scenario=WorkerScenario.HEALTHY)
        self.assertEqual(c.readiness_timeout_seconds, 10.0)
        self.assertFalse(c.jitter_enabled)
