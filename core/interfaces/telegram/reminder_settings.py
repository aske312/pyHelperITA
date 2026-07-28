from __future__ import annotations

from datetime import time
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.access import can_manage
from core.config import Settings
from core.service import VacationService


class ReminderEditor(StatesGroup):
    waiting_time = State()
    waiting_text = State()


def _buttons(rows):
    builder = InlineKeyboardBuilder()
    for text, data in rows:
        builder.button(text=text, callback_data=data)
    builder.adjust(1)
    return builder.as_markup()


def create_reminder_settings_router(
    service: VacationService, settings: Settings
) -> Router:
    router = Router()

    def actor(telegram_id: int):
        return service.database.get_employee_by_telegram_id(telegram_id)

    def accessible(current):
        return [
            item
            for item in service.database.list_employees()
            if item.is_active and can_manage(current, item)
        ]

    async def show_target(message, target_id: int) -> None:
        target = service.database.get_employee(target_id)
        current = actor(message.chat.id)
        if current is None or not can_manage(current, target):
            await message.answer("Недостаточно прав для настройки этих напоминаний.")
            return
        value = service.database.get_reminder_settings(
            target.id,
            settings.default_reminder_days,
            settings.default_reminder_time,
            settings.default_reminder_text,
        )
        state = "включены ✅" if value.enabled else "выключены ⛔"
        await message.answer(
            f"⏰ <b>Напоминания: {escape(target.full_name)}</b>\n\n"
            f"Состояние: <b>{state}</b>\n"
            f"За сколько дней: <b>{value.days_before}</b>\n"
            f"Время: <b>{value.reminder_time:%H:%M}</b>\n"
            f"Текст: <code>{escape(value.text_template)}</code>\n\n"
            "Поля шаблона: {employee_name}, {start_date}, {end_date}, {days_count}",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("Включить / выключить", f"rem_toggle:{target.id}"),
                    ("За 0 дней", f"rem_days:{target.id}:0"),
                    ("За 1 день", f"rem_days:{target.id}:1"),
                    ("За 7 дней", f"rem_days:{target.id}:7"),
                    ("За 14 дней", f"rem_days:{target.id}:14"),
                    ("🕘 Изменить время", f"rem_time:{target.id}"),
                    ("✏️ Изменить текст", f"rem_text:{target.id}"),
                    ("↩️ К получателям", "reminder_menu"),
                ]
            ),
        )

    async def menu(message: Message) -> None:
        current = actor(message.chat.id)
        if current is None:
            await message.answer("Сначала пройдите регистрацию.")
            return
        targets = accessible(current)
        scope = "только вы"
        if current.role == "owner":
            scope = "вы, команды и любой пользователь"
        elif current.is_team_lead:
            scope = "вы и ваша команда"
        await message.answer(
            f"⏰ <b>Настройки напоминаний</b>\n\nДоступная область: {scope}.\n"
            "Выберите, для кого изменить стандартное напоминание об отпуске:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (
                        ("👤 Я" if item.id == current.id else f"• {item.full_name}"),
                        f"rem_target:{item.id}",
                    )
                    for item in targets
                ]
            ),
        )

    @router.message(Command("reminders"))
    async def reminders(message: Message) -> None:
        await menu(message)

    @router.callback_query(F.data == "reminder_menu")
    async def reminder_menu(query: CallbackQuery) -> None:
        await menu(query.message)
        await query.answer()

    @router.callback_query(F.data.startswith("rem_target:"))
    async def target(query: CallbackQuery) -> None:
        await show_target(query.message, int((query.data or "").split(":")[1]))
        await query.answer()

    def current_value(target_id: int):
        return service.database.get_reminder_settings(
            target_id,
            settings.default_reminder_days,
            settings.default_reminder_time,
            settings.default_reminder_text,
        )

    def allowed(query: CallbackQuery, target_id: int) -> bool:
        current = actor(query.from_user.id)
        target_employee = service.database.get_employee(target_id)
        return current is not None and can_manage(current, target_employee)

    @router.callback_query(F.data.startswith("rem_toggle:"))
    async def toggle(query: CallbackQuery) -> None:
        target_id = int((query.data or "").split(":")[1])
        if not allowed(query, target_id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        value = current_value(target_id)
        service.set_reminder(
            target_id, value.days_before, value.reminder_time,
            value.text_template, not value.enabled,
        )
        await show_target(query.message, target_id)
        await query.answer("Настройка сохранена")

    @router.callback_query(F.data.startswith("rem_days:"))
    async def days(query: CallbackQuery) -> None:
        _, raw_id, raw_days = (query.data or "").split(":")
        target_id = int(raw_id)
        if not allowed(query, target_id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        value = current_value(target_id)
        service.set_reminder(
            target_id, int(raw_days), value.reminder_time,
            value.text_template, value.enabled,
        )
        await show_target(query.message, target_id)
        await query.answer("Срок сохранён")

    @router.callback_query(F.data.startswith("rem_time:"))
    async def edit_time(query: CallbackQuery, state: FSMContext) -> None:
        target_id = int((query.data or "").split(":")[1])
        if not allowed(query, target_id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await state.set_state(ReminderEditor.waiting_time)
        await state.set_data({"reminder_target_id": target_id})
        await query.message.answer("Введите время отправки в формате ЧЧ:ММ:")
        await query.answer()

    @router.message(ReminderEditor.waiting_time, F.text)
    async def save_time(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        target_id = int(data["reminder_target_id"])
        current = actor(message.from_user.id)
        target_employee = service.database.get_employee(target_id)
        if current is None or not can_manage(current, target_employee):
            await state.clear()
            return
        try:
            parsed = time.fromisoformat((message.text or "").strip())
        except ValueError:
            await message.answer("Некорректное время. Используйте формат ЧЧ:ММ.")
            return
        value = current_value(target_id)
        service.set_reminder(
            target_id, value.days_before, parsed, value.text_template, value.enabled
        )
        await state.clear()
        await message.answer("✅ Время сохранено.")
        await show_target(message, target_id)

    @router.callback_query(F.data.startswith("rem_text:"))
    async def edit_text(query: CallbackQuery, state: FSMContext) -> None:
        target_id = int((query.data or "").split(":")[1])
        if not allowed(query, target_id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await state.set_state(ReminderEditor.waiting_text)
        await state.set_data({"reminder_target_id": target_id})
        await query.message.answer("Введите новый текст напоминания:")
        await query.answer()

    @router.message(ReminderEditor.waiting_text, F.text)
    async def save_text(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        target_id = int(data["reminder_target_id"])
        current = actor(message.from_user.id)
        target_employee = service.database.get_employee(target_id)
        if current is None or not can_manage(current, target_employee):
            await state.clear()
            return
        value = current_value(target_id)
        try:
            service.set_reminder(
                target_id, value.days_before, value.reminder_time,
                message.text or "", value.enabled,
            )
        except ValueError as error:
            await message.answer(str(error))
            return
        await state.clear()
        await message.answer("✅ Текст сохранён.")
        await show_target(message, target_id)

    return router
