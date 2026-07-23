"""Обратная совместимость: Telegram-приложение перенесено в слой интерфейсов."""

from core.interfaces.telegram.application import (  # noqa: F401
    commands_for_employee,
    run_bot,
    set_employee_command_menu,
    start_bot,
)

__all__ = [
    "commands_for_employee",
    "run_bot",
    "set_employee_command_menu",
    "start_bot",
]
