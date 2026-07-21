from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import Settings
from bot.db import Database


class ReminderSender:
    def __init__(self, database: Database, settings: Settings, bot: Bot):
        self.database = database
        self.settings = settings
        self.bot = bot

    async def send_due(self) -> int:
        now = datetime.now(ZoneInfo(self.settings.app_timezone))
        due = self.database.list_due_reminders(now.date(), now.time())
        sent = 0
        for vacation, reminder in due:
            text = reminder.text_template.format(
                employee_name=vacation.employee_name,
                start_date=vacation.start_date.strftime("%d.%m.%Y"),
                end_date=vacation.end_date.strftime("%d.%m.%Y"),
                days_count=(vacation.end_date - vacation.start_date).days + 1,
            )
            if vacation.telegram_user_id is not None:
                await self.bot.send_message(vacation.telegram_user_id, text)
            if self.settings.admin_telegram_id is not None:
                await self.bot.send_message(
                    self.settings.admin_telegram_id,
                    f"Через {reminder.days_before} дн. запланирован отпуск "
                    f"{vacation.employee_name}: "
                    f"{vacation.start_date:%d.%m.%Y}–{vacation.end_date:%d.%m.%Y}.",
                )
            if (
                vacation.manager_telegram_user_id is not None
                and vacation.manager_telegram_user_id != vacation.telegram_user_id
            ):
                await self.bot.send_message(
                    vacation.manager_telegram_user_id,
                    f"Отпуск сотрудника {vacation.employee_name}: "
                    f"{vacation.start_date:%d.%m.%Y}–{vacation.end_date:%d.%m.%Y}.",
                )
            self.database.mark_reminder_sent(vacation.id, now.date())
            sent += 1
        return sent


class NotificationSender:
    def __init__(self, database: Database, settings: Settings, bot: Bot):
        self.database = database
        self.settings = settings
        self.bot = bot

    async def send_due(self) -> int:
        now = datetime.now(ZoneInfo(self.settings.app_timezone)).replace(tzinfo=None)
        notifications = self.database.list_due_scheduled_notifications(now)
        for notification in notifications:
            delivered = 0
            failed = 0
            for chat_id in self.database.list_notification_recipients():
                try:
                    await self.bot.send_message(chat_id, notification.message_text)
                    delivered += 1
                except TelegramAPIError:
                    failed += 1
            self.database.finish_scheduled_notification(
                notification.id, delivered, failed
            )
        return len(notifications)
