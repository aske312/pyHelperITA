"""Telegram-адаптер: запуск, роутеры и преобразование UI-событий."""

from core.interfaces.telegram.application import (
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
