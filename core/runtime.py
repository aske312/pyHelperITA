from __future__ import annotations

from core.config import get_settings
from core.db import Database
from core.service import VacationService


def build_service() -> VacationService:
    settings = get_settings()
    database = Database(settings.database_path)
    service = VacationService(database, settings)
    service.initialize()
    return service
