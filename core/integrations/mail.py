from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True, slots=True)
class SmtpProvider:
    host: str
    port: int = 465
    use_ssl: bool = True


SMTP_PROVIDERS = {
    "yandex": SmtpProvider("smtp.yandex.ru"),
    "mailru": SmtpProvider("smtp.mail.ru"),
    "google": SmtpProvider("smtp.gmail.com"),
    "microsoft": SmtpProvider("smtp.office365.com", 587, False),
}


class SmtpMailGateway:
    """Независимый от UI SMTP-адаптер для встроенных и произвольных серверов."""

    def __init__(
        self, *, username: str, password: str, provider: str = "yandex",
        host: str | None = None, port: int | None = None, use_ssl: bool | None = None
    ):
        preset = SMTP_PROVIDERS.get(provider)
        if preset is None and not host:
            raise ValueError(f"Для провайдера {provider} необходимо указать SMTP host")
        self.username = username
        self.password = password
        self.host = host or preset.host
        self.port = port or preset.port
        self.use_ssl = preset.use_ssl if use_ssl is None and preset else bool(use_ssl)

    async def send(self, *, recipient: str, subject: str, body: str) -> str:
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        await asyncio.to_thread(self._send_sync, message)
        return message["Message-ID"] or ""

    def _send_sync(self, message: EmailMessage) -> None:
        smtp_class = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        with smtp_class(self.host, self.port, timeout=30) as client:
            if not self.use_ssl:
                client.starttls()
            client.login(self.username, self.password)
            client.send_message(message)
