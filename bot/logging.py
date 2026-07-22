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

from bot.config import Settings

TECHNICAL_LOGGER = "bot.technical"
OPERATIONAL_LOGGER = "bot.operations"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"timestamp": datetime.now(UTC).isoformat(), "level": record.levelname,
                   "event": record.getMessage()}
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _file_logger(name: str, path, settings: Settings, enabled: bool) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.disabled = not enabled
    if enabled:
        logger.setLevel(settings.log_level.upper())
        handler = RotatingFileHandler(path, maxBytes=settings.log_max_bytes,
                                      backupCount=settings.log_backup_count, encoding="utf-8")
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def configure_logging(settings: Settings) -> None:
    settings.log_directory.mkdir(parents=True, exist_ok=True)
    _file_logger(TECHNICAL_LOGGER, settings.log_directory / "technical.log", settings,
                 settings.technical_logging_enabled)
    _file_logger(OPERATIONAL_LOGGER, settings.log_directory / "operations.log", settings,
                 settings.operational_logging_enabled)


def log_resources(settings: Settings) -> None:
    disk = shutil.disk_usage(settings.database_path.resolve().anchor)
    memory = psutil.virtual_memory()
    process = psutil.Process()
    logging.getLogger(TECHNICAL_LOGGER).info("resource_usage", extra={"fields": {
        "cpu_percent": psutil.cpu_percent(), "cpu_count": psutil.cpu_count(),
        "system_memory_total_bytes": memory.total,
        "system_memory_available_bytes": memory.available,
        "system_memory_percent": memory.percent,
        "process_memory_rss_bytes": process.memory_info().rss,
        "process_cpu_percent": process.cpu_percent(),
        "disk_total_bytes": disk.total, "disk_used_bytes": disk.used,
        "disk_free_bytes": disk.free}})


class OperationalLoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        update = data.get("event_update")
        fields = _update_fields(update if isinstance(update, Update) else event)
        started = time.perf_counter()
        logger = logging.getLogger(OPERATIONAL_LOGGER)
        logger.info("request_received", extra={"fields": fields})
        try:
            result = await handler(event, data)
        except Exception:
            fields["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            logger.exception("request_failed", extra={"fields": fields})
            raise
        fields.update(duration_ms=round((time.perf_counter() - started) * 1000, 2),
                      status="handled")
        logger.info("request_completed", extra={"fields": fields})
        return result


class LoggedBot(Bot):
    async def __call__(self, method: TelegramMethod[Any], request_timeout: int | None = None) -> Any:
        started = time.perf_counter()
        logger = logging.getLogger(OPERATIONAL_LOGGER)
        fields = {"telegram_method": method.__class__.__name__,
                  "chat_id": getattr(method, "chat_id", None),
                  "response_text": getattr(method, "text", None)}
        try:
            result = await super().__call__(method, request_timeout=request_timeout)
        except Exception:
            fields["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            logger.exception("response_failed", extra={"fields": fields})
            raise
        fields.update(duration_ms=round((time.perf_counter() - started) * 1000, 2), status="sent")
        logger.info("response_sent", extra={"fields": fields})
        return result


def _update_fields(event: TelegramObject) -> dict[str, Any]:
    message = getattr(event, "message", None)
    callback = getattr(event, "callback_query", None)
    source = message or callback
    user = getattr(source, "from_user", None)
    return {"update_id": getattr(event, "update_id", None),
            "update_type": event.__class__.__name__, "user_id": getattr(user, "id", None),
            "chat_id": getattr(getattr(message, "chat", None), "id", None),
            "request_text": getattr(message, "text", None),
            "callback_data": getattr(callback, "data", None)}