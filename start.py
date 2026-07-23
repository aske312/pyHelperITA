"""Совместимая точка входа для запуска CLI из корня проекта."""

import sys

from core.cli import app


def _configure_utf8_console() -> None:
    """Не зависеть от системной кодовой страницы при выводе CLI."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


if __name__ == "__main__":
    _configure_utf8_console()
    app()