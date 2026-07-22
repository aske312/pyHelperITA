from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.db import validate_full_name
from bot.service import VacationService


class Onboarding(StatesGroup):
    waiting_for_full_name = State()
    waiting_for_phone = State()
    waiting_for_location = State()
    waiting_for_email = State()


def _buttons(items: list[tuple[str, str]], width: int = 3):
    builder = InlineKeyboardBuilder()
    for text, data in items:
        builder.button(text=text, callback_data=data)
    builder.adjust(width)
    return builder.as_markup()


def create_onboarding_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="onboarding")

    async def continue_onboarding(profile, send, state: FSMContext) -> None:
        if not profile.profile_completed:
            await state.set_state(Onboarding.waiting_for_full_name)
            await send("Введите полное ФИО:")
        elif not profile.phone:
            await state.set_state(Onboarding.waiting_for_phone)
            await send("Введите номер телефона:")
        elif not profile.location:
            await state.set_state(Onboarding.waiting_for_location)
            await send("Введите локацию пребывания:")
        elif profile.grade is None:
            await state.clear()
            await send("Выберите грейд:", reply_markup=_buttons([
                (value, f"onboarding_grade:{value}")
                for value in ("Intern", "Junior", "Middle", "Senior", "RM1")
            ]))
        elif profile.direction is None:
            await state.clear()
            await send("Выберите направление:", reply_markup=_buttons([
                (value, f"onboarding_direction:{value}")
                for value in ("SA", "QA", "DEV", "HR")
            ], 4))
        elif not profile.email:
            await state.set_state(Onboarding.waiting_for_email)
            await send("Введите рабочий Email:")
        else:
            await state.clear()
            await send(f"Профиль оформлен, {profile.full_name}.")

    @router.message(CommandStart())
    async def start_onboarding(message: Message, state: FSMContext) -> None:
        user = message.from_user
        if user is None:
            return
        profile = service.database.upsert_telegram_user(
            telegram_user_id=user.id, username=user.username,
            first_name=user.first_name, last_name=user.last_name,
            is_owner=user.id == settings.owner_telegram_id,
        )
        from bot.bot import set_employee_command_menu

        await set_employee_command_menu(message.bot, profile)
        await continue_onboarding(profile, message.answer, state)

    @router.message(Onboarding.waiting_for_full_name)
    async def save_full_name(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        try:
            profile = service.database.get_employee_by_telegram(message.from_user.id)
            if profile is None:
                raise LookupError
            profile = service.database.complete_profile(
                profile.id, validate_full_name(message.text or "")
            )
        except (ValueError, LookupError) as error:
            await message.answer(f"{error}. Пример: Иванов Иван Иванович")
            return
        await continue_onboarding(profile, message.answer, state)

    @router.message(Onboarding.waiting_for_phone)
    async def save_phone(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        if message.from_user is None or not re.fullmatch(r"\+?[0-9 ()-]{7,20}", value):
            await message.answer("Некорректный номер телефона.")
            return
        profile = service.database.get_employee_by_telegram(message.from_user.id)
        if profile is None:
            return
        await continue_onboarding(
            service.database.update_profile(profile.id, phone=value), message.answer, state
        )

    @router.message(Onboarding.waiting_for_location)
    async def save_location(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        if message.from_user is None or not value:
            await message.answer("Локация не может быть пустой.")
            return
        profile = service.database.get_employee_by_telegram(message.from_user.id)
        if profile is None:
            return
        await continue_onboarding(
            service.database.update_profile(profile.id, location=value), message.answer, state
        )

    @router.callback_query(F.data.startswith("onboarding_grade:"))
    async def save_grade(query: CallbackQuery, state: FSMContext) -> None:
        profile = service.database.get_employee_by_telegram(query.from_user.id)
        if profile is None:
            return
        profile = service.database.update_profile(
            profile.id, grade=(query.data or "").split(":", 1)[1]
        )
        await query.answer()
        await continue_onboarding(profile, query.message.edit_text, state)

    @router.callback_query(F.data.startswith("onboarding_direction:"))
    async def save_direction(query: CallbackQuery, state: FSMContext) -> None:
        profile = service.database.get_employee_by_telegram(query.from_user.id)
        if profile is None:
            return
        profile = service.database.update_profile(
            profile.id, direction=(query.data or "").split(":", 1)[1]
        )
        await query.answer()
        await continue_onboarding(profile, query.message.edit_text, state)

    @router.message(Onboarding.waiting_for_email)
    async def save_email(message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip().lower()
        if message.from_user is None or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            await message.answer("Некорректный Email.")
            return
        profile = service.database.get_employee_by_telegram(message.from_user.id)
        if profile is None:
            return
        await continue_onboarding(
            service.database.update_profile(profile.id, email=value), message.answer, state
        )

    return router