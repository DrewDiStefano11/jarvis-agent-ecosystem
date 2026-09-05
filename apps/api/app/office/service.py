from pathlib import Path

from app.models.office import OfficeCatalog, OfficeCommand
from app.office.repository import OfficeRepository


class OfficeService:
    def __init__(self, sessions):
        catalog = OfficeCatalog.model_validate_json(
            Path(__file__).with_name("catalog.json").read_text(encoding="utf-8")
        )
        self.repository = OfficeRepository(sessions, catalog)

    def snapshot(self):
        return self.repository.snapshot()

    def command(self, identity_id: str, command: OfficeCommand):
        return self.repository.command(identity_id, command)

    def reconcile(self, *, stop_all=False):
        return self.repository.reconcile(stop_all=stop_all)
