import tempfile
import shutil
import unittest
from pathlib import Path
from jarvis_worker_supervisor.database import Database

class TempRuntimeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runtime_path = Path(self.temp_dir) / "runtime"
        self.runtime_path.mkdir()
        (self.runtime_path / "state").mkdir()
        (self.runtime_path / "logs").mkdir()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def get_db(self) -> Database:
        db_path = self.runtime_path / "state" / "supervisor.db"
        return Database(str(db_path))
