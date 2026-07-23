from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import Settings
from core.service import VacationService


class AbsenceForm(StatesGroup):
    sick_start = State()
    sick_end = State()
    sick_document = State()
    day_off_date = State()
    event_edit = State()


def _date_buttons(prefix: str):
    builder = InlineKeyboardBuilder()
    yesterday = date.today() - timedelta(days=1)
    builder.button(text="Вчера", callback_data=f"{prefix}:{yesterday.isoformat()}")
    builder.button(text="Сегодня", callback_data=f"{prefix}:{date.today().isoformat()}")
    tomorrow = date.today() + timedelta(days=1)
    builder.button(text="Завтра", callback_data=f"{prefix}:{tomorrow.isoformat()}")
    builder.button(text="✖️ Закрыть", callback_data="ui_close")
    builder.adjust(3)
    return builder.as_markup()


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%d.%m.%Y").date()


def create_absence_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="absence")

    def actor(user_id: int):
        return service.database.get_employee_by_telegram(user_id)

    def can_edit_event(current, employee_id: int) -> bool:
        return current is not None and (
            current.id == employee_id or current.role == "owner"
        )

    def event_row(kind: str, event_id: int):
        if kind == "sick":
            return service.database.get_sick_leave(event_id)
        if kind == "dayoff":
            return service.database.get_day_off(event_id)
        raise LookupError("Событие не найдено")

    def event_text(kind: str, row) -> str:
        if kind == "dayoff":
            return f"🌿 <b>DayOff</b> · {date.fromisoformat(str(row['day_date'])):%d.%m.%Y}"
        start = date.fromisoformat(str(row["start_date"]))
        end = date.fromisoformat(str(row["end_date"])) if row["end_date"] else None
        period = (
            f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"
            if end
            else f"с {start:%d.%m.%Y} · активен"
        )
        return f"🤒 <b>Больничный</b> · {period}"

    async def send_lead(bot, employee, text: str, *, owner: bool = False) -> None:
        recipients = []
        if employee.team_lead_id is not None:
            lead = service.database.get_employee(employee.team_lead_id)
            if lead.telegram_user_id is not None:
                recipients.append(lead.telegram_user_id)
        if owner and settings.owner_telegram_id is not None:
            recipients.append(settings.owner_telegram_id)
        for chat_id in set(recipients):
            try:
                await bot.send_message(chat_id, text)
            except TelegramAPIError:
                pass

    @router.message(Command("absence"))
    async def absence_menu(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        employee = actor(message.from_user.id)
        if employee is None or not employee.profile_completed:
            await message.answer("Сначала завершите регистрацию через /start.")
            return
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.button(text="🏖 Отпуск", callback_data="absence_kind:vacation")
        if employee.role != "guest":
            builder.button(text="🤒 Больничный", callback_data="absence_kind:sick")
            builder.button(text="🌿 Day Off", callback_data="absence_kind:dayoff")
        builder.button(text="✖️ Закрыть", callback_data="ui_close")
        builder.adjust(1)
        await message.answer(
            "📅 <b>Новое отсутствие</b>\n\nВыберите тип события:",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    @router.callback_query(F.data == "absence_kind:sick")
    async def absence_sick(query: CallbackQuery, state: FSMContext) -> None:
        employee = actor(query.from_user.id)
        if employee is None or employee.role == "guest":
            await query.answer("Этот тип отсутствия недоступен.", show_alert=True)
            return
        await state.clear()
        active = service.database.get_active_sick_leave(employee.id)
        if active is None:
            await state.set_state(AbsenceForm.sick_start)
            await query.message.edit_text(
                "🤒 Укажите дату начала больничного в формате ДД.ММ.ГГГГ.",
                reply_markup=_date_buttons("sick_start"),
            )
        else:
            await state.set_state(AbsenceForm.sick_end)
            await state.set_data({"sick_leave_id": int(active["id"])})
            await query.message.edit_text(
                f"🤒 Больничный открыт с {date.fromisoformat(str(active['start_date'])):%d.%m.%Y}.\n"
                "Укажите дату окончания ДД.ММ.ГГГГ:",
                reply_markup=_date_buttons("sick_end"),
            )
        await query.answer()

    @router.callback_query(F.data == "absence_kind:dayoff")
    async def absence_dayoff(query: CallbackQuery, state: FSMContext) -> None:
        employee = actor(query.from_user.id)
        if employee is None or employee.role == "guest":
            await query.answer("Этот тип отсутствия недоступен.", show_alert=True)
            return
        await state.set_state(AbsenceForm.day_off_date)
        await query.message.edit_text(
            "🌿 Выберите день Day Off или введите дату ДД.ММ.ГГГГ:",
            reply_markup=_date_buttons("day_off_date"),
        )
        await query.answer()

    @router.message(Command("sick_leave"))
    async def sick_leave(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        employee = actor(message.from_user.id)
        if employee is None or not employee.profile_completed:
            await message.answer("Сначала завершите регистрацию через /start.")
            return
        await state.clear()
        active = service.database.get_active_sick_leave(employee.id)
        if active is None:
            await state.set_state(AbsenceForm.sick_start)
            await message.answer(
                "🤒 Укажите дату начала больничного в формате ДД.ММ.ГГГГ.",
                reply_markup=_date_buttons("sick_start"),
            )
        else:
            await state.set_state(AbsenceForm.sick_end)
            await state.set_data({"sick_leave_id": int(active["id"])})
            await message.answer(
                f"🤒 Активный больничный открыт с {date.fromisoformat(str(active['start_date'])):%d.%m.%Y}.\n"
                "Укажите дату окончания ДД.ММ.ГГГГ:",
                reply_markup=_date_buttons("sick_end"),
            )

    async def save_sick_start(
        message: Message,
        state: FSMContext,
        value: date,
        user_id: int | None = None,
        bot=None,
    ) -> None:
        employee = actor(user_id or message.from_user.id)
        try:
            service.database.open_sick_leave(employee.id, value)
        except ValueError as error:
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer(
            "✅ Больничный открыт. Для закрытия выберите «Больничный» в /absence."
        )
        await send_lead(
            bot or message.bot,
            employee,
            f"🤒 {employee.full_name} открыл больничный с {value:%d.%m.%Y}.",
        )

    @router.message(AbsenceForm.sick_start, F.text & ~F.text.startswith("/"))
    async def sick_start_text(message: Message, state: FSMContext) -> None:
        try:
            value = _parse_date(message.text or "")
        except ValueError:
            await message.answer("Введите дату в формате ДД.ММ.ГГГГ.")
            return
        await save_sick_start(message, state, value)

    @router.callback_query(F.data.startswith("sick_start:"))
    async def sick_start_button(query, state: FSMContext) -> None:
        await query.answer()
        await save_sick_start(
            query.message,
            state,
            date.fromisoformat(query.data.split(":")[1]),
            query.from_user.id,
            query.bot,
        )

    async def save_sick_end(message: Message, state: FSMContext, value: date) -> None:
        await state.update_data(sick_end=value.isoformat())
        await state.set_state(AbsenceForm.sick_document)
        await message.answer("📎 Отправьте подтверждающий документ файлом.")

    @router.message(AbsenceForm.sick_end, F.text & ~F.text.startswith("/"))
    async def sick_end_text(message: Message, state: FSMContext) -> None:
        try:
            value = _parse_date(message.text or "")
        except ValueError:
            await message.answer("Введите дату в формате ДД.ММ.ГГГГ.")
            return
        await save_sick_end(message, state, value)

    @router.callback_query(F.data.startswith("sick_end:"))
    async def sick_end_button(query, state: FSMContext) -> None:
        await query.answer()
        await save_sick_end(
            query.message, state, date.fromisoformat(query.data.split(":")[1])
        )

    @router.message(AbsenceForm.sick_document, F.document)
    async def sick_document(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        employee = actor(message.from_user.id)
        try:
            service.database.close_sick_leave(
                int(data["sick_leave_id"]),
                date.fromisoformat(str(data["sick_end"])),
                message.document.file_id,
            )
        except (ValueError, LookupError) as error:
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer("✅ Больничный закрыт, документ сохранён.")
        await send_lead(
            message.bot, employee, f"✅ {employee.full_name} закрыл больничный."
        )

    @router.message(Command("day_off"))
    async def day_off(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        employee = actor(message.from_user.id)
        if employee is None or not employee.profile_completed:
            await message.answer("Сначала завершите регистрацию через /start.")
            return
        await state.set_state(AbsenceForm.day_off_date)
        await message.answer(
            "🌿 Выберите один день DayOff или введите дату ДД.ММ.ГГГГ:",
            reply_markup=_date_buttons("day_off_date"),
        )

    async def save_day_off(
        message: Message,
        state: FSMContext,
        value: date,
        user_id: int | None = None,
        bot=None,
    ) -> None:
        employee = actor(user_id or message.from_user.id)
        if (
            not date.today() - timedelta(days=30)
            <= value
            <= date.today() + timedelta(days=1)
        ):
            await message.answer(
                "DayOff можно оформить за последние 30 дней, сегодня или на завтра."
            )
            return
        try:
            _, anomaly = service.database.add_day_off(employee.id, value)
        except ValueError as error:
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer(f"✅ DayOff оформлен на {value:%d.%m.%Y}.")
        text = f"🌿 {employee.full_name} оформил DayOff на {value:%d.%m.%Y}."
        if anomaly:
            text += "\n⚠️ Аномалия: два DayOff подряд."
        await send_lead(bot or message.bot, employee, text, owner=anomaly)

    @router.message(AbsenceForm.day_off_date, F.text & ~F.text.startswith("/"))
    async def day_off_text(message: Message, state: FSMContext) -> None:
        try:
            value = _parse_date(message.text or "")
        except ValueError:
            await message.answer("Введите дату в формате ДД.ММ.ГГГГ.")
            return
        await save_day_off(message, state, value)

    @router.callback_query(F.data.startswith("day_off_date:"))
    async def day_off_button(query, state: FSMContext) -> None:
        await query.answer()
        await save_day_off(
            query.message,
            state,
            date.fromisoformat(query.data.split(":")[1]),
            query.from_user.id,
            query.bot,
        )

    @router.callback_query(F.data.startswith("absence_actions:"))
    async def absence_actions(query: CallbackQuery) -> None:
        _, kind, raw_id = (query.data or "").split(":")
        row = event_row(kind, int(raw_id))
        current = actor(query.from_user.id)
        if not can_edit_event(current, int(row["employee_id"])):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        builder = InlineKeyboardBuilder()
        builder.button(
            text="✏️ Изменить даты", callback_data=f"absence_edit:{kind}:{raw_id}"
        )
        builder.button(
            text="🗑 Удалить", callback_data=f"absence_delete:{kind}:{raw_id}"
        )
        builder.button(text="✖️ Закрыть", callback_data="ui_close")
        builder.adjust(1)
        await query.message.edit_text(
            event_text(kind, row) + "\n\nВыберите действие:",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("absence_edit:"))
    async def absence_edit(query: CallbackQuery, state: FSMContext) -> None:
        _, kind, raw_id = (query.data or "").split(":")
        row = event_row(kind, int(raw_id))
        current = actor(query.from_user.id)
        if not can_edit_event(current, int(row["employee_id"])):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await state.set_state(AbsenceForm.event_edit)
        await state.set_data({"event_kind": kind, "event_id": int(raw_id)})
        prompt = (
            "Введите новую дату DayOff в формате ДД.ММ.ГГГГ."
            if kind == "dayoff"
            else "Введите даты больничного: ДД.ММ.ГГГГ или ДД.ММ.ГГГГ-ДД.ММ.ГГГГ."
        )
        await query.message.edit_text(prompt)
        await query.answer()

    @router.message(AbsenceForm.event_edit, F.text & ~F.text.startswith("/"))
    async def save_event_edit(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        kind = str(data["event_kind"])
        event_id = int(data["event_id"])
        row = event_row(kind, event_id)
        current = actor(message.from_user.id)
        if not can_edit_event(current, int(row["employee_id"])):
            await state.clear()
            return
        try:
            if kind == "dayoff":
                service.database.update_day_off(
                    event_id, _parse_date(message.text or "")
                )
            else:
                values = (message.text or "").split("-", 1)
                start = _parse_date(values[0])
                end = _parse_date(values[1]) if len(values) == 2 else None
                service.database.update_sick_leave_dates(event_id, start, end)
        except (ValueError, LookupError) as error:
            await message.answer(f"Не удалось изменить событие: {error}")
            return
        await state.clear()
        await message.answer("✅ Событие обновлено. Откройте /my_events для проверки.")

    @router.callback_query(F.data.startswith("absence_delete:"))
    async def absence_delete(query: CallbackQuery) -> None:
        _, kind, raw_id = (query.data or "").split(":")
        row = event_row(kind, int(raw_id))
        current = actor(query.from_user.id)
        if not can_edit_event(current, int(row["employee_id"])):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        builder = InlineKeyboardBuilder()
        builder.button(
            text="Удалить безвозвратно",
            callback_data=f"absence_confirm_delete:{kind}:{raw_id}",
        )
        builder.button(text="✖️ Закрыть", callback_data="ui_close")
        await query.message.edit_text(
            event_text(kind, row) + "\n\nПодтвердите удаление:",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("absence_confirm_delete:"))
    async def absence_confirm_delete(query: CallbackQuery) -> None:
        _, kind, raw_id = (query.data or "").split(":")
        row = event_row(kind, int(raw_id))
        current = actor(query.from_user.id)
        if not can_edit_event(current, int(row["employee_id"])):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        if kind == "dayoff":
            service.database.delete_day_off(int(raw_id))
        else:
            service.database.delete_sick_leave(int(raw_id))
        await query.message.edit_text("✅ Событие удалено.")
        await query.answer()

    @router.callback_query(F.data.startswith("staff_events:"))
    async def staff_events(query: CallbackQuery) -> None:
        current = actor(query.from_user.id)
        employee_id = int((query.data or "").split(":")[1])
        if current is None or not can_edit_event(current, employee_id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        employee = service.database.get_employee(employee_id)
        builder = InlineKeyboardBuilder()
        for vacation in service.database.list_vacations(employee_id=employee_id):
            builder.button(
                text=f"🏖 {vacation.start_date:%d.%m.%Y} — {vacation.end_date:%d.%m.%Y}",
                callback_data=f"vacation_actions:{vacation.id}",
            )
        for row in service.database.list_sick_leaves(employee_id):
            builder.button(
                text=f"🤒 {date.fromisoformat(str(row['start_date'])):%d.%m.%Y}",
                callback_data=f"absence_actions:sick:{row['id']}",
            )
        for row in service.database.list_day_offs(employee_id):
            builder.button(
                text=f"🌿 DayOff · {date.fromisoformat(str(row['day_date'])):%d.%m.%Y}",
                callback_data=f"absence_actions:dayoff:{row['id']}",
            )
        builder.button(text="✖️ Закрыть", callback_data="ui_close")
        builder.adjust(1)
        if not builder.buttons:
            await query.message.edit_text(
                f"{employee.full_name}\n\nМероприятий пока нет."
            )
        else:
            await query.message.edit_text(
                f"📅 <b>{employee.full_name}</b>\n\nВыберите мероприятие для корректировки:",
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
        await query.answer()

    return router
