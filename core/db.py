"""Совместимый импорт SQLite-адаптера.

Новый код должен использовать core.integrations.database и create_database().
"""

from core.integrations.database.sqlite import (
    Database,
    format_display_name,
    validate_full_name,
)

__all__ = ["Database", "format_display_name", "validate_full_name"]
