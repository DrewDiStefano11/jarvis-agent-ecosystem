import unittest
from unittest.mock import patch, MagicMock
from helpers import TempRuntimeTestCase
from jarvis_worker_supervisor.enums import WorkerState
from jarvis_worker_supervisor.process_identity import verify_process_identity, generate_process_start_token, generate_instance_id
from jarvis_simulated_worker.scenarios import WorkerScenario
import psutil

class TestProcessIdentity(TempRuntimeTestCase):
    @patch('psutil.Process')
    def test_correct_identity(self, mock_process_class):
        mock_process = MagicMock()
        mock_process.create_time.return_value = 1000.0
        mock_process_class.return_value = mock_process

        db = self.get_db()
        iid = generate_instance_id()
        tok = generate_process_start_token()
        db.insert_worker_instance(iid, 1234, tok, 1000.0, WorkerScenario.HEALTHY, WorkerState.STARTING)

        self.assertTrue(verify_process_identity(db, iid, 1234, tok))

    @patch('psutil.Process')
    def test_wrong_token(self, mock_process_class):
        db = self.get_db()
        iid = generate_instance_id()
        tok = generate_process_start_token()
        db.insert_worker_instance(iid, 1234, tok, 1000.0, WorkerScenario.HEALTHY, WorkerState.STARTING)

        self.assertFalse(verify_process_identity(db, iid, 1234, "wrong-token"))

    @patch('psutil.Process')
    def test_stale_pid_or_reuse(self, mock_process_class):
        mock_process = MagicMock()
        mock_process.create_time.return_value = 2000.0 # Time differs by more than 1s
        mock_process_class.return_value = mock_process

        db = self.get_db()
        iid = generate_instance_id()
        tok = generate_process_start_token()
        db.insert_worker_instance(iid, 1234, tok, 1000.0, WorkerScenario.HEALTHY, WorkerState.STARTING)

        # Simulates a case where the DB pid and checked pid match but create_time differs (PID reuse)
        self.assertFalse(verify_process_identity(db, iid, 1234, tok))

    @patch('psutil.Process')
    def test_access_denied(self, mock_process_class):
        mock_process_class.side_effect = psutil.AccessDenied(1234)

        db = self.get_db()
        iid = generate_instance_id()
        tok = generate_process_start_token()
        db.insert_worker_instance(iid, 1234, tok, 1000.0, WorkerScenario.HEALTHY, WorkerState.STARTING)

        self.assertFalse(verify_process_identity(db, iid, 1234, tok))

    @patch('psutil.Process')
    def test_disappears_during_inspection(self, mock_process_class):
        mock_process_class.side_effect = psutil.NoSuchProcess(1234)

        db = self.get_db()
        iid = generate_instance_id()
        tok = generate_process_start_token()
        db.insert_worker_instance(iid, 1234, tok, 1000.0, WorkerScenario.HEALTHY, WorkerState.STARTING)

        self.assertFalse(verify_process_identity(db, iid, 1234, tok))

    def test_missing_identity(self):
        db = self.get_db()
        self.assertFalse(verify_process_identity(db, "unknown-id", 1234, "token"))

    @patch('psutil.pid_exists')
    def test_pid_exists_alone_is_insufficient(self, mock_pid_exists):
        mock_pid_exists.return_value = True

        db = self.get_db()
        # Even if pid_exists is true, verify_process_identity fails without matching token and create_time
        self.assertFalse(verify_process_identity(db, "unknown-id", 1234, "token"))
