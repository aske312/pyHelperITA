from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    operational_logging_enabled: bool = True
    technical_logging_enabled: bool = True
    log_directory: Path = Path("logs")
    log_level: str = "INFO"
    log_max_bytes: int = Field(default=2_097_152, ge=1024)
    log_backup_count: int = Field(default=3, ge=1, le=100)
    technical_log_interval_seconds: int = Field(default=300, ge=30, le=86400)
    default_reminder_time: str = "09:00"
    default_reminder_text: str = "Напоминание: ваш отпуск начинается {start_date}."

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("default_reminder_time")
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

    def ensure_runtime_dirs(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.operational_logging_enabled or self.technical_logging_enabled:
            self.log_directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
