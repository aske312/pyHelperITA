from cryptography.fernet import Fernet
import pytest

from core.access import PermissionPolicy
from core.config import Settings
from core.db import Database
from core.integrations.secrets import SecretStore
from core.integrations.service import IntegrationService
from core.integrations.database import create_database
from core.service import VacationService


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        database_path=tmp_path / "architecture.sqlite3",
        feature_config_path=tmp_path / "missing.config",
    )
    instance = VacationService(Database(settings.database_path), settings)
    instance.initialize()
    return instance


def test_feature_dependencies_are_validated(tmp_path):
    flags = tmp_path / "features.config"
    flags.write_text(
        "STRICT_FEATURE_DEPENDENCIES=true\nPROFILES=false\n"
        "INTEGRATIONS=false\nMAIL_INTEGRATIONS=true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="зависимости"):
        Settings(feature_config_path=flags)


def test_database_url_selects_sqlite_adapter(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path.as_posix()}/selected.sqlite3",
        feature_config_path=tmp_path / "missing.config",
    )
    database = create_database(settings.database_url)
    database.initialize()
    assert database.path.name == "selected.sqlite3"


def test_legacy_database_path_becomes_database_url(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    settings = Settings(
        database_path=path,
        database_url="",
        feature_config_path=tmp_path / "missing.config",
    )
    assert settings.database_url.startswith("sqlite:///")


def test_permission_inheritance():
    policy = PermissionPolicy("config/permissions.json")
    assert "contacts.read" in policy.permissions_for("employee")
    assert "profile.read_self" in policy.permissions_for("employee")
    assert "employee.manage_team" in policy.permissions_for(
        "employee", is_team_lead=True
    )


def test_integration_password_is_encrypted_and_removed(service):
    employee = service.register_employee("Секретный Сотрудник", 9910)
    secrets = SecretStore(Fernet.generate_key())
    integrations = IntegrationService(service.database, secrets)
    integrations.configure_mail(
        employee.id, "yandex", "employee@yandex.ru", password="app-password"
    )
    with service.database.connect() as connection:
        stored = connection.execute(
            "SELECT secret FROM integration_secrets WHERE employee_id = ?",
            (employee.id,),
        ).fetchone()["secret"]
    assert stored != "app-password"
    assert integrations.get_mail_password(employee.id) == "app-password"
    integrations.disconnect(employee.id, "mail")
    assert integrations.get_mail_password(employee.id) is None
