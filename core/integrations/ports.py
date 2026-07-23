from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol


class MailGateway(Protocol):
    """Контракт будущего SMTP/OAuth-провайдера."""

    async def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
    ) -> str: ...


class CalendarGateway(Protocol):
    """Контракт будущего Google, Microsoft или CalDAV-провайдера."""

    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> Sequence[object]: ...

