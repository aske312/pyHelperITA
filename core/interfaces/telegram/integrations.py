from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import Settings
from core.integrations.service import IntegrationService
from core.integrations.secrets import SecretStore
from core.service import VacationService


class IntegrationForm(StatesGroup):
    waiting_value = State()
    waiting_secret = State()


def _buttons(items: list[tuple[str, str]], width: int = 1):
    builder = InlineKeyboardBuilder()
    for text, data in items:
        builder.button(text=text, callback_data=data)
    builder.button(text="✖️ Закрыть", callback_data="ui_close")
    builder.adjust(width)
    return builder.as_markup()


def _status_text(item) -> str:
    mail = (
        f"{escape(item.mail_address)} · {escape(item.mail_provider)} "
        f"(<code>{escape(item.mail_status)}</code>)"
        if item.mail_address
        else "<i>не настроена</i>"
    )
    calendar = (
        f"{escape(item.calendar_account)} · {escape(item.calendar_provider)} "
        f"(<code>{escape(item.calendar_status)}</code>)"
        if item.calendar_account
        else "<i>не настроен</i>"
    )
    return (
        "🔌 <b>Мои интеграции</b>\n\n"
        f"✉️ <b>Почта:</b> {mail}\n"
        f"📅 <b>Календарь:</b> {calendar}\n\n"
        "Статус <code>pending</code> означает, что настройки сохранены, "
        "но OAuth/API-подключение ещё не выполнено."
    )


def create_integrations_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="integrations")
    secret_store = SecretStore(settings.integration_secret_key) if settings.integration_secret_key else None
    integrations = IntegrationService(service.database, secret_store)

    def employee(telegram_id: int):
        return service.database.get_employee_by_telegram(telegram_id)

    async def show_menu(message: Message, employee_id: int) -> None:
        item = integrations.get(employee_id)
        buttons: list[tuple[str, str]] = []
        if settings.feature_mail_integrations:
            buttons.append(("✉️ Настроить почту", "integration_setup:mail"))
            if item.mail_address:
                buttons.append(("🗑 Отключить почту", "integration_disconnect:mail"))
        if settings.feature_calendar_integrations:
            buttons.append(("📅 Настроить календарь", "integration_setup:calendar"))
            if item.calendar_account:
                buttons.append(
                    ("🗑 Отключить календарь", "integration_disconnect:calendar")
                )
        await message.answer(
            _status_text(item),
            parse_mode="HTML",
            reply_markup=_buttons(buttons),
        )

    @router.message(Command("integrations"))
    async def integrations_command(message: Message) -> None:
        if message.from_user is None:
            return
        current = employee(message.from_user.id)
        if current is None:
            await message.answer("Сначала зарегистрируйтесь через /start.")
            return
        await show_menu(message, current.id)

    @router.callback_query(F.data.startswith("integration_setup:"))
    async def integration_setup(query: CallbackQuery, state: FSMContext) -> None:
        current = employee(query.from_user.id)
        if current is None:
            await query.answer("Профиль не найден.", show_alert=True)
            return
        kind = (query.data or "").split(":")[1]
        if kind == "mail" and not settings.feature_mail_integrations:
            await query.answer("Интеграция с почтой отключена.", show_alert=True)
            return
        if kind == "calendar" and not settings.feature_calendar_integrations:
            await query.answer("Интеграция с календарём отключена.", show_alert=True)
            return
        providers = (
            [
                ("Google Gmail", "google"),
                ("Microsoft 365", "microsoft"),
                ("Яндекс Почта", "yandex"),
                ("Mail.ru", "mailru"),
                ("SMTP", "smtp"),
            ]
            if kind == "mail"
            else [
                ("Google Calendar", "google"),
                ("Microsoft 365", "microsoft"),
                ("CalDAV", "caldav"),
            ]
        )
        await state.set_state(IntegrationForm.waiting_value)
        await state.set_data({"integration_kind": kind})
        await query.message.edit_text(
            "Выберите провайдера:",
            reply_markup=_buttons(
                [
                    (label, f"integration_provider:{kind}:{provider}")
                    for label, provider in providers
                ]
            ),
        )
        await query.answer()

    @router.callback_query(
        IntegrationForm.waiting_value, F.data.startswith("integration_provider:")
    )
    async def integration_provider(query: CallbackQuery, state: FSMContext) -> None:
        _, kind, provider = (query.data or "").split(":")
        await state.update_data(
            integration_kind=kind,
            integration_provider=provider,
        )
        prompt = (
            "Введите адрес почтового ящика:"
            if kind == "mail"
            else "Введите email аккаунта или URL CalDAV-календаря:"
        )
        await query.message.edit_text(prompt)
        await query.answer()

    @router.message(IntegrationForm.waiting_value, F.text & ~F.text.startswith("/"))
    async def integration_value(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        current = employee(message.from_user.id)
        if current is None:
            await state.clear()
            return
        data = await state.get_data()
        kind = str(data.get("integration_kind", ""))
        provider = str(data.get("integration_provider", ""))
        if not provider:
            await message.answer("Сначала выберите провайдера кнопкой.")
            return
        try:
            if kind == "mail":
                if provider in {"yandex", "mailru", "smtp"}:
                    if secret_store is None:
                        await message.answer(
                            "Сохранение пароля отключено: администратор должен задать "
                            "INTEGRATION_SECRET_KEY."
                        )
                        await state.clear()
                        return
                    await state.update_data(integration_address=message.text or "")
                    await state.set_state(IntegrationForm.waiting_secret)
                    await message.answer(
                        "Введите пароль приложения (сообщение будет удалено после обработки):"
                    )
                    return
                item = integrations.configure_mail(
                    current.id, provider, message.text or ""
                )
            else:
                item = integrations.configure_calendar(
                    current.id, provider, message.text or ""
                )
        except ValueError as error:
            await message.answer(f"Не удалось сохранить: {escape(str(error))}")
            return
        await state.clear()
        await message.answer(
            "✅ Настройки сохранены.\n\n" + _status_text(item),
            parse_mode="HTML",
        )

    @router.message(IntegrationForm.waiting_secret, F.text & ~F.text.startswith("/"))
    async def integration_secret(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        current = employee(message.from_user.id)
        data = await state.get_data()
        try:
            if current is None:
                return
            item = integrations.configure_mail(
                current.id,
                str(data.get("integration_provider", "")),
                str(data.get("integration_address", "")),
                password=message.text or "",
            )
        except ValueError as error:
            await message.answer(f"Не удалось сохранить: {escape(str(error))}")
            return
        finally:
            try:
                await message.delete()
            except Exception:
                pass
        await state.clear()
        await message.answer(
            "✅ Подключение и зашифрованный пароль сохранены.\n\n" + _status_text(item),
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith("integration_disconnect:"))
    async def integration_disconnect(query: CallbackQuery) -> None:
        current = employee(query.from_user.id)
        if current is None:
            await query.answer("Профиль не найден.", show_alert=True)
            return
        kind = (query.data or "").split(":")[1]
        integrations.disconnect(current.id, kind)
        await query.message.edit_text(
            "✅ Интеграция отключена. Связанный зашифрованный секрет удалён."
        )
        await query.answer()

    return router
