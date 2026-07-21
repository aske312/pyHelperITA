from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True, slots=True)
class Employee:
    id: int
    full_name: str
    telegram_user_id: int | None
    role: str
    manager_id: int | None
    telegram_username: str | None
    telegram_first_name: str | None
    telegram_last_name: str | None
    telegram_tag: str | None
    birth_date: date | None
    phone: str | None
    email: str | None
    profile_completed: bool
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Vacation:
    id: int
    employee_id: int
    start_date: date
    end_date: date
    created_at: datetime

    @property
    def days_count(self) -> int:
        return (self.end_date - self.start_date).days + 1


@dataclass(frozen=True, slots=True)
class VacationView:
    id: int
    employee_id: int
    employee_name: str
    telegram_user_id: int | None
    manager_telegram_user_id: int | None
    start_date: date
    end_date: date
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReminderSettings:
    employee_id: int
    days_before: int
    reminder_time: time
    text_template: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ScheduledNotification:
    id: int
    scheduled_at: datetime
    message_text: str
    status: str
    created_by_employee_id: int
    delivered_count: int
    failed_count: int
    created_at: datetime
    sent_at: datetime | None
