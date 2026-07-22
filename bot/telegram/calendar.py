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

from bot.access import ROLE_LABELS, can_assign_roles, can_manage
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
            label = f"*{day}" if weekday >= 5 else str(day)
            row.append(
                InlineKeyboardButton(
                    text=label if enabled else f".{day}",
                    callback_data=f"{prefix}:{value.isoformat()}"
                    if enabled
                    else "calendar_noop",
                )
            )
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _vacation_buttons(vacations, *, prefix: str = "editvac"):
    return _buttons(
        [
            (
                f"{item.start_date:%d.%m.%Y} - {item.end_date:%d.%m.%Y}",
                f"{prefix}:{item.id}",
            )
            for item in vacations
        ],
        1,
    )


def create_calendar_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="calendar")

    def actor(telegram_id: int):
        return service.database.get_employee_by_telegram(telegram_id)

    async def start_selection(
        target_id: int,
        privileged: bool,
        state: FSMContext,
        send,
        edit_vacation_id: int | None = None,
    ) -> None:
        current = date.today().year
        await state.set_state(VacationForm.selecting)
        await state.set_data(
            {
                "employee_id": target_id,
                "privileged_mode": privileged,
                "edit_vacation_id": edit_vacation_id,
            }
        )
        years = (
            range(current - 5, current + 2) if privileged else (current, current + 1)
        )
        await send(
            "Выберите год начала отпуска:",
            reply_markup=_buttons([(str(year), f"vyear:{year}") for year in years], 2),
        )

    @router.callback_query(F.data == "calendar_noop")
    async def calendar_noop(query: CallbackQuery) -> None:
        await query.answer()

    @router.message(Command("vacation"))
    async def begin(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        await state.clear()
        employee = actor(message.from_user.id)
        if employee is None or not employee.profile_completed:
            await message.answer("Сначала завершите регистрацию командой /start.")
            return
        await start_selection(employee.id, False, state, message.answer)

    @router.message(Command("my_vacations"))
    async def my_vacations(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        await state.clear()
        employee = actor(message.from_user.id)
        if employee is None:
            await message.answer("Сначала зарегистрируйтесь через /start.")
            return
        vacations = service.database.list_vacations(employee_id=employee.id)
        if not vacations:
            await message.answer(
                "🏖 Сохранённых отпусков пока нет. Добавьте первый через /vacation."
            )
            return
        await message.answer(
            "🏖 <b>Мои отпуска</b>\n\nСначала выберите отпуск:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (
                        f"{item.start_date:%d.%m.%Y} — {item.end_date:%d.%m.%Y}",
                        f"vacation_actions:{item.id}",
                    )
                    for item in vacations
                ],
                1,
            ),
        )

    @router.callback_query(F.data.startswith("vacation_actions:"))
    async def vacation_actions(query: CallbackQuery) -> None:
        vacation = service.database.get_vacation(int((query.data or "").split(":")[1]))
        employee = actor(query.from_user.id)
        if employee is None or vacation.employee_id != employee.id:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            f"🏖 <b>{vacation.start_date:%d.%m.%Y} — "
            f"{vacation.end_date:%d.%m.%Y}</b>\n\nЧто сделать с отпуском?",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("✏️ Изменить", f"editvac:{vacation.id}"),
                    ("🗑 Удалить", f"deletevac:{vacation.id}"),
                ],
                1,
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("editvac:"))
    async def edit_vacation(query: CallbackQuery, state: FSMContext) -> None:
        vacation = service.database.get_vacation(int((query.data or "").split(":")[1]))
        employee = actor(query.from_user.id)
        if employee is None or not can_manage(
            employee, service.database.get_employee(vacation.employee_id)
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await start_selection(
            vacation.employee_id,
            employee.role == "owner" or employee.is_team_lead,
            state,
            query.message.edit_text,
            vacation.id,
        )
        await query.answer()

    @router.callback_query(F.data.startswith("deletevac:"))
    async def request_delete(query: CallbackQuery) -> None:
        vacation = service.database.get_vacation(int((query.data or "").split(":")[1]))
        employee = actor(query.from_user.id)
        if employee is None or not can_manage(
            employee, service.database.get_employee(vacation.employee_id)
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            f"Удалить отпуск {vacation.start_date:%d.%m.%Y} - {vacation.end_date:%d.%m.%Y}?",
            reply_markup=_buttons(
                [("Подтвердить удаление", f"confirmdeletevac:{vacation.id}")], 1
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("confirmdeletevac:"))
    async def confirm_delete(query: CallbackQuery) -> None:
        vacation = service.database.get_vacation(int((query.data or "").split(":")[1]))
        employee = actor(query.from_user.id)
        if employee is None or not can_manage(
            employee, service.database.get_employee(vacation.employee_id)
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        service.database.delete_vacation(vacation.id)
        await query.message.edit_text("✅ Отпуск удалён.")
        await query.answer()

    @router.callback_query(F.data.startswith("employee:"))
    async def employee_actions(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        employee = service.database.get_employee(int((query.data or "").split(":")[1]))
        if current_actor is None or not can_manage(current_actor, employee):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        items = [
            ("Отпуска сотрудника", f"employeevacations:{employee.id}"),
            ("Добавить отпуск", f"ownervacation:{employee.id}"),
            ("Изменить данные", f"manage_profile:{employee.id}"),
        ]
        if can_assign_roles(current_actor):
            items.extend(
                [
                    ("🔐 ПРАВА И НАЗНАЧЕНИЯ", "calendar_noop"),
                    (
                        "Назначить роль «Сотрудник»",
                        f"employee_role:{employee.id}:employee",
                    ),
                    ("Сделать руководителем", f"employee_lead:{employee.id}:on"),
                    ("Снять права руководителя", f"employee_lead:{employee.id}:off"),
                    ("Назначить роль «Гость»", f"employee_role:{employee.id}:guest"),
                    ("Назначить роль «Владелец»", f"employee_role:{employee.id}:owner"),
                    ("Закрепить за руководителем", f"chooselead:{employee.id}"),
                    ("Назначить ментора", f"choosementor:{employee.id}"),
                    ("🗑 Удалить сотрудника", f"delete_employee:{employee.id}"),
                ]
            )
        await query.message.edit_text(
            f"👤 <b>{employee.full_name}</b>\n"
            f"Роль: <b>{ROLE_LABELS.get(employee.role, employee.role)}</b>\n\n"
            "Выберите нужный раздел и действие:",
            parse_mode="HTML",
            reply_markup=_buttons(items, 1),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("delete_employee:"))
    async def delete_employee_request(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        employee = service.database.get_employee(int((query.data or "").split(":")[1]))
        if current_actor is None or current_actor.role != "owner":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        if employee.role == "owner":
            await query.answer("Нельзя удалить владельца продукта.", show_alert=True)
            return
        await query.message.edit_text(
            f"🗑 Удалить сотрудника <b>{employee.full_name}</b>?\n\n"
            "Будут удалены профиль, отпуска и связи с командами.",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("Удалить безвозвратно", f"confirm_delete_employee:{employee.id}"),
                    ("Отмена", "calendar_noop"),
                ],
                1,
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("confirm_delete_employee:"))
    async def delete_employee_confirm(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        if current_actor is None or current_actor.role != "owner":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        employee_id = int((query.data or "").split(":")[1])
        employee = service.database.get_employee(employee_id)
        service.database.delete_employee(employee_id)
        await query.message.edit_text(
            f"✅ Запись <b>{employee.full_name}</b> удалена.",
            parse_mode="HTML",
        )
        await query.answer("Сотрудник удалён")

    @router.callback_query(F.data.startswith("employee_role:"))
    async def change_employee_role(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        if current_actor is None or not can_assign_roles(current_actor):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        _, employee_id, role = (query.data or "").split(":")
        employee = service.database.update_employee(int(employee_id), role=role)
        from bot.bot import set_employee_command_menu

        await set_employee_command_menu(query.bot, employee)
        await query.message.edit_text(
            f"{employee.full_name}\nНовая роль: {ROLE_LABELS[employee.role]}"
        )
        await query.answer("Роль обновлена")

    @router.callback_query(F.data.startswith("employee_lead:"))
    async def change_team_lead_property(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        if current_actor is None or not can_assign_roles(current_actor):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        _, raw_id, value = (query.data or "").split(":")
        employee = service.database.update_employee(
            int(raw_id), is_team_lead=value == "on"
        )
        from bot.bot import set_employee_command_menu

        await set_employee_command_menu(query.bot, employee)
        status = "включено" if employee.is_team_lead else "отключено"
        await query.message.edit_text(
            f"{employee.full_name}\nСвойство тимлида {status}."
        )
        await query.answer()

    @router.callback_query(F.data.startswith("chooselead:"))
    async def choose_team_lead(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        if current_actor is None or not can_assign_roles(current_actor):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        employee_id = int((query.data or "").split(":")[1])
        leads = [
            item
            for item in service.database.list_employees()
            if item.is_team_lead and item.id != employee_id
        ]
        items = [
            (item.full_name, f"assignlead:{employee_id}:{item.id}") for item in leads
        ]
        items.append(("Без тимлида", f"assignlead:{employee_id}:none"))
        await query.message.edit_text(
            "Выберите тимлида:", reply_markup=_buttons(items, 1)
        )
        await query.answer()

    @router.callback_query(F.data.startswith("assignlead:"))
    async def assign_team_lead(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        if current_actor is None or not can_assign_roles(current_actor):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        _, raw_employee, raw_lead = (query.data or "").split(":")
        lead_id = None if raw_lead == "none" else int(raw_lead)
        employee = service.database.update_employee(
            int(raw_employee), team_lead_id=lead_id, set_team_lead=True
        )
        await query.message.edit_text(
            f"Тимлид для {employee.full_name}: {employee.team_lead_id or 'не назначен'}"
        )
        await query.answer()

    @router.callback_query(F.data.startswith("choosementor:"))
    async def choose_mentor(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        employee_id = int((query.data or "").split(":")[1])
        target = service.database.get_employee(employee_id)
        if current_actor is None or not can_manage(current_actor, target):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        candidates = [
            item
            for item in service.database.list_employees()
            if item.id != employee_id and item.role != "guest"
        ]
        items = [
            (item.full_name, f"assignmentor:{employee_id}:{item.id}")
            for item in candidates
        ]
        items.append(("Без ментора", f"assignmentor:{employee_id}:none"))
        await query.message.edit_text(
            "Выберите ментора:", reply_markup=_buttons(items, 1)
        )
        await query.answer()

    @router.callback_query(F.data.startswith("assignmentor:"))
    async def assign_mentor(query: CallbackQuery) -> None:
        current_actor = actor(query.from_user.id)
        _, raw_employee, raw_mentor = (query.data or "").split(":")
        target = service.database.get_employee(int(raw_employee))
        if current_actor is None or not can_manage(current_actor, target):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        mentor_id = None if raw_mentor == "none" else int(raw_mentor)
        employee = service.database.update_employee(
            target.id, mentor_id=mentor_id, set_mentor=True
        )
        await query.message.edit_text(
            f"Ментор для {employee.full_name}: {employee.mentor_id or 'не назначен'}"
        )
        await query.answer()

    @router.callback_query(F.data.startswith("employeevacations:"))
    async def employee_vacations(query: CallbackQuery) -> None:
        employee_id = int((query.data or "").split(":")[1])
        employee = service.database.get_employee(employee_id)
        current_actor = actor(query.from_user.id)
        if current_actor is None or not can_manage(current_actor, employee):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        vacations = service.database.list_vacations(employee_id=employee_id)
        if not vacations:
            await query.message.edit_text(
                f"{employee.full_name}\nОтпусков нет.",
                reply_markup=_buttons(
                    [("Добавить отпуск", f"ownervacation:{employee.id}")], 1
                ),
            )
        else:
            await query.message.edit_text(
                f"{employee.full_name}\nВыберите отпуск:",
                reply_markup=_vacation_buttons(vacations),
            )
        await query.answer()

    @router.callback_query(F.data.startswith("ownervacation:"))
    async def managed_vacation_begin(query: CallbackQuery, state: FSMContext) -> None:
        employee_id = int((query.data or "").split(":")[1])
        target = service.database.get_employee(employee_id)
        current_actor = actor(query.from_user.id)
        if current_actor is None or not can_manage(current_actor, target):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await start_selection(employee_id, True, state, query.message.edit_text)
        await query.answer()

    @router.callback_query(VacationForm.selecting, F.data.startswith("vyear:"))
    async def year(query: CallbackQuery, state: FSMContext) -> None:
        year_value = int((query.data or "").split(":")[1])
        data = await state.get_data()
        today = date.today()
        if not data.get("privileged_mode") and year_value not in {
            today.year,
            today.year + 1,
        }:
            await query.answer("Этот год недоступен.", show_alert=True)
            return
        first = (
            today.month
            if year_value == today.year and not data.get("privileged_mode")
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
        minimum = None if data.get("privileged_mode") else date.today()
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
            f"Начало: {start:%d.%m.%Y}\nВыберите месяц окончания:",
            reply_markup=_months(start.year, "vem", start.month),
        )
        await query.answer()

    @router.callback_query(VacationForm.selecting, F.data.startswith("vem:"))
    async def end_month(query: CallbackQuery, state: FSMContext) -> None:
        _, year_value, month = (query.data or "").split(":")
        start = date.fromisoformat(str((await state.get_data())["start_date"]))
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
            edit_id = data.get("edit_vacation_id")
            if edit_id:
                vacation = service.database.update_vacation(int(edit_id), start, end)
                action = "обновлен"
            else:
                vacation = service.add_vacation(int(data["employee_id"]), start, end)
                action = "сохранен"
            employee = service.database.get_employee(vacation.employee_id)
            anomalies = service.vacation_anomalies(vacation)
        except (ValueError, LookupError) as error:
            await query.answer(str(error), show_alert=True)
            return
        await state.clear()
        await query.message.edit_text(
            f"Отпуск {action}: {start:%d.%m.%Y} - {end:%d.%m.%Y}."
        )
        if settings.owner_telegram_id and not data.get("privileged_mode"):
            anomaly_text = "\nАномалии: " + "; ".join(anomalies) if anomalies else ""
            await query.bot.send_message(
                settings.owner_telegram_id,
                f"{employee.full_name}: отпуск {action} {start:%d.%m.%Y} - {end:%d.%m.%Y}."
                + anomaly_text,
            )
        await query.answer()

    return router
