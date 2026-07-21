from __future__ import annotations

import asyncio
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, FSInputFile, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import get_settings
from bot.export import export_vacations_xlsx
from bot.reminders import NotificationSender, ReminderSender
from bot.runtime import build_service
from bot.service import VacationService
from bot.telegram.calendar import create_calendar_router
from bot.telegram.admin import create_admin_router
from bot.telegram.onboarding import create_onboarding_router
from bot.telegram.profile import create_profile_router

router = Router()
_service: VacationService | None = None


def get_service() -> VacationService:
    if _service is None:
        raise RuntimeError("Сервис бота не инициализирован")
    return _service


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Корпоративный бот-помощник. Доступные сейчас возможности:\n\n"
        "/vacation — открыть календарь отпусков\n"
        "/my_vacations — показать мои отпуска\n"
        "/profile — посмотреть и изменить персональные данные\n"
        "Остальные команды доступны согласно вашей роли."
    )


@router.message(Command("my_vacations"))
async def my_vacations_command(message: Message) -> None:
    if not message.from_user:
        return
    employee = get_service().database.get_employee_by_telegram(message.from_user.id)
    if employee is None:
        await message.answer("Ваш Telegram ID не привязан.")
        return
    vacations = get_service().database.list_vacations(employee_id=employee.id)
    if not vacations:
        await message.answer("Сохраненных отпусков пока нет.")
        return
    lines = [
        f"#{item.id}: {item.start_date:%d.%m.%Y}–{item.end_date:%d.%m.%Y}"
        for item in vacations
    ]
    await message.answer("Ваши отпуска:\n" + "\n".join(lines))


def get_admin(message: Message):
    if not message.from_user:
        return None
    employee = get_service().database.get_employee_by_telegram(message.from_user.id)
    if employee is None or employee.role != "admin":
        return None
    return employee


@router.message(Command("export"))
async def export_command(message: Message) -> None:
    if get_admin(message) is None:
        await message.answer("Выгрузка доступна только администратору.")
        return
    parts = (message.text or "").split(maxsplit=1)
    try:
        year = int(parts[1]) if len(parts) == 2 else None
    except ValueError:
        await message.answer("Пример: /export 2026")
        return
    items = get_service().database.list_vacations(year=year)
    with TemporaryDirectory() as directory:
        path = export_vacations_xlsx(items, Path(directory) / "vacations.xlsx")
        await message.answer_document(
            FSInputFile(path, filename=f"vacations-{year or 'all'}.xlsx"),
            caption=f"Отпусков в выгрузке: {len(items)}",
        )


@router.message(Command("reminder"))
async def reminder_command(message: Message) -> None:
    if not message.from_user:
        return
    employee = get_service().database.get_employee_by_telegram(message.from_user.id)
    if employee is None or employee.role != "admin":
        await message.answer("Команда доступна только администратору.")
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) != 4:
        await message.answer(
            "Пример: /reminder 7 09:00 Напоминание: отпуск начинается {start_date}."
        )
        return
    try:
        days_before = int(parts[1])
        reminder_time = time.fromisoformat(parts[2])
        get_service().set_reminder(employee.id, days_before, reminder_time, parts[3])
    except ValueError as error:
        await message.answer(f"Некорректные настройки: {error}")
        return
    await message.answer(
        f"Напоминание настроено: за {days_before} дн., в {reminder_time:%H:%M}."
    )


async def run_bot() -> None:
    global _service
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
    _service = build_service()
    bot = Bot(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_onboarding_router(_service, settings))
    dispatcher.include_router(create_profile_router(_service))
    dispatcher.include_router(create_admin_router(_service, settings))
    dispatcher.include_router(create_calendar_router(_service, settings))
    dispatcher.include_router(router)
    await bot.set_my_commands(
        [
            BotCommand(command="vacation", description="Добавить отпуск"),
            BotCommand(command="my_vacations", description="Мои отпуска"),
            BotCommand(command="profile", description="Мой профиль"),
            BotCommand(command="employees", description="Список сотрудников"),
            BotCommand(command="export", description="Выгрузить XLSX"),
            BotCommand(command="broadcast", description="Рассылка пользователям"),
        ]
    )
    sender = ReminderSender(_service.database, settings, bot)
    notification_sender = NotificationSender(_service.database, settings, bot)
    scheduler = AsyncIOScheduler(timezone=settings.app_timezone)
    scheduler.add_job(
        sender.send_due, "interval", minutes=1, max_instances=1, coalesce=True
    )
    scheduler.add_job(
        notification_sender.send_due,
        "interval",
        minutes=1,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def start_bot() -> None:
    asyncio.run(run_bot())
