from __future__ import annotations

import calendar
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.service import VacationService

MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


class VacationForm(StatesGroup):
    selecting = State()


def _buttons(items: list[tuple[str, str]], width: int = 3):
    builder = InlineKeyboardBuilder()
    for text, data in items:
        builder.button(text=text, callback_data=data)
    builder.adjust(width)
    return builder.as_markup()


def _months(year: int, prefix: str, first: int = 1):
    return _buttons([(MONTHS[m - 1], f"{prefix}:{year}:{m}") for m in range(first, 13)])


def _days(year: int, month: int, prefix: str, minimum: date | None = None):
    rows = [
        [
            InlineKeyboardButton(text=name, callback_data="calendar_noop")
            for name in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        ]
    ]
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for weekday, day in enumerate(week):
            if day == 0:
                row.append(
                    InlineKeyboardButton(text=" ", callback_data="calendar_noop")
                )
                continue
            value = date(year, month, day)
            enabled = minimum is None or value >= minimum
            label = f"🔴{day}" if weekday >= 5 else str(day)
            row.append(
                InlineKeyboardButton(
                    text=label if enabled else f"·{day}",
                    callback_data=f"{prefix}:{value.isoformat()}"
                    if enabled
                    else "calendar_noop",
                )
            )
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_calendar_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="calendar")

    @router.callback_query(F.data == "calendar_noop")
    async def calendar_noop(query: CallbackQuery) -> None:
        await query.answer()

    def is_admin(telegram_user_id: int) -> bool:
        employee = service.database.get_employee_by_telegram(telegram_user_id)
        return employee is not None and employee.role == "admin"

    @router.message(Command("vacation"))
    async def begin(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        employee = service.database.get_employee_by_telegram(message.from_user.id)
        if employee is None or not employee.profile_completed:
            await message.answer("Сначала завершите регистрацию командой /start.")
            return
        current = date.today().year
        await state.set_state(VacationForm.selecting)
        await state.set_data({"employee_id": employee.id, "admin_mode": False})
        await message.answer(
            "Выберите год начала отпуска:",
            reply_markup=_buttons(
                [(str(year), f"vyear:{year}") for year in (current, current + 1)], 2
            ),
        )

    @router.message(Command("employees"))
    async def employees(message: Message) -> None:
        if message.from_user is None:
            return
        actor = service.database.get_employee_by_telegram(message.from_user.id)
        if actor is None or actor.role != "admin":
            await message.answer("Команда доступна только администратору.")
            return
        items = service.database.list_employees()
        await message.answer(
            "Список сотрудников:",
            reply_markup=_buttons(
                [(item.full_name, f"employee:{item.id}") for item in items], 1
            ),
        )

    @router.callback_query(F.data.startswith("employee:"))
    async def employee_actions(query: CallbackQuery) -> None:
        if not is_admin(query.from_user.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        employee = service.database.get_employee(int((query.data or "").split(":")[1]))
        await query.message.edit_text(
            employee.full_name + chr(10) + f"Роль: {employee.role}",
            reply_markup=_buttons(
                [
                    ("Отпуска сотрудника", f"employeevacations:{employee.id}"),
                    ("Добавить отпуск", f"adminvacation:{employee.id}"),
                ],
                1,
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("employeevacations:"))
    async def employee_vacations(query: CallbackQuery) -> None:
        if not is_admin(query.from_user.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        employee_id = int((query.data or "").split(":")[1])
        employee = service.database.get_employee(employee_id)
        vacations = service.database.list_vacations(employee_id=employee_id)
        lines = [
            f"#{item.id}: {item.start_date:%d.%m.%Y}–{item.end_date:%d.%m.%Y}"
            for item in vacations
        ]
        text = (
            employee.full_name
            + chr(10)
            + (chr(10).join(lines) if lines else "Отпусков нет.")
        )
        await query.message.edit_text(
            text,
            reply_markup=_buttons(
                [("Добавить отпуск", f"adminvacation:{employee.id}")], 1
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("adminvacation:"))
    async def admin_begin(query: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(query.from_user.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        employee_id = int((query.data or "").split(":")[1])
        current = date.today().year
        await state.set_state(VacationForm.selecting)
        await state.set_data({"employee_id": employee_id, "admin_mode": True})
        await query.message.edit_text(
            "Выберите год начала отпуска:",
            reply_markup=_buttons(
                [
                    (str(year), f"vyear:{year}")
                    for year in range(current - 5, current + 2)
                ]
            ),
        )
        await query.answer()

    @router.callback_query(VacationForm.selecting, F.data.startswith("vyear:"))
    async def year(query: CallbackQuery, state: FSMContext) -> None:
        year_value = int((query.data or "").split(":")[1])
        data = await state.get_data()
        today = date.today()
        if not data.get("admin_mode") and year_value not in {
            today.year,
            today.year + 1,
        }:
            await query.answer("Этот год недоступен.", show_alert=True)
            return
        first = (
            today.month
            if year_value == today.year and not data.get("admin_mode")
            else 1
        )
        await query.message.edit_text(
            "Выберите месяц начала:", reply_markup=_months(year_value, "vsm", first)
        )
        await query.answer()

    @router.callback_query(VacationForm.selecting, F.data.startswith("vsm:"))
    async def start_month(query: CallbackQuery, state: FSMContext) -> None:
        _, year_value, month = (query.data or "").split(":")
        data = await state.get_data()
        minimum = None if data.get("admin_mode") else date.today()
        await query.message.edit_text(
            "Выберите первый день:",
            reply_markup=_days(int(year_value), int(month), "vs", minimum),
        )
        await query.answer()

    @router.callback_query(VacationForm.selecting, F.data.startswith("vs:"))
    async def start_day(query: CallbackQuery, state: FSMContext) -> None:
        start = date.fromisoformat((query.data or "").split(":", 1)[1])
        await state.update_data(start_date=start.isoformat())
        await query.message.edit_text(
            f"Начало: {start:%d.%m.%Y}" + chr(10) + "Выберите месяц окончания:",
            reply_markup=_months(start.year, "vem", start.month),
        )
        await query.answer()

    @router.callback_query(VacationForm.selecting, F.data.startswith("vem:"))
    async def end_month(query: CallbackQuery, state: FSMContext) -> None:
        _, year_value, month = (query.data or "").split(":")
        data = await state.get_data()
        start = date.fromisoformat(str(data["start_date"]))
        await query.message.edit_text(
            "Выберите последний день:",
            reply_markup=_days(int(year_value), int(month), "ve", start),
        )
        await query.answer()

    @router.callback_query(VacationForm.selecting, F.data.startswith("ve:"))
    async def finish(query: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        start = date.fromisoformat(str(data["start_date"]))
        end = date.fromisoformat((query.data or "").split(":", 1)[1])
        try:
            vacation = service.add_vacation(int(data["employee_id"]), start, end)
            employee = service.database.get_employee(vacation.employee_id)
            anomalies = service.vacation_anomalies(vacation)
        except (ValueError, LookupError) as error:
            await query.answer(str(error), show_alert=True)
            return
        await state.clear()
        await query.message.edit_text(
            f"Отпуск сохранён: {start:%d.%m.%Y}–{end:%d.%m.%Y}."
        )
        if settings.admin_telegram_id and not data.get("admin_mode"):
            anomaly_text = (
                chr(10) + "⚠️ Аномалии: " + "; ".join(anomalies) if anomalies else ""
            )
            await query.bot.send_message(
                settings.admin_telegram_id,
                f"{employee.full_name} добавил отпуск "
                f"{start:%d.%m.%Y}–{end:%d.%m.%Y}." + anomaly_text,
            )
        await query.answer()

    return router
