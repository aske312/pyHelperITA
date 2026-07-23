from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

import psutil
from aiogram import BaseMiddleware, Bot
from aiogram.methods import TelegramMethod
from aiogram.types import TelegramObject, Update

from core.config import Settings

TECHNICAL_LOGGER = "core.technical"
OPERATIONAL_LOGGER = "core.operations"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )


def _file_logger(name: str, path, settings: Settings, enabled: bool) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.disabled = not enabled
    if enabled:
        logger.setLevel(settings.log_level.upper())
        handler = RotatingFileHandler(
            path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def configure_logging(settings: Settings) -> None:
    settings.log_directory.mkdir(parents=True, exist_ok=True)
    _file_logger(
        TECHNICAL_LOGGER,
        settings.log_directory / "technical.log",
        settings,
        settings.technical_logging_enabled,
    )
    _file_logger(
        OPERATIONAL_LOGGER,
        settings.log_directory / "operations.log",
        settings,
        settings.operational_logging_enabled,
    )


def log_resources(settings: Settings) -> None:
    process = psutil.Process()
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(settings.database_path.resolve().anchor)
    database_size = (
        settings.database_path.stat().st_size if settings.database_path.exists() else 0
    )
    logging.getLogger(TECHNICAL_LOGGER).info(
        "health",
        extra={
            "fields": {
                "process_rss_mb": round(process.memory_info().rss / 1_048_576, 1),
                "process_cpu_percent": round(process.cpu_percent(), 1),
                "system_memory_percent": round(memory.percent, 1),
                "disk_free_gb": round(disk.free / 1_073_741_824, 2),
                "database_mb": round(database_size / 1_048_576, 2),
            }
        },
    )


class OperationalLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = data.get("event_update")
        fields = _update_fields(update if isinstance(update, Update) else event)
        started = time.perf_counter()
        logger = logging.getLogger(OPERATIONAL_LOGGER)
        try:
            result = await handler(event, data)
        except Exception:
            fields["duration_ms"] = round((time.perf_counter() - started) * 1000)
            logger.exception("update_failed", extra={"fields": fields})
            raise
        fields["duration_ms"] = round((time.perf_counter() - started) * 1000)
        logger.info("update_handled", extra={"fields": fields})
        return result


class LoggedBot(Bot):
    async def __call__(
        self,
        method: TelegramMethod[Any],
        request_timeout: int | None = None,
    ) -> Any:
        try:
            return await super().__call__(method, request_timeout=request_timeout)
        except Exception:
            logging.getLogger(OPERATIONAL_LOGGER).exception(
                "telegram_request_failed",
                extra={
                    "fields": {
                        "method": method.__class__.__name__,
                        "chat_id": getattr(method, "chat_id", None),
                    }
                },
            )
            raise


def _update_fields(event: TelegramObject) -> dict[str, Any]:
    message = getattr(event, "message", None)
    callback = getattr(event, "callback_query", None)
    source = message or callback
    user = getattr(source, "from_user", None)
    callback_message = getattr(callback, "message", None)
    chat = getattr(message or callback_message, "chat", None)
    text = getattr(message, "text", None) or ""
    command = text.split(maxsplit=1)[0] if text.startswith("/") else None
    return {
        "update_id": getattr(event, "update_id", None),
        "user_id": getattr(user, "id", None),
        "chat_id": getattr(chat, "id", None),
        "command": command,
        "callback": getattr(callback, "data", None),
        "message_length": len(text) if text else None,
    }
