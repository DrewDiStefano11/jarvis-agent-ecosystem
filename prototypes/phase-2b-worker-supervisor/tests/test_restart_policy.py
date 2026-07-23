import unittest
from jarvis_worker_supervisor.restart_policy import calculate_backoff
from jarvis_worker_supervisor.config import SupervisorConfig
from jarvis_simulated_worker.scenarios import WorkerScenario

class TestRestartPolicy(unittest.TestCase):
    def test_backoff_calculation(self):
        c = SupervisorConfig(runtime_dir=".", scenario=WorkerScenario.HEALTHY, initial_backoff_seconds=1.0, maximum_backoff_seconds=10.0)
        self.assertEqual(calculate_backoff(0, c), 0.0)
        self.assertEqual(calculate_backoff(1, c), 1.0)
        self.assertEqual(calculate_backoff(2, c), 2.0)
        self.assertEqual(calculate_backoff(3, c), 4.0)
        self.assertEqual(calculate_backoff(4, c), 8.0)
        self.assertEqual(calculate_backoff(5, c), 10.0) # max bounded
