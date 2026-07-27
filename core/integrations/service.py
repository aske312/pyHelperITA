from __future__ import annotations

import re
from datetime import datetime

from core.db import Database
from core.integrations.models import IntegrationSettings
from core.integrations.secrets import SecretStore

MAIL_PROVIDERS = {"google", "microsoft", "yandex", "mailru", "smtp"}
CALENDAR_PROVIDERS = {"google", "microsoft", "caldav"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class IntegrationService:
    """Управляет персональными настройками; секреты здесь не хранятся."""

    def __init__(self, database: Database, secret_store: SecretStore | None = None):
        self.database = database
        self.secret_store = secret_store

    @staticmethod
    def _from_row(row) -> IntegrationSettings:
        return IntegrationSettings(
            employee_id=int(row["employee_id"]),
            mail_provider=row["mail_provider"],
            mail_address=row["mail_address"],
            mail_status=str(row["mail_status"]),
            calendar_provider=row["calendar_provider"],
            calendar_account=row["calendar_account"],
            calendar_username=row["calendar_username"],
            calendar_status=str(row["calendar_status"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def get(self, employee_id: int) -> IntegrationSettings:
        self.database.get_employee(employee_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM employee_integrations WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            if row is None:
                now = datetime.now().isoformat(timespec="seconds")
                connection.execute(
                    """INSERT INTO employee_integrations(employee_id, updated_at)
                       VALUES (?, ?)""",
                    (employee_id, now),
                )
                row = connection.execute(
                    "SELECT * FROM employee_integrations WHERE employee_id = ?",
                    (employee_id,),
                ).fetchone()
        return self._from_row(row)

    def configure_mail(
        self, employee_id: int, provider: str, address: str, password: str | None = None
    ) -> IntegrationSettings:
        provider = provider.strip().lower()
        address = address.strip().lower()
        if provider not in MAIL_PROVIDERS:
            raise ValueError("Неизвестный почтовый провайдер")
        if not EMAIL_PATTERN.fullmatch(address):
            raise ValueError("Введите корректный email")
        self.get(employee_id)
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE employee_integrations
                   SET mail_provider = ?, mail_address = ?, mail_status = 'pending',
                       updated_at = ?
                   WHERE employee_id = ?""",
                (
                    provider,
                    address,
                    datetime.now().isoformat(timespec="seconds"),
                    employee_id,
                ),
            )
            if password is not None:
                if self.secret_store is None:
                    raise ValueError("Хранилище секретов не настроено")
                connection.execute(
                    """INSERT INTO integration_secrets(employee_id, kind, secret)
                       VALUES (?, 'mail', ?)
                       ON CONFLICT(employee_id, kind) DO UPDATE SET secret=excluded.secret""",
                    (employee_id, self.secret_store.encrypt(password)),
                )
        return self.get(employee_id)

    def get_mail_password(self, employee_id: int) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT secret FROM integration_secrets "
                "WHERE employee_id = ? AND kind = 'mail'",
                (employee_id,),
            ).fetchone()
        if row is None:
            return None
        if self.secret_store is None:
            raise ValueError("Хранилище секретов не настроено")
        return self.secret_store.decrypt(str(row["secret"]))

    def configure_calendar(
        self,
        employee_id: int,
        provider: str,
        account: str,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> IntegrationSettings:
        provider = provider.strip().lower()
        account = account.strip()
        if provider not in CALENDAR_PROVIDERS:
            raise ValueError("Неизвестный календарный провайдер")
        if not account:
            raise ValueError("Укажите аккаунт или адрес календаря")
        if provider == "caldav" and not account.startswith("https://"):
            raise ValueError("CalDAV URL должен начинаться с https://")
        if password is not None and self.secret_store is None:
            raise ValueError("Хранилище секретов не настроено")
        self.get(employee_id)
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE employee_integrations
                   SET calendar_provider = ?, calendar_account = ?,
                       calendar_username = ?, calendar_status = 'pending', updated_at = ?
                   WHERE employee_id = ?""",
                (
                    provider,
                    account,
                    username.strip() if username else None,
                    datetime.now().isoformat(timespec="seconds"),
                    employee_id,
                ),
            )
            if password is not None:
                connection.execute(
                    """INSERT INTO integration_secrets(employee_id, kind, secret)
                       VALUES (?, 'calendar', ?)
                       ON CONFLICT(employee_id, kind) DO UPDATE SET secret=excluded.secret""",
                    (employee_id, self.secret_store.encrypt(password)),
                )
        return self.get(employee_id)

    def get_calendar_password(self, employee_id: int) -> str | None:
        return self._get_password(employee_id, "calendar")

    def _get_password(self, employee_id: int, kind: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT secret FROM integration_secrets WHERE employee_id = ? AND kind = ?",
                (employee_id, kind),
            ).fetchone()
        if row is None:
            return None
        if self.secret_store is None:
            raise ValueError("Хранилище секретов не настроено")
        return self.secret_store.decrypt(str(row["secret"]))

    def disconnect(self, employee_id: int, kind: str) -> IntegrationSettings:
        if kind not in {"mail", "calendar"}:
            raise ValueError("Неизвестный тип интеграции")
        self.get(employee_id)
        with self.database.connect() as connection:
            if kind == "mail":
                connection.execute(
                    """UPDATE employee_integrations
                       SET mail_provider = NULL, mail_address = NULL,
                           mail_status = 'disconnected', updated_at = ?
                       WHERE employee_id = ?""",
                    (datetime.now().isoformat(timespec="seconds"), employee_id),
                )
                connection.execute(
                    "DELETE FROM integration_secrets WHERE employee_id = ? AND kind = 'mail'",
                    (employee_id,),
                )
            else:
                connection.execute(
                    """UPDATE employee_integrations
                       SET calendar_provider = NULL, calendar_account = NULL,
                           calendar_username = NULL,
                           calendar_status = 'disconnected', updated_at = ?
                       WHERE employee_id = ?""",
                    (datetime.now().isoformat(timespec="seconds"), employee_id),
                )
                connection.execute(
                    "DELETE FROM integration_secrets WHERE employee_id = ? AND kind = 'calendar'",
                    (employee_id,),
                )
                connection.execute(
                    "DELETE FROM calendar_events WHERE employee_id = ?", (employee_id,)
                )
        return self.get(employee_id)
