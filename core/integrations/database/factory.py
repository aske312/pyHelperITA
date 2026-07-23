from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlparse

from core.integrations.database.sqlite import Database

DatabaseFactory = Callable[[str], Any]
_DRIVERS: dict[str, DatabaseFactory] = {}


def register_database_driver(scheme: str, factory: DatabaseFactory) -> None:
    """Регистрирует внешний драйвер без изменения runtime или UI."""
    normalized = scheme.lower().strip()
    if not normalized:
        raise ValueError("Схема драйвера БД не может быть пустой")
    _DRIVERS[normalized] = factory


def _sqlite_factory(url: str) -> Database:
    parsed = urlparse(url)
    if parsed.scheme != "sqlite":
        raise ValueError("Ожидается SQLite URL")
    if parsed.netloc not in {"", "localhost"}:
        raise ValueError("SQLite URL не поддерживает удалённый host")
    raw_path = unquote(parsed.path)
    if raw_path in {"", "/"}:
        raise ValueError("SQLite URL не содержит путь к файлу")
    # sqlite:///data/app.sqlite3 -> относительный путь; sqlite:////var/... -> абсолютный.
    path = raw_path[1:] if not raw_path.startswith("//") else raw_path[1:]
    return Database(path)


register_database_driver("sqlite", _sqlite_factory)


def create_database(url: str):
    scheme = urlparse(url).scheme.lower()
    factory = _DRIVERS.get(scheme)
    if factory is None:
        available = ", ".join(sorted(_DRIVERS))
        raise ValueError(
            f"Драйвер БД для схемы '{scheme or '<пусто>'}' не установлен. "
            f"Доступно: {available}"
        )
    return factory(url)


def database_description(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "sqlite":
        return f"SQLite · {unquote(parsed.path).lstrip('/')}"
    return f"{parsed.scheme} · {parsed.hostname or 'local'}"
