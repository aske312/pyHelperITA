from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import Settings
from core.events import format_events, list_events, period_dates
from core.service import VacationService


def _period_buttons(team_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="Сегодня", callback_data=f"team_events_period:{team_id}:today")
    builder.button(
        text="Текущая неделя", callback_data=f"team_events_period:{team_id}:week"
    )
    builder.button(
        text="Предстоящий месяц",
        callback_data=f"team_events_period:{team_id}:month",
    )
    builder.button(text="← К команде", callback_data=f"team_view:{team_id}")
    builder.button(text="✖️ Закрыть", callback_data="ui_close")
    builder.adjust(1)
    return builder.as_markup()


def create_events_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="events")

    def actor(telegram_user_id: int):
        return service.database.get_employee_by_telegram(telegram_user_id)

    def can_view_team(employee, team) -> bool:
        return employee is not None and (
            employee.role == "owner" or team.lead_id == employee.id
        )

    def global_buttons():
        builder = InlineKeyboardBuilder()
        builder.button(text="Сегодня", callback_data="events_period:today")
        builder.button(text="Текущая неделя", callback_data="events_period:week")
        builder.button(text="Предстоящий месяц", callback_data="events_period:month")
        builder.button(text="✖️ Закрыть", callback_data="ui_close")
        builder.adjust(1)
        return builder.as_markup()

    @router.message(Command("events"))
    async def events_command(message: Message) -> None:
        if message.from_user is None:
            return
        employee = actor(message.from_user.id)
        if employee is None or employee.role == "guest" or not employee.is_active:
            await message.answer("Просмотр запланированных событий доступен сотрудникам.")
            return
        await message.answer(
            "📅 <b>Запланированные события</b>\n\nВыберите период:",
            parse_mode="HTML",
            reply_markup=global_buttons(),
        )

    @router.callback_query(F.data.startswith("events_period:"))
    async def global_events_period(query: CallbackQuery) -> None:
        employee = actor(query.from_user.id)
        if employee is None or employee.role == "guest" or not employee.is_active:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        period = (query.data or "").split(":")[1]
        today = datetime.now(ZoneInfo(settings.app_timezone)).date()
        start, end, title = period_dates(period, today)
        await query.message.edit_text(
            format_events(list_events(service.database, start, end), title),
            parse_mode="HTML",
            reply_markup=global_buttons(),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("team_events:"))
    async def team_events(query: CallbackQuery) -> None:
        employee = actor(query.from_user.id)
        team_id = int((query.data or "").split(":")[1])
        team = service.database.get_team(team_id)
        if not can_view_team(employee, team):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            f"📅 <b>События команды {escape(team.name)}</b>\n\nВыберите период:",
            parse_mode="HTML",
            reply_markup=_period_buttons(team_id),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("team_events_period:"))
    async def events_period(query: CallbackQuery) -> None:
        employee = actor(query.from_user.id)
        _, raw_team_id, period = (query.data or "").split(":")
        team_id = int(raw_team_id)
        team = service.database.get_team(team_id)
        if not can_view_team(employee, team):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        today = datetime.now(ZoneInfo(settings.app_timezone)).date()
        start, end, title = period_dates(period, today)
        employee_ids = {team.lead_id}
        employee_ids.update(
            item.id for item in service.database.list_team_members(team.id)
        )
        await query.message.edit_text(
            format_events(
                list_events(service.database, start, end, employee_ids),
                f"{title} · {escape(team.name)}",
            ),
            parse_mode="HTML",
            reply_markup=_period_buttons(team_id),
        )
        await query.answer()

    return router
