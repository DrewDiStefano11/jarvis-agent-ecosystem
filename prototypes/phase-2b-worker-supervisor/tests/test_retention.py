import unittest
from helpers import TempRuntimeTestCase
from jarvis_worker_supervisor.retention import enforce_log_retention
from jarvis_worker_supervisor.config import SupervisorConfig
from jarvis_simulated_worker.scenarios import WorkerScenario

class TestLogRetention(TempRuntimeTestCase):
    def test_log_retention(self):
        l_dir = self.runtime_path / "logs"
        f = l_dir / "test.log"
        f.write_text("A" * 200)

        c = SupervisorConfig(runtime_dir=str(self.runtime_path), scenario=WorkerScenario.LOG_FLOOD, max_log_bytes=100)
        enforce_log_retention(str(self.runtime_path), c)

        # Should be truncated to half of max_log_bytes
        self.assertEqual(f.stat().st_size, 50)
