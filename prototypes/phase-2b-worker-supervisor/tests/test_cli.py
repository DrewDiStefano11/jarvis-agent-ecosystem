import unittest
import subprocess
from helpers import TempRuntimeTestCase
import sys

class TestCLI(TempRuntimeTestCase):
    def test_cli_help(self):
        res = subprocess.run([sys.executable, "-m", "jarvis_worker_supervisor", "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Jarvis Worker Supervisor", res.stdout)

    def test_cli_init_clean(self):
        r_dir = str(self.runtime_path)
        res = subprocess.run([sys.executable, "-m", "jarvis_worker_supervisor", "init", "--runtime-dir", r_dir], capture_output=True)
        self.assertEqual(res.returncode, 0)

        res = subprocess.run([sys.executable, "-m", "jarvis_worker_supervisor", "clean", "--runtime-dir", r_dir], capture_output=True)
        self.assertEqual(res.returncode, 0)

    def test_cli_simulate(self):
        r_dir = str(self.runtime_path)
        subprocess.run([sys.executable, "-m", "jarvis_worker_supervisor", "init", "--runtime-dir", r_dir], capture_output=True)
        res = subprocess.run([sys.executable, "-m", "jarvis_worker_supervisor", "simulate", "--runtime-dir", r_dir, "--scenario", "complete-and-exit"], capture_output=True)
        self.assertEqual(res.returncode, 0)
