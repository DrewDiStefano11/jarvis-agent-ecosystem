import unittest
import sys
from jarvis_worker_supervisor.launcher import launch_worker
from jarvis_simulated_worker.scenarios import WorkerScenario
from helpers import TempRuntimeTestCase
import subprocess

class TestLauncher(TempRuntimeTestCase):
    def test_launcher_uses_sys_executable_and_shell_false(self):
        proc, out, err = launch_worker(str(self.runtime_path), "iid", "tok", WorkerScenario.COMPLETE_AND_EXIT)
        proc.wait()

        # This proves we can launch and it correctly executes
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(out.exists())
        self.assertTrue(err.exists())
