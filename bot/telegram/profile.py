from __future__ import annotations

import re
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.access import can_manage, visible_contacts
from bot.db import format_display_name, validate_full_name
from bot.service import VacationService


class ProfileForm(StatesGroup):
    waiting_value = State()


FORMAT_LABELS = {"hybrid": "Гибрид", "remote": "Удаленка", "office": "Офис"}
EDITABLE_FIELDS = (
    ("ФИО", "full_name"), ("Дата рождения", "birth_date"),
    ("Телефон", "phone"), ("Email", "email"), ("Email_P", "personal_email"),
    ("Локация", "location"),
    ("Город офиса", "office_city"), ("Формат работы", "work_format"),
    ("Владение английским", "english_level"),
    ("Дата трудоустройства", "employment_date"),
    ("Грейд", "grade"), ("Направление", "direction"),
    ("Боевой проект", "project_name"), ("Дата старта проекта", "project_start_date"),
)


def _buttons(items: list[tuple[str, str]], width: int = 1):
    builder = InlineKeyboardBuilder()
    for text, data in items:
        builder.button(text=text, callback_data=data)
    builder.adjust(width)
    return builder.as_markup()


def _profile_text(profile, *, public: bool = False) -> str:
    lines = [
        format_display_name(profile.full_name),
        f"Грейд: {profile.grade or 'не указан'}",
        f"Направление: {profile.direction or 'не указано'}",
        f"Боевой проект: {profile.project_name or 'не указан'}",
        (f"Старт на проекте: {profile.project_start_date:%d.%m.%Y}"
         if profile.project_start_date else "Старт на проекте: не указан"),
        f"Телефон: {profile.phone or 'не указан'}",
        f"Email: {profile.email or 'не указан'}",
        f"Email_P: {profile.personal_email or 'не указан'}",
        f"Telegram: {profile.telegram_tag or 'без username'}",
        f"Локация пребывания: {profile.location or 'не указана'}",
        f"Город офиса: {profile.office_city or 'не указан'}",
        f"Формат работы: {FORMAT_LABELS.get(profile.work_format, 'не указан')}",
        f"Владение английским: {profile.english_level or 'не указано'}",
        (f"Дата трудоустройства: {profile.employment_date:%d.%m.%Y}"
         if profile.employment_date else "Дата трудоустройства: не указана"),
    ]
    if not public:
        lines.insert(1, f"Дата рождения: {profile.birth_date:%d.%m.%Y}"
                     if profile.birth_date else "Дата рождения: не указана")
    return "\n".join(lines)


def _editor(target_id: int, prefix: str):
    return _buttons([(label, f"{prefix}:{target_id}:{field}")
                     for label, field in EDITABLE_FIELDS], 2)


