from __future__ import annotations

from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import Settings
from bot.service import VacationService


def create_admin_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="admin")

    @router.message(Command("broadcast"))
    async def broadcast(message: Message) -> None:
        if message.from_user is None:
            return
        admin = service.database.get_employee_by_telegram(message.from_user.id)
        if admin is None or admin.role != "admin":
            await message.answer("Команда доступна только администратору.")
            return

        arguments = (message.text or "").partition(" ")[2].strip()
        if arguments == "list":
            notifications = service.database.list_scheduled_notifications(
                pending_only=True
            )
            if not notifications:
                await message.answer("Запланированных уведомлений нет.")
                return
            lines = [
                f"#{item.id} — {item.scheduled_at:%d.%m.%Y %H:%M}\n{item.message_text}"
                for item in notifications
            ]
            await message.answer(
                "Запланированные уведомления:\n\n" + "\n\n".join(lines)
            )
            return

        if arguments.startswith("delete "):
            try:
                notification_id = int(arguments.removeprefix("delete ").strip())
            except ValueError:
                await message.answer("Пример удаления: /broadcast delete 3")
                return
            if service.database.cancel_scheduled_notification(notification_id):
                await message.answer(f"Уведомление #{notification_id} отменено.")
            else:
                await message.answer("Активное уведомление с таким ID не найдено.")
            return

        parts = arguments.split(maxsplit=2)
        if len(parts) != 3:
            await message.answer(
                "Создание: /broadcast ДД.ММ.ГГГГ ЧЧ:ММ текст\n"
                "Список: /broadcast list\n"
                "Отмена: /broadcast delete ID"
            )
            return
        try:
            scheduled_at = datetime.strptime(f"{parts[0]} {parts[1]}", "%d.%m.%Y %H:%M")
            if scheduled_at <= datetime.now():
                raise ValueError("Дата и время должны быть в будущем")
            notification = service.database.add_scheduled_notification(
                scheduled_at, parts[2], admin.id
            )
        except ValueError as error:
            await message.answer(f"Не удалось запланировать уведомление: {error}")
            return
        await message.answer(
            f"Уведомление #{notification.id} запланировано на "
            f"{notification.scheduled_at:%d.%m.%Y в %H:%M}."
        )

    return router
