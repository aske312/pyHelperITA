from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram import F, Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    FSInputFile,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import get_settings
from bot.export import export_vacations_xlsx
from bot.logging import (
    LoggedBot,
    OperationalLoggingMiddleware,
    configure_logging,
    log_resources,
)
from bot.reminders import NotificationSender, ReminderSender, SystemNotificationSender
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
    BotCommand(command="my_vacations", description="Изменить или удалить отпуск"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="contacts", description="Контакты сотрудников"),
    BotCommand(command="help", description="Доступные команды"),
]
TEAM_LEAD_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="employees", description="Моя команда и сотрудники"),
    BotCommand(command="invite_team", description="Пригласить в команду"),
    BotCommand(command="dismiss_team", description="Исключить из команды"),
]
OWNER_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="staff", description="Все сотрудники"),
    BotCommand(command="team_create", description="Создать команду"),
    BotCommand(command="invite_team", description="Пригласить в команду"),
    BotCommand(command="dismiss_team", description="Исключить из команды"),
    BotCommand(command="delete_team", description="Удалить команду"),
    BotCommand(command="guest", description="Добавить гостя"),
    BotCommand(command="notifications", description="Нотификации"),
    BotCommand(command="export", description="Выгрузить XLSX"),
]
GUEST_COMMANDS = [
    BotCommand(command="vacation", description="Добавить отпуск"),
    BotCommand(command="my_vacations", description="Изменить или удалить отпуск"),
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


async def set_employee_command_menu(bot: Bot, employee) -> None:
    """Refresh the private command menu after a profile or role change."""
    if employee.telegram_user_id is None:
        return
    await bot.set_my_commands(
        commands_for_employee(employee),
        scope=BotCommandScopeChat(chat_id=employee.telegram_user_id),
    )


@router.message(Command("help"))
async def help_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    employee = None
    if message.from_user is not None:
        employee = get_service().database.get_employee_by_telegram(message.from_user.id)
    commands = (
        commands_for_employee(employee) if employee is not None else BASE_COMMANDS
    )
    lines = [f"• /<b>{item.command}</b> — {item.description}" for item in commands]
    await message.answer(
        "🧭 <b>Доступные команды</b>\n\n" + "\n".join(lines), parse_mode="HTML"
    )

    guide_name = (
        "admin.md"
        if employee is not None and employee.role == "owner"
        else "lead.md"
        if employee is not None and employee.is_team_lead
        else "guest.md"
        if employee is not None and employee.role == "guest"
        else "employees.md"
    )
    guide = Path(__file__).resolve().parent.parent / "docs" / guide_name
    if guide.exists():
        await message.answer_document(
            FSInputFile(guide, filename=guide.name),
            caption="📘 Руководство для вашей роли",
        )


def get_owner(message: Message):
    if not message.from_user:
        return None
    employee = get_service().database.get_employee_by_telegram(message.from_user.id)
    return employee if employee is not None and employee.role == "owner" else None


def _export_buttons(items: list[tuple[str, str]]):
    builder = InlineKeyboardBuilder()
    for text, data in items:
        builder.button(text=text, callback_data=data)
    builder.adjust(2)
    return builder.as_markup()


async def _send_export(target, year: int | None) -> None:
    items = get_service().database.list_vacations(year=year)
    with TemporaryDirectory() as directory:
        path = export_vacations_xlsx(items, Path(directory) / "vacations.xlsx")
        await target.answer_document(
            FSInputFile(path, filename=f"vacations-{year or 'all'}.xlsx"),
            caption=f"✅ Выгрузка готова · отпусков: {len(items)}",
        )


@router.message(Command("export"))
async def export_command(message: Message) -> None:
    if not get_settings().feature_exports:
        await message.answer("Экспорт отключен.")
        return
    if get_owner(message) is None:
        await message.answer("Выгрузка доступна только владельцу.")
        return
    years = sorted(
        {item.start_date.year for item in get_service().database.list_vacations()},
        reverse=True,
    )
    await message.answer(
        "📊 <b>Период выгрузки</b>\n\nВыберите год или весь список:",
        parse_mode="HTML",
        reply_markup=_export_buttons(
            [("Весь список", "export_period:all")]
            + [(str(year), f"export_period:{year}") for year in years]
        ),
    )


@router.callback_query(F.data.startswith("export_period:"))
async def export_period(query: CallbackQuery) -> None:
    if not query.from_user:
        return
    employee = get_service().database.get_employee_by_telegram(query.from_user.id)
    if employee is None or employee.role != "owner":
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    raw_period = (query.data or "").split(":")[1]
    year = None if raw_period == "all" else int(raw_period)
    await query.answer("Формирую выгрузку…")
    await _send_export(query.message, year)


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
    dispatcher.include_router(create_team_router(_service, settings))
    dispatcher.include_router(router)
    await configure_command_menus(bot, _service)
    sender = ReminderSender(_service.database, settings, bot)
    notification_sender = NotificationSender(_service.database, settings, bot)
    system_notification_sender = SystemNotificationSender(
        _service.database, settings, bot
    )
    scheduler = AsyncIOScheduler(timezone=settings.app_timezone)
    if settings.feature_reminders:
        scheduler.add_job(
            sender.send_due, "interval", minutes=1, max_instances=1, coalesce=True
        )
    if settings.feature_notifications:
        scheduler.add_job(
            notification_sender.send_due,
            "interval",
            minutes=1,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            system_notification_sender.send_due,
            "interval",
            minutes=1,
            max_instances=1,
            coalesce=True,
        )
    if settings.technical_logging_enabled:
        log_resources(settings)
        scheduler.add_job(
            log_resources,
            "interval",
            seconds=settings.technical_log_interval_seconds,
            args=[settings],
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
