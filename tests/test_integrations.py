from __future__ import annotations

import pytest

from core.config import Settings
from core.db import Database
from core.integrations.mail import smtp_username
from core.integrations.service import IntegrationService
from core.service import VacationService


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        database_path=tmp_path / "integrations.sqlite3",
        owner_telegram_id=None,
        owner_full_name="",
    )
    instance = VacationService(Database(settings.database_path), settings)
    instance.initialize()
    return instance


def test_employee_can_configure_personal_mail_and_calendar(service):
    employee = service.register_employee("Интеграционный Сотрудник", 9901)
    integrations = IntegrationService(service.database)

    empty = integrations.get(employee.id)
    assert empty.mail_status == "disconnected"
    assert empty.calendar_status == "disconnected"

    configured = integrations.configure_mail(
        employee.id, "google", "employee@example.com"
    )
    configured = integrations.configure_calendar(
        employee.id, "microsoft", "employee@example.com"
    )

    assert configured.mail_address == "employee@example.com"
    assert configured.mail_status == "pending"
    assert configured.calendar_provider == "microsoft"
    assert configured.calendar_status == "pending"


def test_integrations_do_not_store_credentials(service):
    employee = service.register_employee("Безопасный Сотрудник", 9902)
    integrations = IntegrationService(service.database)
    integrations.configure_mail(employee.id, "smtp", "safe@example.com")

    with service.database.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(employee_integrations)")
        }

    assert "password" not in columns
    assert "access_token" not in columns
    assert "refresh_token" not in columns


def test_mail_connection_status_can_be_updated(service):
    employee = service.register_employee("Почтовый Сотрудник", 9903)
    integrations = IntegrationService(service.database)
    integrations.configure_mail(employee.id, "yandex", "mail@example.com")

    connected = integrations.set_mail_status(employee.id, "connected")

    assert connected.mail_status == "connected"

    with pytest.raises(ValueError):
        integrations.set_mail_status(employee.id, "unknown")


def test_senla_mail_uses_thunderbird_namespace_login(service):
    employee = service.register_employee("SENLA Сотрудник", 9904)
    integrations = IntegrationService(service.database)

    configured = integrations.configure_mail(
        employee.id, "senla", "sergei_lisin@senla.eu"
    )

    assert configured.mail_provider == "senla"
    assert (
        smtp_username(configured.mail_address, configured.mail_provider)
        == "sergei_lisin@senla.eu@thunderbird"
    )


def test_integration_feature_flags(tmp_path):
    flags = tmp_path / "features.config"
    flags.write_text(
        "INTEGRATIONS=true\nMAIL_INTEGRATIONS=false\n"
        "CALENDAR_INTEGRATIONS=true\nCMD_INTEGRATIONS=false\n",
        encoding="utf-8",
    )

    settings = Settings(feature_config_path=flags)

    assert settings.feature_integrations is True
    assert settings.feature_mail_integrations is False
    assert settings.feature_calendar_integrations is True
    assert settings.command_enabled("integrations") is False
