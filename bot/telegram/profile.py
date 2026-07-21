from __future__ import annotations

import re
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import validate_full_name
from bot.service import VacationService


class ProfileForm(StatesGroup):
    waiting_value = State()


def profile_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить ФИО", callback_data="profile:full_name")
    builder.button(text="✏️ Изменить дату рождения", callback_data="profile:birth_date")
    builder.button(text="✏️ Изменить телефон", callback_data="profile:phone")
    builder.button(text="✏️ Изменить Email", callback_data="profile:email")
    builder.adjust(2)
    return builder.as_markup()


def create_profile_router(service: VacationService) -> Router:
    router = Router(name="profile")

    @router.message(Command("profile"))
    async def show_profile(message: Message) -> None:
        if message.from_user is None:
            return
        profile = service.database.get_employee_by_telegram(message.from_user.id)
        if profile is None:
            await message.answer("Сначала зарегистрируйтесь через /start.")
            return
        lines = [
            f"ФИО: {profile.full_name}",
            f"Дата рождения: {profile.birth_date:%d.%m.%Y}"
            if profile.birth_date
            else "Дата рождения: не указана",
            f"Телефон: {profile.phone or 'не указан'}",
            f"Email: {profile.email or 'не указан'}",
            f"Telegram: {profile.telegram_tag or 'без username'}",
        ]
        await message.answer(
            chr(10).join(lines)
            + chr(10)
            + chr(10)
            + "Для редактирования выберите кнопку ниже:",
            reply_markup=profile_keyboard(),
        )

    @router.callback_query(F.data.startswith("profile:"))
    async def choose_field(query: CallbackQuery, state: FSMContext) -> None:
        field = (query.data or "").split(":")[1]
        prompts = {
            "full_name": "Введите Фамилию Имя, полное ФИО или Фамилию И.О.",
            "birth_date": "Введите дату рождения: ДД.ММ.ГГГГ",
            "phone": "Введите телефон в международном формате:",
            "email": "Введите электронную почту:",
        }
        await state.set_state(ProfileForm.waiting_value)
        await state.set_data({"profile_field": field})
        await query.message.answer(prompts[field])
        await query.answer()

    @router.message(ProfileForm.waiting_value)
    async def save_field(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        profile = service.database.get_employee_by_telegram(message.from_user.id)
        if profile is None:
            await state.clear()
            return
        data = await state.get_data()
        field = str(data["profile_field"])
        value = (message.text or "").strip()
        try:
            if field == "full_name":
                service.database.update_profile(
                    profile.id, full_name=validate_full_name(value)
                )
            elif field == "birth_date":
                birth_date = datetime.strptime(value, "%d.%m.%Y").date()
                if birth_date >= date.today():
                    raise ValueError
                service.database.update_profile(profile.id, birth_date=birth_date)
            elif field == "phone":
                if not re.fullmatch(r"\+?[0-9 ()-]{7,20}", value):
                    raise ValueError
                service.database.update_profile(profile.id, phone=value)
            elif field == "email":
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
                    raise ValueError
                service.database.update_profile(profile.id, email=value)
        except ValueError:
            await message.answer("Некорректное значение. Попробуйте ещё раз.")
            return
        await state.clear()
        await message.answer("Профиль обновлён. Открыть снова: /profile")

    return router
