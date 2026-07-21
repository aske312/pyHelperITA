from __future__ import annotations

from bot.config import get_settings
from bot.db import Database
from bot.service import VacationService


def build_service() -> VacationService:
    settings = get_settings()
    database = Database(settings.database_path)
    service = VacationService(database, settings)
    service.initialize()
    return service
