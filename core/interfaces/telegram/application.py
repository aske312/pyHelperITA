from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram import F, Bot, Dispatcher, Router
from aiogram import BaseMiddleware
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

from core.config import Settings, get_settings
from core.export import export_vacations_xlsx
from core.logging import (
    LoggedBot,
    OperationalLoggingMiddleware,
    configure_logging,
    log_resources,
)
from core.instance import single_bot_instance
from core.interfaces.telegram.integrations import create_integrations_router
from core.reminders import NotificationSender, ReminderSender, SystemNotificationSender
from core.runtime import build_service
from core.service import VacationService
from core.interfaces.telegram.absence import create_absence_router
from core.interfaces.telegram.calendar import create_calendar_router
from core.interfaces.telegram.events import create_events_router
from core.interfaces.telegram.onboarding import create_onboarding_router
from core.interfaces.telegram.owner import create_owner_router
from core.interfaces.telegram.profile import create_profile_router
from core.interfaces.telegram.team import create_team_router

router = Router()
_service: VacationService | None = None

BASE_COMMANDS = [
    BotCommand(command="clear", description="Закрыть меню и сбросить ввод"),
    BotCommand(command="absence", description="Оформить отсутствие"),
    BotCommand(command="my_events", description="Мои события: просмотр и управление"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="contacts", description="Контакты сотрудников"),
    BotCommand(command="help", description="Доступные команды"),
    BotCommand(command="events", description="События команды"),
    BotCommand(command="integrations", description="Почта и календарь"),
]
TEAM_LEAD_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="employees", description="Моя команда"),
    BotCommand(command="invite_team", description="Пригласить в команду"),
    BotCommand(command="dismiss_team", description="Исключить из команды"),
]
OWNER_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="staff", description="Все сотрудники"),
    BotCommand(command="teams", description="Все команды"),
    BotCommand(command="employee_add", description="Добавить сотрудника"),
    BotCommand(command="notifications", description="Нотификации"),
    BotCommand(command="export", description="Выгрузить XLSX"),
]
GUEST_COMMANDS = [
    BotCommand(command="clear", description="Закрыть меню и сбросить ввод"),
    BotCommand(command="absence", description="Оформить отпуск"),
    BotCommand(command="my_events", description="Мои отпуска"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="help", description="Доступные команды"),
    BotCommand(command="integrations", description="Почта и календарь"),
]


def get_service() -> VacationService:
    if _service is None:
        raise RuntimeError("Сервис бота не инициализирован")
    return _service


def commands_for_employee(employee) -> list[BotCommand]:
    if employee.role == "owner":
        commands = list(OWNER_COMMANDS)
        if employee.is_team_lead:
            commands.append(BotCommand(command="employees", description="Моя команда"))
    elif employee.role == "guest":
        commands = list(GUEST_COMMANDS)
    elif employee.is_team_lead:
        commands = list(TEAM_LEAD_COMMANDS)
    else:
        commands = list(BASE_COMMANDS)
    settings = get_settings()
    return [item for item in commands if settings.command_enabled(item.command)]


class FeatureCommandMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings):
        self.settings = settings

    async def __call__(self, handler, event, data):
        text = getattr(event, "text", None) or ""
        if text.startswith("/"):
            command = text[1:].split(maxsplit=1)[0].split("@", 1)[0]
            if not self.settings.command_enabled(command):
                await event.answer("Эта команда отключена в config/features.config.")
                return None
        return await handler(event, data)


async def configure_command_menus(bot: Bot, service: VacationService) -> None:
    await bot.set_my_commands(
        [item for item in BASE_COMMANDS if get_settings().command_enabled(item.command)]
    )
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
        "owner.md"
        if employee is not None and employee.role == "owner"
        else "team_lead.md"
        if employee is not None and employee.is_team_lead
        else "guest.md"
        if employee is not None and employee.role == "guest"
        else "employee.md"
    )
    guide = get_settings().guides_path / guide_name
    if get_settings().default_send_role_guide and guide.exists():
        await message.answer_document(
            FSInputFile(guide, filename=guide.name),
            caption="📘 Руководство для вашей роли",
        )


@router.message(Command("clear", "cancel"))
async def clear_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.is_bot:
            try:
                await message.bot.delete_message(
                    message.chat.id, message.reply_to_message.message_id
                )
            except Exception:
                pass
    try:
        await message.delete()
    except Exception:
        await message.answer("Состояние очищено. Бот ожидает команду.")


@router.callback_query(F.data == "ui_close")
async def close_current_output(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await query.message.delete()
    except Exception:
        await query.message.edit_text("Меню закрыто. Бот ожидает команду.")
    await query.answer()


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
    temp_root = Path(".temp")
    temp_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=temp_root) as directory:
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
    if settings.feature_onboarding and not settings.onboarding_password:
        raise RuntimeError("ONBOARDING_PASSWORD необходимо задать в файле .env")
    configure_logging(settings)
    _service = build_service()
    bot_class = LoggedBot if settings.operational_logging_enabled else Bot
    bot = bot_class(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.message.outer_middleware(FeatureCommandMiddleware(settings))
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
    if settings.feature_absences:
        dispatcher.include_router(create_absence_router(_service, settings))
    if settings.feature_teams:
        dispatcher.include_router(create_team_router(_service, settings))
    if settings.feature_events:
        dispatcher.include_router(create_events_router(_service, settings))
    if settings.feature_integrations:
        dispatcher.include_router(create_integrations_router(_service, settings))
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
    if settings.feature_notifications or settings.feature_events:
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
    with single_bot_instance():
        asyncio.run(run_bot())
