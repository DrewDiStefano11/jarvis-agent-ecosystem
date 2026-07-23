import unittest
from helpers import TempRuntimeTestCase
from jarvis_worker_supervisor.shutdown import request_graceful_shutdown
from jarvis_simulated_worker.shutdown import check_shutdown_file

class TestShutdown(TempRuntimeTestCase):
    def test_shutdown_file(self):
        r_dir = str(self.runtime_path)
        request_graceful_shutdown(r_dir, "iid")
        self.assertTrue(check_shutdown_file(r_dir, "iid"))
