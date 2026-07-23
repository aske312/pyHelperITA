from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import Settings
from core.events import format_events, list_events, period_dates
from core.service import VacationService


def _period_buttons():
    builder = InlineKeyboardBuilder()
    builder.button(text="Сегодня", callback_data="events_period:today")
    builder.button(text="Текущая неделя", callback_data="events_period:week")
    builder.button(text="Предстоящий месяц", callback_data="events_period:month")
    builder.button(text="✖️ Закрыть", callback_data="ui_close")
    builder.adjust(1)
    return builder.as_markup()


def create_events_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="events")

    def actor(telegram_user_id: int):
        return service.database.get_employee_by_telegram(telegram_user_id)

    @router.message(Command("events"))
    async def events_command(message: Message) -> None:
        if message.from_user is None:
            return
        employee = actor(message.from_user.id)
        if employee is None or employee.role == "guest":
            await message.answer("Команда недоступна для гостевого профиля.")
            return
        await message.answer(
            "📅 <b>События</b>\n\nВыберите период:",
            parse_mode="HTML",
            reply_markup=_period_buttons(),
        )

    @router.callback_query(F.data.startswith("events_period:"))
    async def events_period(query: CallbackQuery) -> None:
        employee = actor(query.from_user.id)
        if employee is None or employee.role == "guest":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        period = (query.data or "").split(":", 1)[1]
        today = datetime.now(ZoneInfo(settings.app_timezone)).date()
        start, end, title = period_dates(period, today)
        await query.message.edit_text(
            format_events(list_events(service.database, start, end), title),
            parse_mode="HTML",
            reply_markup=_period_buttons(),
        )
        await query.answer()

    return router
