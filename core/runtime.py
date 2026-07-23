from __future__ import annotations

from core.config import get_settings
from core.integrations.database import create_database
from core.service import VacationService


def build_service() -> VacationService:
    settings = get_settings()
    database = create_database(settings.database_url)
    service = VacationService(database, settings)
    service.initialize()
    return service
