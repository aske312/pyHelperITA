from __future__ import annotations

from datetime import date, datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.access import can_manage, visible_contacts
from core.config import get_settings
from core.db import format_display_name, validate_full_name
from core.directories import (
    Directories,
    validate_city,
    validate_email,
    validate_employee_id,
    validate_phone,
)
from core.service import VacationService


class ProfileForm(StatesGroup):
    waiting_value = State()


FORMAT_LABELS = {"hybrid": "Гибрид", "remote": "Удаленка", "office": "Офис"}
REFERENCE_DIRECTORIES = Directories.load(
    get_settings().directories_path
)
EDITABLE_FIELDS = (
    ("ФИО", "full_name"),
    ("Дата рождения", "birth_date"),
    ("Телефон", "phone"),
    ("Email", "email"),
    ("Личный Email", "personal_email"),
    ("Город", "location"),
    ("Офис", "office_city"),
    ("Формат работы", "work_format"),
    ("Владение английским", "english_level"),
    ("Дата трудоустройства", "employment_date"),
    ("Грейд", "grade"),
    ("Направление", "direction"),
    ("Боевой проект", "project_name"),
    ("Дата старта проекта", "project_start_date"),
)
MANAGED_EDITABLE_FIELDS = tuple(
    item
    for item in EDITABLE_FIELDS
    if item[1]
    not in {
        "birth_date",
        "phone",
        "personal_email",
        "direction",
        "employment_date",
    }
)


def _buttons(items: list[tuple[str, str]], width: int = 1):
    builder = InlineKeyboardBuilder()
    for text, data in items:
        builder.button(text=text, callback_data=data)
    builder.button(text="✖️ Закрыть", callback_data="ui_close")
    builder.adjust(width)
    return builder.as_markup()


def _value(value: object | None, fallback: str = "не указано") -> str:
    return escape(str(value)) if value else f"<i>{fallback}</i>"


def _profile_text(
    profile,
    *,
    details: bool = False,
    public: bool = False,
    manager=None,
    mentor=None,
    show_relations: bool = False,
    status: str | None = None,
) -> str:
    full_title = escape(profile.full_name)
    status_line = f"\n📍 <b>Статус:</b> <code>{escape(status)}</code>" if status else ""
    short_title = escape(format_display_name(profile.full_name))
    if not details:
        return (
            f"👤 <b>{full_title}</b>{status_line}\n"
            f"Кратко: <b>{short_title}</b>\n\n"
            f"🎯 <b>Грейд:</b> {_value(profile.grade, 'не указан')}\n"
            f"🧭 <b>Направление:</b> {_value(profile.direction)}\n"
            f"⚔️ <b>Боевой проект:</b> {_value(profile.project_name, 'не указан')}\n"
            f"📍 <b>Город:</b> {_value(profile.location, 'не указан')}\n\n"
            f"☎️ <b>Телефон:</b> {_value(profile.phone, 'не указан')}\n"
            f"✉️ <b>Рабочая почта:</b> {_value(profile.email, 'не указана')}\n"
            f"💬 <b>Telegram:</b> {_value(profile.telegram_tag, 'username не задан')}"
        )
    lines = [
        f"📋 <b>Дополнительная информация</b>\n<b>{full_title}</b>{status_line}\n",
        f"🏢 <b>Офис:</b> {_value(profile.office_city, 'не указан')}",
        f"💼 <b>Формат работы:</b> {_value(FORMAT_LABELS.get(profile.work_format), 'не указан')}",
        f"🌐 <b>Английский:</b> {_value(REFERENCE_DIRECTORIES.label_english(profile.english_level))}",
        (
            f"📅 <b>Дата трудоустройства:</b> <code>{profile.employment_date:%d.%m.%Y}</code>"
            if profile.employment_date
            else "📅 <b>Дата трудоустройства:</b> <i>не указана</i>"
        ),
        (
            f"🚀 <b>Старт на проекте:</b> <code>{profile.project_start_date:%d.%m.%Y}</code>"
            if profile.project_start_date
            else "🚀 <b>Старт на проекте:</b> <i>не указан</i>"
        ),
        f"📨 <b>Личная почта:</b> {_value(profile.personal_email, 'не указана')}",
    ]
    if not public:
        birthday = (
            f"<code>{profile.birth_date:%d.%m.%Y}</code>"
            if profile.birth_date
            else "<i>не указана</i>"
        )
        lines.insert(1, f"🎂 <b>Дата рождения:</b> {birthday}")
    if show_relations:
        if manager is not None:
            lines.append(f"👔 <b>Руководитель:</b> {escape(manager.full_name)}")
        else:
            lines.append("👔 <b>Руководитель:</b> <i>не назначен</i>")
        if mentor is not None:
            lines.append(f"🎓 <b>Ментор:</b> {escape(mentor.full_name)}")
        else:
            lines.append("🎓 <b>Ментор:</b> <i>не назначен</i>")
    return "\n".join(lines)


