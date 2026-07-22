from __future__ import annotations

import re
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.db import format_display_name, validate_full_name
from bot.service import VacationService


class GuestForm(StatesGroup):
    filling = State()


class NotificationForm(StatesGroup):
    waiting_datetime = State()
    waiting_text = State()
    waiting_repeat_count = State()
    waiting_edit_count = State()


def _buttons(items: list[tuple[str, str]], width: int = 1):
    builder = InlineKeyboardBuilder()
    for text, data in items:
        builder.button(text=text, callback_data=data)
    builder.adjust(width)
    return builder.as_markup()


def create_owner_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="owner")

    def actor(telegram_id: int):
        return service.database.get_employee_by_telegram(telegram_id)

    def is_owner(telegram_id: int) -> bool:
        employee = actor(telegram_id)
        return employee is not None and employee.role == "owner"

    @router.message(Command("staff"))
    async def staff(message: Message) -> None:
        if message.from_user is None or not is_owner(message.from_user.id):
            await message.answer("Команда доступна только владельцу продукта.")
            return
        employees = service.database.list_employees()
        await message.answer(
            "🗂 <b>Все сотрудники</b>\n\nВыберите запись для полного управления:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (
                        f"{'' if item.role == 'owner' else '⭐ ' if item.is_team_lead else ''}"
                        f"{format_display_name(item.full_name)}",
                        f"employee:{item.id}",
                    )
                    for item in employees
                ]
            ),
        )

    @router.message(Command("guest"))
    async def create_guest(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        current = actor(message.from_user.id)
        if current is None or (current.role != "owner" and not current.is_team_lead):
            await message.answer("Гостей могут создавать владелец и руководители.")
            return
        leads = (
            [current]
            if current.role != "owner"
            else [
                item
                for item in service.database.list_employees()
                if item.is_team_lead and item.role != "guest"
            ]
        )
        if not leads:
            await message.answer("Сначала назначьте хотя бы одного руководителя.")
            return
        await state.set_state(GuestForm.filling)
        await state.set_data({"guest_step": "lead"})
        await message.answer(
            "👤 <b>Новый гость · шаг 1 из 6</b>\n\nВыберите руководителя:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (
                        format_display_name(item.full_name),
                        f"guest_lead:{item.id}",
                    )
                    for item in leads
                ]
            ),
        )

    @router.callback_query(GuestForm.filling, F.data.startswith("guest_lead:"))
    async def guest_lead(query: CallbackQuery, state: FSMContext) -> None:
        lead_id = int((query.data or "").split(":")[1])
        lead = service.database.get_employee(lead_id)
        current = actor(query.from_user.id)
        if (
            current is None
            or not lead.is_team_lead
            or (current.role != "owner" and current.id != lead.id)
        ):
            await query.answer("Руководитель недоступен.", show_alert=True)
            return
        await state.update_data(team_lead_id=lead_id, guest_step="name")
        await query.message.edit_text(
            "👤 <b>Новый гость · шаг 2 из 6</b>\n\nВведите ФИО:",
            parse_mode="HTML",
        )
        await query.answer()

    @router.message(GuestForm.filling, F.text & ~F.text.startswith("/"))
    async def guest_value(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        current = actor(message.from_user.id)
        if current is None or (current.role != "owner" and not current.is_team_lead):
            await state.clear()
            return
        data = await state.get_data()
        step = data.get("guest_step")
        value = (message.text or "").strip()
        try:
            if step == "name":
                await state.update_data(
                    full_name=validate_full_name(value), guest_step="phone"
                )
                await message.answer(
                    "☎️ <b>Новый гость · шаг 3 из 6</b>\n\nВведите телефон:",
                    parse_mode="HTML",
                )
            elif step == "phone":
                if not re.fullmatch(r"\+?[0-9 ()-]{7,20}", value):
                    raise ValueError
                await state.update_data(phone=value, guest_step="location")
                await message.answer(
                    "📍 <b>Новый гость · шаг 4 из 6</b>\n\nВведите локацию пребывания:",
                    parse_mode="HTML",
                )
            elif step == "location":
                if not value:
                    raise ValueError
                await state.update_data(location=value, guest_step="grade")
                await message.answer(
                    "🎯 <b>Новый гость · шаг 5 из 6</b>\n\nВыберите грейд:",
                    parse_mode="HTML",
                    reply_markup=_buttons(
                        [
                            (item, f"guest_grade:{item}")
                            for item in ("Intern", "Junior", "Middle", "Senior", "RM1")
                        ],
                        2,
                    ),
                )
            elif step == "email":
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
                    raise ValueError
                guest = service.register_employee(str(data["full_name"]))
                guest = service.database.update_profile(
                    guest.id,
                    phone=str(data["phone"]),
                    location=str(data["location"]),
                    grade=str(data["grade"]),
                    direction=str(data["direction"]),
                    email=value,
                )
                guest = service.database.update_employee(
                    guest.id,
                    role="guest",
                    team_lead_id=int(data["team_lead_id"]),
                    set_team_lead=True,
                )
                await state.clear()
                lead = service.database.get_employee(int(data["team_lead_id"]))
                await message.answer(
                    "✅ <b>Гость создан</b>\n\n"
                    f"{escape(guest.full_name)}\n"
                    f"Руководитель: {escape(format_display_name(lead.full_name))}",
                    parse_mode="HTML",
                )
            else:
                await message.answer("Используйте предложенные кнопки.")
        except (ValueError, LookupError) as error:
            await message.answer(
                f"Некорректное значение. {escape(str(error)) if str(error) else 'Попробуйте ещё раз.'}"
            )

    @router.callback_query(GuestForm.filling, F.data.startswith("guest_grade:"))
    async def guest_grade(query: CallbackQuery, state: FSMContext) -> None:
        grade = (query.data or "").split(":")[1]
        await state.update_data(grade=grade, guest_step="direction")
        await query.message.edit_text(
            "🧭 <b>Новый гость · шаг 6 из 6</b>\n\nВыберите направление:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (item, f"guest_direction:{item}")
                    for item in ("SA", "QA", "DEV", "HR")
                ],
                2,
            ),
        )
        await query.answer()

    @router.callback_query(GuestForm.filling, F.data.startswith("guest_direction:"))
    async def guest_direction(query: CallbackQuery, state: FSMContext) -> None:
        direction = (query.data or "").split(":")[1]
        await state.update_data(direction=direction, guest_step="email")
        await query.message.edit_text(
            "✉️ <b>Завершение регистрации</b>\n\nВведите рабочий Email:",
            parse_mode="HTML",
        )
        await query.answer()

    async def notification_menu(message: Message) -> None:
        await message.answer(
            "🔔 <b>Нотификации</b>\n\nВыберите действие:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("➕ Создать", "notification_create"),
                    ("📋 Запланированные", "notification_list"),
                ]
            ),
        )

    @router.message(Command("notifications"))
    async def notifications(message: Message) -> None:
        if message.from_user is None or not is_owner(message.from_user.id):
            await message.answer("Команда доступна только владельцу продукта.")
            return
        await notification_menu(message)

    @router.callback_query(F.data == "notification_create")
    async def notification_create(query: CallbackQuery) -> None:
        if not is_owner(query.from_user.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            "👥 <b>Получатели</b>\n\nВыберите аудиторию:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("Все пользователи", "notification_scope:all"),
                    ("Сотрудники и руководители", "notification_scope:employees"),
                    ("Гости", "notification_scope:guests"),
                    ("Определённая команда", "notification_scope:team"),
                    ("Выбрать сотрудников", "notification_scope:people"),
                ]
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("notification_scope:"))
    async def notification_scope(query: CallbackQuery, state: FSMContext) -> None:
        if not is_owner(query.from_user.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        scope = (query.data or "").split(":")[1]
        if scope == "people":
            await state.clear()
            await state.update_data(notification_people=[])
            employees = [
                item
                for item in service.database.list_employees()
                if item.telegram_user_id is not None
            ]
            await query.message.edit_text(
                "👤 <b>Точечные получатели</b>\n\n"
                "Нажимайте на сотрудников для выбора, затем «Готово»:",
                parse_mode="HTML",
                reply_markup=_buttons(
                    [
                        (
                            format_display_name(item.full_name),
                            f"notification_person:{item.id}",
                        )
                        for item in employees
                    ]
                    + [("✅ Готово", "notification_people_done")]
                ),
            )
            await query.answer()
            return
        if scope == "team":
            teams = service.database.list_teams()
            await query.message.edit_text(
                "Выберите команду:",
                reply_markup=_buttons(
                    [(team.name, f"notification_team:{team.id}") for team in teams]
                ),
            )
            await query.answer()
            return
        roles = {
            "all": ("owner", "team_lead", "employee", "guest"),
            "employees": ("team_lead", "employee"),
            "guests": ("guest",),
        }[scope]
        await state.set_state(NotificationForm.waiting_datetime)
        await state.set_data({"notification_roles": roles})
        await query.message.edit_text(
            "📅 Введите дату и время первой отправки: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>",
            parse_mode="HTML",
        )
        await query.answer()

    @router.callback_query(F.data.startswith("notification_person:"))
    async def notification_person(query: CallbackQuery, state: FSMContext) -> None:
        employee_id = int((query.data or "").split(":")[1])
        data = await state.get_data()
        selected = {int(item) for item in data.get("notification_people", [])}
        if employee_id in selected:
            selected.remove(employee_id)
        else:
            selected.add(employee_id)
        await state.update_data(notification_people=sorted(selected))
        employees = [
            item
            for item in service.database.list_employees()
            if item.telegram_user_id is not None
        ]
        await query.message.edit_reply_markup(
            reply_markup=_buttons(
                [
                    (
                        f"{'✅ ' if item.id in selected else ''}"
                        f"{format_display_name(item.full_name)}",
                        f"notification_person:{item.id}",
                    )
                    for item in employees
                ]
                + [(f"✅ Готово · выбрано {len(selected)}", "notification_people_done")]
            )
        )
        await query.answer(
            "Получатель выбран" if employee_id in selected else "Получатель исключён"
        )

    @router.callback_query(F.data == "notification_people_done")
    async def notification_people_done(query: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        selected = tuple(int(item) for item in data.get("notification_people", []))
        if not selected:
            await query.answer("Выберите хотя бы одного сотрудника.", show_alert=True)
            return
        await state.set_state(NotificationForm.waiting_datetime)
        await state.update_data(
            notification_roles=(),
            notification_employee_ids=selected,
        )
        await query.message.edit_text(
            f"👤 Выбрано получателей: <b>{len(selected)}</b>\n\n"
            "Введите дату и время первой отправки: "
            "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>",
            parse_mode="HTML",
        )
        await query.answer()

    @router.callback_query(F.data.startswith("notification_team:"))
    async def notification_team(query: CallbackQuery, state: FSMContext) -> None:
        team_id = int((query.data or "").split(":")[1])
        service.database.get_team(team_id)
        await state.set_state(NotificationForm.waiting_datetime)
        await state.set_data(
            {"notification_roles": (), "notification_team_id": team_id}
        )
        await query.message.edit_text(
            "📅 Введите дату и время первой отправки: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>",
            parse_mode="HTML",
        )
        await query.answer()

    @router.message(NotificationForm.waiting_datetime, F.text & ~F.text.startswith("/"))
    async def notification_datetime(message: Message, state: FSMContext) -> None:
        try:
            value = datetime.strptime((message.text or "").strip(), "%d.%m.%Y %H:%M")
            if value <= datetime.now():
                raise ValueError
        except ValueError:
            await message.answer("Введите будущую дату в формате ДД.ММ.ГГГГ ЧЧ:ММ.")
            return
        await state.update_data(notification_at=value.isoformat(timespec="minutes"))
        await state.set_state(NotificationForm.waiting_text)
        await message.answer("✍️ Введите текст нотификации:")

    @router.message(NotificationForm.waiting_text, F.text & ~F.text.startswith("/"))
    async def notification_text(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Текст не может быть пустым.")
            return
        await state.update_data(notification_text=text)
        await message.answer(
            "🔁 <b>Периодичность</b>\n\nВыберите интервал:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("Один раз", "notification_interval:0"),
                    ("Каждый час", "notification_interval:60"),
                    ("Каждый день", "notification_interval:1440"),
                    ("Каждую неделю", "notification_interval:10080"),
                ],
                2,
            ),
        )

    async def save_notification(
        message: Message, state: FSMContext, count: int, interval: int | None
    ):
        data = await state.get_data()
        owner = actor(message.chat.id)
        if owner is None:
            await state.clear()
            return
        notification = service.database.add_scheduled_notification(
            datetime.fromisoformat(str(data["notification_at"])),
            str(data["notification_text"]),
            owner.id,
            tuple(data.get("notification_roles", ())),
            target_team_id=(
                int(data["notification_team_id"])
                if data.get("notification_team_id") is not None
                else None
            ),
            recipient_employee_ids=tuple(
                int(item) for item in data.get("notification_employee_ids", ())
            ),
            repeat_interval_minutes=interval,
            repeat_count=count,
        )
        await state.clear()
        await message.answer(
            f"✅ <b>Нотификация #{notification.id} создана</b>\n\n"
            f"Первая отправка: <code>{notification.scheduled_at:%d.%m.%Y %H:%M}</code>\n"
            f"Отправок: <code>{count}</code>",
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith("notification_interval:"))
    async def notification_interval(query: CallbackQuery, state: FSMContext) -> None:
        interval = int((query.data or "").split(":")[1])
        if interval == 0:
            await save_notification(query.message, state, 1, None)
        else:
            await state.update_data(notification_interval=interval)
            await state.set_state(NotificationForm.waiting_repeat_count)
            await query.message.edit_text(
                "🔁 Введите общее количество отправок, включая первую:"
            )
        await query.answer()

    @router.message(
        NotificationForm.waiting_repeat_count, F.text & ~F.text.startswith("/")
    )
    async def notification_repeat_count(message: Message, state: FSMContext) -> None:
        try:
            count = int((message.text or "").strip())
            if count < 2 or count > 100:
                raise ValueError
        except ValueError:
            await message.answer("Введите число от 2 до 100.")
            return
        data = await state.get_data()
        await save_notification(
            message,
            state,
            count,
            int(data["notification_interval"]),
        )

    @router.callback_query(F.data == "notification_list")
    async def notification_list(query: CallbackQuery) -> None:
        if not is_owner(query.from_user.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        items = service.database.list_scheduled_notifications(pending_only=True)
        if not items:
            await query.message.edit_text("Запланированных нотификаций нет.")
            await query.answer()
            return
        await query.message.edit_text(
            "📋 <b>Запланированные нотификации</b>\n\nВыберите запись:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (
                        f"#{item.id} · {item.scheduled_at:%d.%m %H:%M} · "
                        f"{item.repeats_remaining} отправ.",
                        f"notification_view:{item.id}",
                    )
                    for item in items
                ]
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("notification_view:"))
    async def notification_view(query: CallbackQuery) -> None:
        item = service.database.get_scheduled_notification(
            int((query.data or "").split(":")[1])
        )
        interval = (
            f"{item.repeat_interval_minutes} мин."
            if item.repeat_interval_minutes
            else "однократно"
        )
        await query.message.edit_text(
            f"🔔 <b>Нотификация #{item.id}</b>\n\n"
            f"Следующая: <code>{item.scheduled_at:%d.%m.%Y %H:%M}</code>\n"
            f"Повторы: {item.repeats_remaining}, интервал: {interval}\n\n"
            f"{escape(item.message_text)}",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("🔁 Настроить повторы", f"notification_settings:{item.id}"),
                    ("🗑 Удалить", f"notification_delete:{item.id}"),
                ]
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("notification_settings:"))
    async def notification_settings(query: CallbackQuery) -> None:
        notification_id = int((query.data or "").split(":")[1])
        await query.message.edit_text(
            "Выберите новый интервал:",
            reply_markup=_buttons(
                [
                    ("Один раз", f"notification_edit_interval:{notification_id}:0"),
                    ("Каждый час", f"notification_edit_interval:{notification_id}:60"),
                    (
                        "Каждый день",
                        f"notification_edit_interval:{notification_id}:1440",
                    ),
                    (
                        "Каждую неделю",
                        f"notification_edit_interval:{notification_id}:10080",
                    ),
                ],
                2,
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("notification_edit_interval:"))
    async def notification_edit_interval(
        query: CallbackQuery, state: FSMContext
    ) -> None:
        _, raw_id, raw_interval = (query.data or "").split(":")
        interval = int(raw_interval)
        if interval == 0:
            service.database.update_notification_schedule(int(raw_id), None, 1)
            await query.message.edit_text("✅ Нотификация настроена на одну отправку.")
        else:
            await state.set_state(NotificationForm.waiting_edit_count)
            await state.set_data(
                {
                    "edit_notification_id": int(raw_id),
                    "edit_notification_interval": interval,
                }
            )
            await query.message.edit_text(
                "Введите новое количество отправок от 2 до 100:"
            )
        await query.answer()

    @router.message(
        NotificationForm.waiting_edit_count, F.text & ~F.text.startswith("/")
    )
    async def notification_edit_count(message: Message, state: FSMContext) -> None:
        try:
            count = int((message.text or "").strip())
            if count < 2 or count > 100:
                raise ValueError
        except ValueError:
            await message.answer("Введите число от 2 до 100.")
            return
        data = await state.get_data()
        service.database.update_notification_schedule(
            int(data["edit_notification_id"]),
            int(data["edit_notification_interval"]),
            count,
        )
        await state.clear()
        await message.answer("✅ Настройки повторов обновлены.")

    @router.callback_query(F.data.startswith("notification_delete:"))
    async def notification_delete(query: CallbackQuery) -> None:
        notification_id = int((query.data or "").split(":")[1])
        await query.message.edit_text(
            f"Удалить нотификацию #{notification_id}?",
            reply_markup=_buttons(
                [
                    ("Удалить", f"notification_confirm_delete:{notification_id}"),
                    ("Отмена", "notification_list"),
                ]
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("notification_confirm_delete:"))
    async def notification_confirm_delete(query: CallbackQuery) -> None:
        notification_id = int((query.data or "").split(":")[1])
        removed = service.database.cancel_scheduled_notification(notification_id)
        await query.message.edit_text(
            "✅ Нотификация удалена." if removed else "Активная нотификация не найдена."
        )
        await query.answer()

    return router
