from __future__ import annotations

from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.access import NOTIFICATION_GROUPS
from bot.config import Settings
from bot.service import VacationService


def _parse_roles(value: str) -> tuple[str, ...]:
    roles = tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    if not roles or set(roles) - NOTIFICATION_GROUPS:
        raise ValueError("Роли: guest, employee, team_lead, owner")
    return roles


def create_owner_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="owner")

    @router.message(Command("guest"))
    async def create_guest(message: Message) -> None:
        if message.from_user is None:
            return
        actor = service.database.get_employee_by_telegram(message.from_user.id)
        if actor is None or (actor.role != "owner" and not actor.is_team_lead):
            await message.answer("Гостей могут создавать владелец и тимлид.")
            return
        arguments = (message.text or "").partition(" ")[2].strip()
        team_lead_id = actor.id if actor.is_team_lead and actor.role != "owner" else None
        name = arguments
        if actor.role == "owner" and arguments:
            first, separator, rest = arguments.partition(" ")
            if first.isdigit() and separator:
                lead = service.database.get_employee(int(first))
                if not lead.is_team_lead:
                    await message.answer("Указанный сотрудник не является тимлидом.")
                    return
                team_lead_id = lead.id
                name = rest
        if not name:
            await message.answer("Формат: /guest [ID_тимлида] Фамилия Имя")
            return
        try:
            guest = service.register_employee(name)
            guest = service.database.update_employee(
                guest.id, role="guest", team_lead_id=team_lead_id, set_team_lead=True
            )
        except (ValueError, LookupError) as error:
            await message.answer(f"Не удалось создать гостя: {error}")
            return
        await message.answer(
            f"Гость #{guest.id} создан. Тимлид: {guest.team_lead_id or 'не назначен'}."
        )

    @router.message(Command("broadcast"))
    async def broadcast(message: Message) -> None:
        if message.from_user is None:
            return
        owner = service.database.get_employee_by_telegram(message.from_user.id)
        if owner is None or owner.role != "owner":
            await message.answer("Команда доступна только владельцу.")
            return
        arguments = (message.text or "").partition(" ")[2].strip()
        if arguments == "list":
            notifications = service.database.list_scheduled_notifications(pending_only=True)
            if not notifications:
                await message.answer("Запланированных уведомлений нет.")
                return
            lines = [
                f"#{item.id} - {item.scheduled_at:%d.%m.%Y %H:%M}\n"
                f"Группы: {', '.join(item.recipient_roles)}\n{item.message_text}"
                for item in notifications
            ]
            await message.answer("Запланированные уведомления:\n\n" + "\n\n".join(lines))
            return
        if arguments.startswith("delete "):
            try:
                notification_id = int(arguments.removeprefix("delete ").strip())
            except ValueError:
                await message.answer("Формат: /broadcast delete ID")
                return
            text = (f"Уведомление #{notification_id} отменено."
                    if service.database.cancel_scheduled_notification(notification_id)
                    else "Активное уведомление не найдено.")
            await message.answer(text)
            return
        if arguments.startswith("roles "):
            try:
                _, raw_id, raw_roles = arguments.split(maxsplit=2)
                notification = service.database.update_notification_roles(
                    int(raw_id), _parse_roles(raw_roles)
                )
            except (ValueError, LookupError) as error:
                await message.answer(f"Не удалось изменить группы: {error}")
                return
            await message.answer(
                f"Группы уведомления #{notification.id}: "
                + ", ".join(notification.recipient_roles)
            )
            return

        recipient_roles = ("owner", "team_lead", "employee")
        if arguments.startswith("roles="):
            raw_roles, separator, arguments = arguments.partition(" ")
            if not separator:
                await message.answer("После групп укажите дату, время и текст.")
                return
            try:
                recipient_roles = _parse_roles(raw_roles.removeprefix("roles="))
            except ValueError as error:
                await message.answer(str(error))
                return
        parts = arguments.split(maxsplit=2)
        if len(parts) != 3:
            await message.answer(
                "Создание: /broadcast [roles=employee,team_lead] ДД.ММ.ГГГГ ЧЧ:ММ текст\n"
                "Группы: /broadcast roles ID employee,guest\n"
                "Список: /broadcast list\nОтмена: /broadcast delete ID"
            )
            return
        try:
            scheduled_at = datetime.strptime(f"{parts[0]} {parts[1]}", "%d.%m.%Y %H:%M")
            if scheduled_at <= datetime.now():
                raise ValueError("Дата и время должны быть в будущем")
            notification = service.database.add_scheduled_notification(
                scheduled_at, parts[2], owner.id, recipient_roles
            )
        except ValueError as error:
            await message.answer(f"Не удалось запланировать уведомление: {error}")
            return
        await message.answer(
            f"Уведомление #{notification.id} запланировано на "
            f"{notification.scheduled_at:%d.%m.%Y в %H:%M}. Группы: "
            + ", ".join(notification.recipient_roles)
        )

    return router