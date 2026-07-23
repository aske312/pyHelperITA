from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from core.features import FEATURES, flag_mapping, validate_dependencies


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения и файла .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    telegram_bot_token: str = ""
    database_path: Path = Path("data/base.sqlite3")
    database_url: str = ""
    app_timezone: str = "Europe/Moscow"
    owner_telegram_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("OWNER_TELEGRAM_ID", "ADMIN_TELEGRAM_ID"),
    )
    owner_full_name: str = Field(
        default="", validation_alias=AliasChoices("OWNER_FULL_NAME", "ADMIN_FULL_NAME")
    )
    default_reminder_days: int = Field(default=14, ge=0, le=365)
    feature_onboarding: bool = True
    feature_profiles: bool = True
    feature_vacations: bool = True
    feature_owner: bool = Field(
        default=True, validation_alias=AliasChoices("FEATURE_OWNER", "FEATURE_ADMIN")
    )
    feature_exports: bool = True
    feature_reminders: bool = True
    feature_notifications: bool = True
    feature_events: bool = True
    feature_teams: bool = True
    feature_absences: bool = True
    feature_integrations: bool = True
    feature_mail_integrations: bool = True
    feature_calendar_integrations: bool = True
    strict_feature_dependencies: bool = False
    feature_config_path: Path = Path("config/features.config")
    directories_path: Path = Path("config/directories.json")
    permissions_path: Path = Path("config/permissions.json")
    guides_path: Path = Path("docs/guides")
    integration_secret_key: str = Field(default="", repr=False)
    onboarding_password: str = Field(default="", repr=False)
    daily_events_time: str = "09:10"
    command_start: bool = True
    command_clear: bool = True
    command_help: bool = True
    command_vacation: bool = True
    command_absence: bool = True
    command_sick_leave: bool = True
    command_day_off: bool = True
    command_my_events: bool = True
    command_profile: bool = True
    command_contacts: bool = True
    command_events: bool = True
    command_employees: bool = True
    command_invite_team: bool = True
    command_dismiss_team: bool = True
    command_staff: bool = True
    command_teams: bool = True
    command_team_create: bool = True
    command_delete_team: bool = True
    command_guest: bool = False
    command_notifications: bool = True
    command_export: bool = True
    command_integrations: bool = True
    auto_daily_events: bool = True
    auto_birthday_notifications: bool = True
    auto_probation_notifications: bool = True
    auto_vacation_notifications: bool = True
    default_guest_access: bool = True
    default_send_role_guide: bool = True
    profile_relations: bool = True
    employee_onboarding_path: Path = Path("docs/onboarding/employee.md")
    guest_onboarding_path: Path = Path("docs/onboarding/guest.md")
    operational_logging_enabled: bool = True
    technical_logging_enabled: bool = True
    log_directory: Path = Path("logs")
    log_level: str = "INFO"
    log_max_bytes: int = Field(default=2_097_152, ge=1024)
    log_backup_count: int = Field(default=3, ge=1, le=100)
    technical_log_interval_seconds: int = Field(default=300, ge=30, le=86400)
    default_reminder_time: str = "09:00"
    default_reminder_text: str = "Напоминание: ваш отпуск начинается {start_date}."

    @model_validator(mode="after")
    def load_feature_flags(self):
        mapping = flag_mapping()
        mapping["STRICT_FEATURE_DEPENDENCIES"] = "strict_feature_dependencies"
        path = self.feature_config_path
        if not path.exists():
            return self
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"{path}:{line_number}: ожидается КЛЮЧ=true/false")
            key, raw_value = (part.strip() for part in line.split("=", 1))
            if key not in mapping:
                raise ValueError(f"{path}:{line_number}: неизвестный фичтогл {key}")
            value = raw_value.lower()
            if value not in {"true", "false"}:
                raise ValueError(
                    f"{path}:{line_number}: значение должно быть true/false"
                )
            setattr(self, mapping[key], value == "true")
        if self.strict_feature_dependencies:
            validate_dependencies(
                {
                    name: bool(getattr(self, feature.setting))
                    for name, feature in FEATURES.items()
                }
            )
        return self

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def resolve_database_url(self):
        if not self.database_url:
            normalized = self.database_path.as_posix()
            self.database_url = f"sqlite:///{quote(normalized)}"
        return self

    @field_validator("default_reminder_time", "daily_events_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("Время должно быть в формате HH:MM")
        hour, minute = map(int, parts)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Некорректное время")
        return f"{hour:02d}:{minute:02d}"

    @field_validator("owner_full_name")
    @classmethod
    def normalize_owner_name(cls, value: str) -> str:
        return " ".join(value.split())

    def command_enabled(self, command: str) -> bool:
        if command == "integrations" and not self.feature_integrations:
            return False
        field = {
            "start": "command_start",
            "clear": "command_clear",
            "cancel": "command_clear",
            "help": "command_help",
            "vacation": "command_vacation",
            "absence": "command_absence",
            "sick_leave": "command_sick_leave",
            "day_off": "command_day_off",
            "my_events": "command_my_events",
            "profile": "command_profile",
            "contacts": "command_contacts",
            "events": "command_events",
            "employees": "command_employees",
            "invite_team": "command_invite_team",
            "dismiss_team": "command_dismiss_team",
            "staff": "command_staff",
            "teams": "command_teams",
            "team_create": "command_team_create",
            "delete_team": "command_delete_team",
            "guest": "command_guest",
            "notifications": "command_notifications",
            "export": "command_export",
            "integrations": "command_integrations",
        }.get(command)
        return True if field is None else bool(getattr(self, field))

    def ensure_runtime_dirs(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.operational_logging_enabled or self.technical_logging_enabled:
            self.log_directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
