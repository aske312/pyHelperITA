from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from core.db import Database


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    event_date: date
    icon: str
    text: str


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _birthday_in_range(birth_date: date, start: date, end: date) -> date | None:
    for year in range(start.year, end.year + 1):
        day = min(birth_date.day, monthrange(year, birth_date.month)[1])
        candidate = date(year, birth_date.month, day)
        if start <= candidate <= end:
            return candidate
    return None


def list_events(database: Database, start: date, end: date) -> list[CalendarEvent]:
    employees = {
        employee.id: employee
        for employee in database.list_employees()
        if employee.role != "guest"
    }
    events: list[CalendarEvent] = []
    for employee in employees.values():
        if employee.birth_date:
            birthday = _birthday_in_range(employee.birth_date, start, end)
            if birthday:
                events.append(
                    CalendarEvent(
                        birthday, "🎂", f"День рождения: {employee.full_name}"
                    )
                )
        if employee.employment_date:
            probation_end = add_months(employee.employment_date, 3)
            if start <= probation_end <= end:
                events.append(
                    CalendarEvent(
                        probation_end,
                        "📋",
                        f"Завершение испытательного срока: {employee.full_name}",
                    )
                )
    for vacation in database.list_vacations():
        if vacation.employee_id not in employees:
            continue
        if start <= vacation.start_date <= end:
            events.append(
                CalendarEvent(
                    vacation.start_date,
                    "🏖",
                    f"Начало отпуска: {vacation.employee_name} "
                    f"({vacation.start_date:%d.%m}–{vacation.end_date:%d.%m})",
                )
            )
        if start <= vacation.end_date <= end:
            events.append(
                CalendarEvent(
                    vacation.end_date,
                    "🏁",
                    f"Окончание отпуска: {vacation.employee_name}",
                )
            )
    return sorted(events, key=lambda item: (item.event_date, item.text))


def period_dates(period: str, today: date) -> tuple[date, date, str]:
    if period == "today":
        return today, today, "Сегодня"
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6), "Текущая неделя"
    if period == "month":
        return today, add_months(today, 1), "Предстоящий месяц"
    raise ValueError("Неизвестный период")


def format_events(events: list[CalendarEvent], title: str) -> str:
    if not events:
        return f"📅 <b>{title}</b>\n\nСобытий не запланировано."
    lines = [
        f"{item.icon} <code>{item.event_date:%d.%m.%Y}</code> · {item.text}"
        for item in events
    ]
    return f"📅 <b>{title}</b>\n\n" + "\n".join(lines)
