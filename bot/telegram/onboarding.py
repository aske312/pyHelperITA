from __future__ import annotations

from datetime import date, datetime, time

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.config import Settings
from bot.db import validate_full_name
from bot.service import VacationService


class Onboarding(StatesGroup):
    waiting_for_full_name = State()
    waiting_for_birth_date = State()


def create_onboarding_router(
    service: VacationService,
    settings: Settings,
) -> Router:
    router = Router(name="onboarding")

    @router.message(CommandStart())
    async def start_onboarding(message: Message, state: FSMContext) -> None:
        user = message.from_user
        if user is None:
            return
        profile = service.database.upsert_telegram_user(
            telegram_user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            is_admin=user.id == settings.admin_telegram_id,
        )
        if profile.profile_completed and profile.birth_date is not None:
            await state.clear()
            await message.answer(
                f"С возвращением, \n{profile.full_name}. \nВаш профиль сохранён."
            )
            return
        if profile.profile_completed:
            await state.set_state(Onboarding.waiting_for_birth_date)
            await message.answer("Укажите дату рождения в формате ДД.ММ.ГГГГ.")
            return
        await state.set_state(Onboarding.waiting_for_full_name)
        await message.answer(
            "Для регистрации отправьте Фамилию Имя, полное ФИО или Фамилию И.О.:"
            + chr(10)
            + "Например: Иванов Иван или Иванов И.И."
        )

    @router.message(Onboarding.waiting_for_full_name)
    async def save_full_name(message: Message, state: FSMContext) -> None:
        user = message.from_user
        if user is None:
            return
        try:
            full_name = validate_full_name(message.text or "")
            profile = service.database.get_employee_by_telegram(user.id)
            if profile is None:
                profile = service.database.upsert_telegram_user(
                    user.id,
                    user.username,
                    user.first_name,
                    user.last_name,
                    is_admin=user.id == settings.admin_telegram_id,
                )
            completed = service.database.complete_profile(profile.id, full_name)
        except (ValueError, LookupError) as error:
            await message.answer(f"{error}. Пример: Иванов Иван Иванович")
            return
        await state.set_state(Onboarding.waiting_for_birth_date)
        await message.answer(
            f"Профиль создан: {completed.full_name}."
            + chr(10)
            + "Теперь укажите дату рождения в формате ДД.ММ.ГГГГ."
        )

    @router.message(Onboarding.waiting_for_birth_date)
    async def save_birth_date(message: Message, state: FSMContext) -> None:
        user = message.from_user
        if user is None:
            return
        try:
            birth_date = datetime.strptime(message.text or "", "%d.%m.%Y").date()
            today = date.today()
            age = (
                today.year
                - birth_date.year
                - ((today.month, today.day) < (birth_date.month, birth_date.day))
            )
            if age < 14 or age > 100:
                raise ValueError
            profile = service.database.get_employee_by_telegram(user.id)
            if profile is None:
                raise LookupError
            completed = service.database.update_profile(
                profile.id, birth_date=birth_date
            )
            service.set_reminder(
                completed.id,
                14,
                time.fromisoformat(settings.default_reminder_time),
                settings.default_reminder_text,
            )
        except (ValueError, LookupError):
            await message.answer("Некорректная дата. Пример: 31.12.1990")
            return
        await state.clear()
        await message.answer("Регистрация завершена. Данные профиля сохранены.")

    return router