def create_profile_router(service: VacationService) -> Router:
    router = Router(name="profile")

    def get_actor(telegram_id: int):
        return service.database.get_employee_by_telegram(telegram_id)

    @router.message(Command("profile"))
    async def show_profile(message: Message) -> None:
        if message.from_user is None:
            return
        profile = get_actor(message.from_user.id)
        if profile is None:
            await message.answer("Сначала зарегистрируйтесь через /start.")
            return
        if profile.role == "guest":
            await message.answer("Гостю доступен календарь и контакт назначенного тимлида.")
            return
        await message.answer(_profile_text(profile) + "\n\nВыберите поле:",
                             reply_markup=_editor(profile.id, "profilefield"))

    @router.message(Command("contacts"))
    async def contacts(message: Message) -> None:
        if message.from_user is None:
            return
        actor = get_actor(message.from_user.id)
        if actor is None or not actor.profile_completed:
            await message.answer("Сначала зарегистрируйтесь через /start.")
            return
        employees = visible_contacts(actor, service.database.list_employees())
        if not employees:
            await message.answer("Тимлид не назначен.")
            return
        await message.answer("Контакты сотрудников:", reply_markup=_buttons([
            (item.full_name, f"contact:{item.id}") for item in employees
        ]))

    @router.callback_query(F.data.startswith("contact:"))
    async def show_contact(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        if actor is None or not actor.profile_completed:
            await query.answer("Нет доступа.", show_alert=True)
            return
        employee = service.database.get_employee(int((query.data or "").split(":")[1]))
        if employee not in visible_contacts(actor, service.database.list_employees()):
            await query.answer("Контакт недоступен.", show_alert=True)
            return
        await query.message.edit_text(_profile_text(employee, public=True))
        await query.answer()

    @router.callback_query(F.data.startswith("manage_profile:"))
    async def manage_profile(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        target = service.database.get_employee(int((query.data or "").split(":")[1]))
        if actor is None or (actor.role != "owner" and not actor.is_team_lead) or not can_manage(actor, target):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            _profile_text(target) + "\n\nРедактирование данных:",
            reply_markup=_editor(target.id, "ownerfield"),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("profilefield:") | F.data.startswith("ownerfield:"))
    async def choose_field(query: CallbackQuery, state: FSMContext) -> None:
        prefix, raw_id, field = (query.data or "").split(":")
        actor = get_actor(query.from_user.id)
        target_id = int(raw_id)
        if (
            actor is None
            or actor.role == "guest"
            or not can_manage(actor, service.database.get_employee(target_id))
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        choices = {
            "work_format": [("Гибрид", "hybrid"), ("Удаленка", "remote"), ("Офис", "office")],
            "grade": [(v, v) for v in ("Intern", "Junior", "Middle", "Senior", "RM1")],
            "direction": [(v, v) for v in ("SA", "QA", "DEV", "HR")],
            "project_name": [("Нет проекта", "Нет проекта"), ("Лаба", "Лаба")],
        }
        if field in choices:
            items = [(label, f"setprofile:{target_id}:{field}:{value}")
                     for label, value in choices[field]]
            if field == "project_name":
                items.append(("Ввести вручную", f"manualprofile:{target_id}:{field}"))
            await query.message.answer("Выберите значение:", reply_markup=_buttons(items, 3))
            await query.answer()
            return
        prompts = {
            "full_name": "Введите ФИО:", "birth_date": "Введите дату рождения ДД.ММ.ГГГГ:",
            "phone": "Введите телефон:", "email": "Введите Email:",
            "personal_email": "Введите Email_P:",
            "english_level": "Укажите уровень владения английским:",
            "employment_date": "Введите дату трудоустройства ДД.ММ.ГГГГ:",
            "location": "Введите локацию пребывания:",
            "office_city": "Введите город офиса:",
            "project_start_date": "Введите дату старта на проекте ДД.ММ.ГГГГ:",
        }
        await state.set_state(ProfileForm.waiting_value)
        await state.set_data({"target_id": target_id, "profile_field": field,
                              "owner_edit": prefix == "ownerfield"})
        await query.message.answer(prompts[field])
        await query.answer()

    @router.callback_query(F.data.startswith("manualprofile:"))
    async def manual_field(query: CallbackQuery, state: FSMContext) -> None:
        _, raw_id, field = (query.data or "").split(":")
        actor = get_actor(query.from_user.id)
        target_id = int(raw_id)
        if (
            actor is None
            or actor.role == "guest"
            or not can_manage(actor, service.database.get_employee(target_id))
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await state.set_state(ProfileForm.waiting_value)
        await state.set_data({"target_id": target_id, "profile_field": field})
        await query.message.answer("Введите название проекта:")
        await query.answer()

    @router.callback_query(F.data.startswith("setprofile:"))
    async def set_choice(query: CallbackQuery) -> None:
        _, raw_id, field, value = (query.data or "").split(":", 3)
        actor = get_actor(query.from_user.id)
        target_id = int(raw_id)
        if (
            actor is None
            or actor.role == "guest"
            or not can_manage(actor, service.database.get_employee(target_id))
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        service.database.update_profile(target_id, **{field: value})
        await query.message.edit_text("Данные сотрудника обновлены.")
        await query.answer()

    @router.message(ProfileForm.waiting_value)
    async def save_field(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        data = await state.get_data()
        actor = get_actor(message.from_user.id)
        target_id = int(data["target_id"])
        if (
            actor is None
            or actor.role == "guest"
            or not can_manage(actor, service.database.get_employee(target_id))
        ):
            await state.clear()
            return
        field = str(data["profile_field"])
        value = (message.text or "").strip()
        try:
            if not value:
                raise ValueError
            kwargs = {field: value}
            if field == "full_name":
                kwargs[field] = validate_full_name(value)
            elif field in {"birth_date", "project_start_date", "employment_date"}:
                parsed = datetime.strptime(value, "%d.%m.%Y").date()
                if field == "birth_date" and parsed >= date.today():
                    raise ValueError
                kwargs[field] = parsed
            elif field == "phone" and not re.fullmatch(r"\+?[0-9 ()-]{7,20}", value):
                raise ValueError
            elif field in {"email", "personal_email"} and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
                raise ValueError
            service.database.update_profile(target_id, **kwargs)
        except ValueError:
            await message.answer("Некорректное значение. Попробуйте еще раз.")
            return
        await state.clear()
        await message.answer("Данные сотрудника обновлены.")

    return router