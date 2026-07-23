from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from core.config import Settings
from core.db import Database
from core.events import format_events, list_events


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
            if self.settings.owner_telegram_id is not None:
                await self.bot.send_message(
                    self.settings.owner_telegram_id,
                    f"Через {reminder.days_before} дн. запланирован отпуск "
                    f"{vacation.employee_name}: "
                    f"{vacation.start_date:%d.%m.%Y}–{vacation.end_date:%d.%m.%Y}.",
                )
            if (
                vacation.team_lead_telegram_user_id is not None
                and vacation.team_lead_telegram_user_id != vacation.telegram_user_id
            ):
                await self.bot.send_message(
                    vacation.team_lead_telegram_user_id,
                    f"Отпуск сотрудника {vacation.employee_name}: "
                    f"{vacation.start_date:%d.%m.%Y}–{vacation.end_date:%d.%m.%Y}.",
                )
            self.database.mark_reminder_sent(vacation.id, now.date())
            sent += 1
        return sent


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


class SystemNotificationSender:
    """Send mandatory team-lead notifications with durable deduplication."""

    def __init__(self, database: Database, settings: Settings, bot: Bot):
        self.database = database
        self.settings = settings
        self.bot = bot

    async def _send_once(
        self,
        chat_id: int,
        text: str,
        event_type: str,
        employee_id: int,
        event_key: str,
    ) -> bool:
        if self.database.was_system_notification_sent(
            event_type, employee_id, event_key
        ):
            return False
        try:
            await self.bot.send_message(chat_id, text)
        except TelegramAPIError:
            return False
        self.database.mark_system_notification_sent(event_type, employee_id, event_key)
        return True

    async def send_due(self, now: datetime | None = None) -> int:
        current = now or datetime.now(ZoneInfo(self.settings.app_timezone))
        sent = 0

        for (
            vacation_id,
            employee_id,
            employee_name,
            lead_chat_id,
            start_date,
            end_date,
        ) in (
            self.database.list_pending_vacation_lead_notifications()
            if (
                self.settings.feature_notifications
                and self.settings.auto_vacation_notifications
            )
            else ()
        ):
            sent += await self._send_once(
                lead_chat_id,
                "🏖 Сотрудник добавил отпуск\n\n"
                f"Сотрудник: {employee_name}\n"
                f"Период: {start_date:%d.%m.%Y} — {end_date:%d.%m.%Y}",
                "vacation_added",
                employee_id,
                str(vacation_id),
            )

        today = current.date()
        if (
            self.settings.feature_events
            and self.settings.auto_daily_events
            and current.time().replace(tzinfo=None)
            >= time.fromisoformat(self.settings.daily_events_time)
        ):
            daily_text = format_events(
                list_events(self.database, today, today), "События на сегодня"
            )
            for recipient in self.database.list_employees():
                if (
                    recipient.role == "guest"
                    or not recipient.is_active
                    or recipient.telegram_user_id is None
                ):
                    continue
                sent += await self._send_once(
                    recipient.telegram_user_id,
                    daily_text,
                    "daily_events",
                    recipient.id,
                    today.isoformat(),
                )
        if not self.settings.feature_notifications or current.time().replace(
            tzinfo=None
        ) < time(9, 30):
            return sent

        for employee in self.database.list_employees():
            if not employee.is_active or employee.team_lead_id is None:
                continue
            try:
                lead = self.database.get_employee(employee.team_lead_id)
            except LookupError:
                continue
            if lead.telegram_user_id is None:
                continue

            if (
                self.settings.auto_birthday_notifications
                and employee.birth_date
                and (
                    employee.birth_date.month,
                    employee.birth_date.day,
                )
                == (today.month, today.day)
            ):
                sent += await self._send_once(
                    lead.telegram_user_id,
                    "🎂 Сегодня день рождения сотрудника\n\n"
                    f"Сотрудник: {employee.full_name}",
                    "birthday",
                    employee.id,
                    str(today.year),
                )

            if self.settings.auto_probation_notifications and employee.employment_date:
                probation_end = _add_months(employee.employment_date, 3)
                if probation_end == today:
                    sent += await self._send_once(
                        lead.telegram_user_id,
                        "📋 Сегодня завершается испытательный срок\n\n"
                        f"Сотрудник: {employee.full_name}\n"
                        f"Дата трудоустройства: {employee.employment_date:%d.%m.%Y}",
                        "probation_end",
                        employee.id,
                        probation_end.isoformat(),
                    )
        for sick in self.database.list_long_active_sick_leaves(today):
            sent += await self._send_once(
                int(sick["lead_chat_id"]),
                "⚠️ Аномалия больничного\n\n"
                f"{sick['full_name']} находится на больничном более 10 дней "
                f"(с {date.fromisoformat(str(sick['start_date'])):%d.%m.%Y}).",
                "sick_leave_over_10_days",
                int(sick["employee_id"]),
                f"{sick['id']}:{today.isoformat()}",
            )
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
            for chat_id in self.database.list_notification_recipients(
                notification.recipient_roles,
                notification.target_team_id,
                notification.recipient_employee_ids,
            ):
                try:
                    await self.bot.send_message(chat_id, notification.message_text)
                    delivered += 1
                except TelegramAPIError:
                    failed += 1
            self.database.finish_scheduled_notification(
                notification.id, delivered, failed
            )
        return len(notifications)