def _editor(target_id: int, prefix: str, *, owner: bool = False):
    fields = EDITABLE_FIELDS if prefix == "profilefield" else MANAGED_EDITABLE_FIELDS
    items = [(label, f"{prefix}:{target_id}:{field}") for label, field in fields]
    if owner and prefix == "ownerfield":
        items.append(("ID сотрудника", f"{prefix}:{target_id}:id"))
    return _buttons(items, 2)


def _managed_profile_text(profile) -> str:
    work_format = FORMAT_LABELS.get(profile.work_format, profile.work_format)
    project_start = (
        f"<code>{profile.project_start_date:%d.%m.%Y}</code>"
        if profile.project_start_date
        else "<i>не указана</i>"
    )
    return "\n".join(
        (
            "✏️ <b>Редактирование данных сотрудника</b>",
            f"🆔 <b>ID:</b> <code>{profile.id}</code>",
            f"👤 <b>ФИО:</b> {_value(profile.full_name)}",
            f"✉️ <b>Рабочая почта:</b> {_value(profile.email, 'не указана')}",
            f"📍 <b>Город:</b> {_value(profile.location, 'не указан')}",
            f"🏢 <b>Офис:</b> {_value(profile.office_city, 'не указан')}",
            f"💼 <b>Формат работы:</b> {_value(work_format, 'не указан')}",
            f"🌐 <b>Английский:</b> {_value(REFERENCE_DIRECTORIES.label_english(profile.english_level))}",
            f"🎯 <b>Грейд:</b> {_value(profile.grade)}",
            f"⚔️ <b>Боевой проект:</b> {_value(profile.project_name)}",
            f"🚀 <b>Старт на проекте:</b> {project_start}",
            "\nВыберите поле для изменения:",
        )
    )


def _profile_actions(target_id: int, *, own: bool, public: bool = False):
    items = [("📋 Подробнее", f"profile_more:{target_id}:{int(public)}")]
    if own:
        items.append(("✏️ Изменить данные", f"profile_edit:{target_id}"))
    return _buttons(items, 1)


