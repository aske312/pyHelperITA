from __future__ import annotations

from typing import Protocol


class MessageGateway(Protocol):
    """Порт доставки: Telegram, другой мессенджер или web реализуют его снаружи."""

    async def send_text(self, recipient: str, text: str) -> None: ...


class IdentityGateway(Protocol):
    """Связывает внешний аккаунт интерфейса с сотрудником ядра."""

    def employee_id_for(self, interface: str, external_id: str) -> int | None: ...
