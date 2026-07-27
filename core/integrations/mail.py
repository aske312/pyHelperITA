from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from datetime import datetime
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
    "senla": SmtpProvider("mail.senla.eu"),
}


def smtp_username(address: str, provider: str) -> str:
    """Return the authentication login, which may differ from the mailbox address."""
    return f"{address}@thunderbird" if provider == "senla" else address


class SmtpMailGateway:
    """Независимый от UI SMTP-адаптер для встроенных и произвольных серверов."""

    def __init__(
        self,
        *,
        username: str,
        password: str,
        provider: str = "yandex",
        host: str | None = None,
        port: int | None = None,
        use_ssl: bool | None = None,
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

    async def verify(self) -> None:
        await asyncio.to_thread(self._verify_sync)

    def _verify_sync(self) -> None:
        smtp_class = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        with smtp_class(self.host, self.port, timeout=30) as client:
            if not self.use_ssl:
                client.starttls()
            client.login(self.username, self.password)

    def _send_sync(self, message: EmailMessage) -> None:
        smtp_class = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        with smtp_class(self.host, self.port, timeout=30) as client:
            if not self.use_ssl:
                client.starttls()
            client.login(self.username, self.password)
            client.send_message(message)


class MailConnectionSyncService:
    """Проверяет доступ к парольным SMTP-интеграциям, ничего не отправляя."""

    def __init__(self, database, integration_service):
        self.database = database
        self.integrations = integration_service

    async def sync_all(self) -> int:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT employee_id, mail_provider, mail_address FROM employee_integrations "
                "WHERE mail_provider IN ('google', 'microsoft', 'yandex', 'mailru', 'senla') "
                "AND mail_address IS NOT NULL"
            ).fetchall()
        connected = 0
        for row in rows:
            employee_id = int(row["employee_id"])
            password = self.integrations.get_mail_password(employee_id)
            if not password:
                continue
            try:
                await SmtpMailGateway(
                    username=smtp_username(
                        str(row["mail_address"]), str(row["mail_provider"])
                    ),
                    password=password,
                    provider=str(row["mail_provider"]),
                ).verify()
                status = "connected"
                connected += 1
            except Exception:
                status = "error"
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE employee_integrations SET mail_status = ?, updated_at = ? "
                    "WHERE employee_id = ?",
                    (status, datetime.now().isoformat(timespec="seconds"), employee_id),
                )
        return connected
