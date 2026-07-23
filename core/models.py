from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True, slots=True)
class Employee:
    id: int
    full_name: str
    telegram_user_id: int | None
    role: str
    is_team_lead: bool
    team_lead_id: int | None
    mentor_id: int | None
    telegram_username: str | None
    telegram_first_name: str | None
    telegram_last_name: str | None
    telegram_tag: str | None
    birth_date: date | None
    phone: str | None
    email: str | None
    personal_email: str | None
    english_level: str | None
    employment_date: date | None
    location: str | None
    office_city: str | None
    work_format: str | None
    grade: str | None
    direction: str | None
    project_name: str | None
    project_start_date: date | None
    profile_completed: bool
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Team:
    id: int
    name: str
    lead_id: int
    lead_name: str
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
    team_lead_telegram_user_id: int | None
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
    recipient_roles: tuple[str, ...]
    target_team_id: int | None
    recipient_employee_ids: tuple[int, ...]
    repeat_interval_minutes: int | None
    repeats_remaining: int
    delivered_count: int
    failed_count: int
    created_at: datetime
    sent_at: datetime | None
