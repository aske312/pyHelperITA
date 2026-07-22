from __future__ import annotations

import asyncio
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, BotCommandScopeChat, FSInputFile, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import get_settings
from bot.export import export_vacations_xlsx
from bot.logging import LoggedBot, OperationalLoggingMiddleware, configure_logging, log_resources
from bot.reminders import NotificationSender, ReminderSender
from bot.runtime import build_service
from bot.service import VacationService
from bot.telegram.calendar import create_calendar_router
from bot.telegram.onboarding import create_onboarding_router
from bot.telegram.owner import create_owner_router
from bot.telegram.profile import create_profile_router
from bot.telegram.team import create_team_router

router = Router()
_service: VacationService | None = None

BASE_COMMANDS = [
    BotCommand(command="vacation", description="Добавить отпуск"),
    BotCommand(command="my_vacations", description="Мои отпуска"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="contacts", description="Контакты сотрудников"),
    BotCommand(command="help", description="Доступные команды"),
]
TEAM_LEAD_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="employees", description="Мои сотрудники"),
    BotCommand(command="team", description="Моя команда"),
    BotCommand(command="team_create", description="Создать команду"),
    BotCommand(command="team_add", description="Добавить в команду"),
    BotCommand(command="team_remove", description="Удалить из команды"),
]
OWNER_COMMANDS = TEAM_LEAD_COMMANDS + [
    BotCommand(command="guest", description="Добавить гостя"),
    BotCommand(command="export", description="Выгрузить XLSX"),
    BotCommand(command="broadcast", description="Рассылка пользователям"),
    BotCommand(command="reminder", description="Настроить напоминания"),
]
GUEST_COMMANDS = [
    BotCommand(command="vacation", description="Добавить отпуск"),
    BotCommand(command="my_vacations", description="Мои отпуска"),
    BotCommand(command="contacts", description="Контакт тимлида"),
    BotCommand(command="help", description="Доступные команды"),
]


def get_service() -> VacationService:
    if _service is None:
        raise RuntimeError("Сервис бота не инициализирован")
    return _service


def commands_for_employee(employee) -> list[BotCommand]:
    if employee.role == "owner":
        return OWNER_COMMANDS
    if employee.role == "guest":
        return GUEST_COMMANDS
    if employee.is_team_lead:
        return TEAM_LEAD_COMMANDS
    return BASE_COMMANDS


async def configure_command_menus(bot: Bot, service: VacationService) -> None:
    await bot.set_my_commands(BASE_COMMANDS)
    for employee in service.database.list_employees():
        if employee.telegram_user_id is not None:
            await bot.set_my_commands(
                commands_for_employee(employee),
                scope=BotCommandScopeChat(chat_id=employee.telegram_user_id),
            )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    employee = None
    if message.from_user is not None:
        employee = get_service().database.get_employee_by_telegram(message.from_user.id)
    commands = commands_for_employee(employee) if employee is not None else BASE_COMMANDS
    lines = [f"/<b>{item.command}</b> - {item.description}" for item in commands]
    await message.answer(
        "<b>Доступные команды</b>\n\n" + "\n".join(lines), parse_mode="HTML"
    )


@router.message(Command("my_vacations"))
async def my_vacations_command(message: Message) -> None:
    if not get_settings().feature_vacations:
        await message.answer("Функция отпусков отключена.")
        return
    if not message.from_user:
        return
    employee = get_service().database.get_employee_by_telegram(message.from_user.id)
    if employee is None:
        await message.answer("Ваш Telegram ID не привязан.")
        return
    vacations = get_service().database.list_vacations(employee_id=employee.id)
    if not vacations:
        await message.answer("<b>Мои отпуска</b>\n\nСохраненных отпусков пока нет.", parse_mode="HTML")
        return
    lines = [
        f"{index}. <b>{item.start_date:%d.%m.%Y}</b> - <b>{item.end_date:%d.%m.%Y}</b>"
        f" ({item.days_count} дн.)"
        for index, item in enumerate(vacations, 1)
    ]
    await message.answer("<b>Мои отпуска</b>\n\n" + "\n".join(lines), parse_mode="HTML")


def get_owner(message: Message):
    if not message.from_user:
        return None
    employee = get_service().database.get_employee_by_telegram(message.from_user.id)
    return employee if employee is not None and employee.role == "owner" else None


@router.message(Command("export"))
async def export_command(message: Message) -> None:
    if not get_settings().feature_exports:
        await message.answer("Экспорт отключен.")
        return
    if get_owner(message) is None:
        await message.answer("Выгрузка доступна только владельцу.")
        return
    parts = (message.text or "").split(maxsplit=1)
    try:
        year = int(parts[1]) if len(parts) == 2 else None
    except ValueError:
        await message.answer("Формат: <code>/export 2026</code>", parse_mode="HTML")
        return
    items = get_service().database.list_vacations(year=year)
    with TemporaryDirectory() as directory:
        path = export_vacations_xlsx(items, Path(directory) / "vacations.xlsx")
        await message.answer_document(
            FSInputFile(path, filename=f"vacations-{year or 'all'}.xlsx"),
            caption=f"Выгрузка готова. Отпусков: {len(items)}",
        )


@router.message(Command("reminder"))
async def reminder_command(message: Message) -> None:
    if not get_settings().feature_reminders:
        await message.answer("Напоминания отключены.")
        return
    employee = get_owner(message)
    if employee is None:
        await message.answer("Команда доступна только владельцу.")
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) != 4:
        await message.answer(
            "Формат: <code>/reminder 7 09:00 Текст {start_date}</code>",
            parse_mode="HTML",
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
        f"<b>Напоминание настроено</b>\n\nЗа {days_before} дн. в {reminder_time:%H:%M}",
        parse_mode="HTML",
    )


async def run_bot() -> None:
    global _service
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
    configure_logging(settings)
    _service = build_service()
    bot_class = LoggedBot if settings.operational_logging_enabled else Bot
    bot = bot_class(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    if settings.operational_logging_enabled:
        dispatcher.update.outer_middleware(OperationalLoggingMiddleware())
    if settings.feature_onboarding:
        dispatcher.include_router(create_onboarding_router(_service, settings))
    if settings.feature_profiles:
        dispatcher.include_router(create_profile_router(_service))
    if settings.feature_owner:
        dispatcher.include_router(create_owner_router(_service, settings))
    if settings.feature_vacations:
        dispatcher.include_router(create_calendar_router(_service, settings))
    dispatcher.include_router(create_team_router(_service))
    dispatcher.include_router(router)
    await configure_command_menus(bot, _service)
    sender = ReminderSender(_service.database, settings, bot)
    notification_sender = NotificationSender(_service.database, settings, bot)
    scheduler = AsyncIOScheduler(timezone=settings.app_timezone)
    if settings.feature_reminders:
        scheduler.add_job(sender.send_due, "interval", minutes=1, max_instances=1, coalesce=True)
    if settings.feature_notifications:
        scheduler.add_job(
            notification_sender.send_due, "interval", minutes=1, max_instances=1, coalesce=True
        )
    if settings.technical_logging_enabled:
        log_resources(settings)
        scheduler.add_job(
            log_resources, "interval", seconds=settings.technical_log_interval_seconds,
            args=[settings], max_instances=1, coalesce=True,
        )
    scheduler.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def start_bot() -> None:
    asyncio.run(run_bot())