def create_profile_router(service: VacationService) -> Router:
    router = Router(name="profile")
    directories = Directories.load(service.settings.directories_path)

    def get_actor(telegram_id: int):
        return service.database.get_employee_by_telegram(telegram_id)

    def render_profile(profile, **kwargs) -> str:
        return _profile_text(
            profile,
            status=service.database.employee_presence_status(profile.id),
            **kwargs,
        )

    @router.message(Command("profile"))
    async def show_profile(message: Message) -> None:
        if message.from_user is None:
            return
        profile = get_actor(message.from_user.id)
        if profile is None:
            await message.answer("Сначала зарегистрируйтесь через /start.")
            return
        await message.answer(
            render_profile(profile),
            parse_mode="HTML",
            reply_markup=_profile_actions(profile.id, own=True),
        )

    @router.message(Command("contacts"))
    async def contacts(message: Message) -> None:
        if message.from_user is None:
            return
        actor = get_actor(message.from_user.id)
        if actor is None or not actor.profile_completed:
            await message.answer("Сначала зарегистрируйтесь через /start.")
            return
        if actor.role == "guest":
            await message.answer(
                "⛔ Контакты сотрудников недоступны гостевому профилю."
            )
            return
        employees = visible_contacts(actor, service.database.list_employees())
        if not employees:
            await message.answer("Руководитель не назначен.")
            return
        await message.answer(
            "👥 <b>Контакты сотрудников</b>\n\nВыберите сотрудника:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (
                        f"{item.full_name} · {status}"
                        if (
                            status := service.database.employee_presence_status(item.id)
                        )
                        else item.full_name,
                        f"contact:{item.id}",
                    )
                    for item in employees
                ]
            ),
        )

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
        await query.message.edit_text(
            render_profile(employee, public=True),
            parse_mode="HTML",
            reply_markup=_profile_actions(employee.id, own=False, public=True),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("profile_more:"))
    async def show_profile_details(query: CallbackQuery) -> None:
        _, raw_id, raw_public = (query.data or "").split(":")
        target = service.database.get_employee(int(raw_id))
        actor = get_actor(query.from_user.id)
        public = bool(int(raw_public))
        allowed = actor is not None and (
            target.id == actor.id
            or can_manage(actor, target)
            or target in visible_contacts(actor, service.database.list_employees())
        )
        if not allowed:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        manager = (
            service.database.get_employee(target.team_lead_id)
            if target.team_lead_id is not None
            else None
        )
        mentor = (
            service.database.get_employee(target.mentor_id)
            if target.mentor_id is not None
            else None
        )
        actions = [("← Основная информация", f"profile_main:{target.id}:{int(public)}")]
        if service.settings.profile_relations:
            if manager is not None:
                actions.append(
                    (
                        f"👔 {format_display_name(manager.full_name)}",
                        f"contact:{manager.id}",
                    )
                )
            if mentor is not None:
                actions.append(
                    (
                        f"🎓 {format_display_name(mentor.full_name)}",
                        f"contact:{mentor.id}",
                    )
                )
        await query.message.edit_text(
            render_profile(
                target,
                details=True,
                public=public,
                manager=manager,
                mentor=mentor,
                show_relations=service.settings.profile_relations,
            ),
            parse_mode="HTML",
            reply_markup=_buttons(actions),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("profile_main:"))
    async def show_profile_main(query: CallbackQuery) -> None:
        _, raw_id, raw_public = (query.data or "").split(":")
        target = service.database.get_employee(int(raw_id))
        actor = get_actor(query.from_user.id)
        public = bool(int(raw_public))
        if actor is None:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            render_profile(target, public=public),
            parse_mode="HTML",
            reply_markup=_profile_actions(
                target.id, own=target.id == actor.id, public=public
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("profile_edit:"))
    async def edit_own_profile(query: CallbackQuery) -> None:
        target_id = int((query.data or "").split(":")[1])
        actor = get_actor(query.from_user.id)
        if actor is None or actor.id != target_id:
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            "✏️ <b>Редактирование профиля</b>\n\nВыберите поле:",
            parse_mode="HTML",
            reply_markup=_editor(target_id, "profilefield"),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("manage_profile:"))
    async def manage_profile(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        target = service.database.get_employee(int((query.data or "").split(":")[1]))
        if (
            actor is None
            or (actor.role != "owner" and not actor.is_team_lead)
            or not can_manage(actor, target)
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            _managed_profile_text(target),
            parse_mode="HTML",
            reply_markup=_editor(target.id, "ownerfield", owner=actor.role == "owner"),
        )
        await query.answer()

    @router.callback_query(
        F.data.startswith("profilefield:") | F.data.startswith("ownerfield:")
    )
    async def choose_field(query: CallbackQuery, state: FSMContext) -> None:
        prefix, raw_id, field = (query.data or "").split(":")
        actor = get_actor(query.from_user.id)
        target_id = int(raw_id)
        target = service.database.get_employee(target_id)
        allowed_fields = (
            {item[1] for item in EDITABLE_FIELDS}
            if prefix == "profilefield" and actor is not None and actor.id == target_id
            else {item[1] for item in MANAGED_EDITABLE_FIELDS}
        )
        if actor is not None and actor.role == "owner":
            allowed_fields.add("id")
        if (
            actor is None
            or not can_manage(actor, target)
            or field not in allowed_fields
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        choices = {
            "work_format": [
                (label, value) for value, label in directories.work_formats.items()
            ],
            "grade": [(value, value) for value in directories.grades],
            "direction": [(value, value) for value in directories.directions],
            "english_level": [
                (f"{value} ({label})", value)
                for value, label in directories.english_levels.items()
            ],
            "office_city": [(value, value) for value in directories.offices],
            "project_name": [(value, value) for value in directories.projects],
        }
        if field in choices:
            items = [
                (label, f"setprofile:{target_id}:{field}:{value}")
                for label, value in choices[field]
            ]
            if field == "project_name":
                items.append(("Ввести вручную", f"manualprofile:{target_id}:{field}"))
            await query.message.answer(
                "Выберите значение:", reply_markup=_buttons(items, 3)
            )
            await query.answer()
            return
        prompts = {
            "id": "Введите новый числовой ID сотрудника:",
            "full_name": "Введите ФИО:",
            "birth_date": "Введите дату рождения ДД.ММ.ГГГГ:",
            "phone": "Введите телефон:",
            "email": "Введите Email:",
            "personal_email": "Введите Email_P:",
            "employment_date": "Введите дату трудоустройства ДД.ММ.ГГГГ:",
            "location": "Введите город:",
            "project_start_date": "Введите дату старта на проекте ДД.ММ.ГГГГ:",
        }
        await state.set_state(ProfileForm.waiting_value)
        await state.set_data(
            {
                "target_id": target_id,
                "profile_field": field,
                "owner_edit": prefix == "ownerfield",
            }
        )
        await query.message.answer(prompts[field])
        await query.answer()

    @router.callback_query(F.data.startswith("manualprofile:"))
    async def manual_field(query: CallbackQuery, state: FSMContext) -> None:
        _, raw_id, field = (query.data or "").split(":")
        actor = get_actor(query.from_user.id)
        target_id = int(raw_id)
        if actor is None or not can_manage(
            actor, service.database.get_employee(target_id)
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
        if actor is None or not can_manage(
            actor, service.database.get_employee(target_id)
        ):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        allowed_values = {
            "work_format": set(directories.work_formats),
            "grade": set(directories.grades),
            "direction": set(directories.directions),
            "english_level": set(directories.english_levels),
            "office_city": set(directories.offices),
            "project_name": set(directories.projects),
        }
        if field not in allowed_values or value not in allowed_values[field]:
            await query.answer("Значение отсутствует в справочнике.", show_alert=True)
            return
        service.database.update_profile(target_id, **{field: value})
        await query.message.edit_text(
            "✅ <b>Данные сотрудника обновлены</b>", parse_mode="HTML"
        )
        await query.answer()

    @router.message(ProfileForm.waiting_value, F.text & ~F.text.startswith("/"))
    async def save_field(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        data = await state.get_data()
        actor = get_actor(message.from_user.id)
        target_id = int(data["target_id"])
        if actor is None or not can_manage(
            actor, service.database.get_employee(target_id)
        ):
            await state.clear()
            return
        field = str(data["profile_field"])
        value = (message.text or "").strip()
        try:
            if not value:
                raise ValueError
            value = directories.ensure_allowed_text(value, maximum=254)
            if field == "id":
                if actor.role != "owner":
                    raise ValueError
                service.database.update_employee_id(
                    target_id, validate_employee_id(value)
                )
                await state.clear()
                await message.answer(
                    "✅ <b>ID сотрудника обновлён</b>", parse_mode="HTML"
                )
                return
            kwargs = {field: value}
            if field == "full_name":
                kwargs[field] = validate_full_name(value)
            elif field in {"birth_date", "project_start_date", "employment_date"}:
                parsed = datetime.strptime(value, "%d.%m.%Y").date()
                if field == "birth_date" and parsed >= date.today():
                    raise ValueError
                kwargs[field] = parsed
            elif field == "phone":
                kwargs[field] = validate_phone(value)
            elif field in {"email", "personal_email"}:
                kwargs[field] = validate_email(value)
            elif field == "location":
                kwargs[field] = validate_city(value, directories)
            elif field == "project_name":
                kwargs[field] = directories.ensure_allowed_text(value, maximum=200)
            service.database.update_profile(target_id, **kwargs)
        except ValueError:
            await message.answer("Некорректное значение. Попробуйте еще раз.")
            return
        await state.clear()
        await message.answer("✅ <b>Данные сотрудника обновлены</b>", parse_mode="HTML")

    return router
