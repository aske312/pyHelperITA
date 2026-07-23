from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class IntegrationSettings:
    """Безопасные метаданные интеграций без паролей и OAuth-токенов."""

    employee_id: int
    mail_provider: str | None
    mail_address: str | None
    mail_status: str
    calendar_provider: str | None
    calendar_account: str | None
    calendar_status: str
    updated_at: datetime

