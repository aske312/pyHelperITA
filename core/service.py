from __future__ import annotations

from datetime import date, time, timedelta

from core.config import Settings
from core.db import Database
from core.models import Employee, ReminderSettings, Vacation
from core.holidays import load_holidays


class VacationService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
    ) -> None:
        self.database = database
        self.settings = settings

    def initialize(self) -> None:
        self.database.initialize()
        if self.settings.owner_telegram_id is not None:
            if not self.settings.owner_full_name:
                raise ValueError(
                    "Для OWNER_TELEGRAM_ID необходимо указать OWNER_FULL_NAME в .env"
                )
            self.database.ensure_owner(
                self.settings.owner_telegram_id,
                self.settings.owner_full_name,
            )

    def register_employee(
        self, full_name: str, telegram_user_id: int | None = None
    ) -> Employee:
        employee = self.database.add_employee(full_name, telegram_user_id)
        self.database.upsert_reminder_settings(
            ReminderSettings(
                employee_id=employee.id,
                days_before=self.settings.default_reminder_days,
                reminder_time=time.fromisoformat(self.settings.default_reminder_time),
                text_template=self.settings.default_reminder_text,
                enabled=True,
            )
        )
        return employee

    def add_vacation(
        self, employee_id: int, start_date: date, end_date: date
    ) -> Vacation:
        return self.database.add_vacation(employee_id, start_date, end_date)

    def vacation_anomalies(self, vacation: Vacation) -> list[str]:
        holidays = load_holidays(self.settings.russian_holidays_path)

        def working_days(start: date, end: date) -> int:
            count = 0
            current = start
            while current <= end:
                if current.weekday() < 5 and current not in holidays:
                    count += 1
                current += timedelta(days=1)
            return count

        items = self.database.list_vacations(
            employee_id=vacation.employee_id,
            year=vacation.start_date.year,
        )
        anomalies: list[str] = []
        total_days = sum(
            working_days(item.start_date, item.end_date) for item in items
        )
        if total_days > 28:
            anomalies.append(
                f"суммарно {total_days} рабочих дней в {vacation.start_date.year} году"
            )
        period_days = working_days(vacation.start_date, vacation.end_date)
        if period_days > 28:
            anomalies.append(f"один период длиннее 28 рабочих дней: {period_days}")
        if period_days == 1:
            anomalies.append("отдельный отпуск на один день")
        if vacation.start_date.weekday() >= 5 or vacation.end_date.weekday() >= 5:
            anomalies.append("граница отпуска приходится на выходной")
        if len(items) >= 3:
            anomalies.append(f"отпуск разбит на {len(items)} периодов за год")
        if vacation.start_date >= date.today():
            days_until = (vacation.start_date - date.today()).days
            if days_until < 14:
                anomalies.append(
                    f"отпуск добавлен менее чем за 14 дней: за {days_until}"
                )
        ordered = sorted(items, key=lambda item: item.start_date)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            gap = (current.start_date - previous.end_date).days - 1
            if 0 <= gap <= 2:
                anomalies.append(f"короткий разрыв между отпусками: {gap} дн.")
                break
        return anomalies

    def set_reminder(
        self,
        employee_id: int,
        days_before: int,
        reminder_time: time,
        text_template: str,
        enabled: bool = True,
    ) -> ReminderSettings:
        if days_before < 0:
            raise ValueError("Количество дней не может быть отрицательным")
        template = text_template.strip()
        if not template:
            raise ValueError("Текст напоминания не может быть пустым")
        try:
            template.format(
                employee_name="Сотрудник",
                start_date="01.01.2027",
                end_date="14.01.2027",
                days_count=14,
            )
        except KeyError as error:
            raise ValueError(f"Неизвестное поле шаблона: {error.args[0]}") from error
        settings = ReminderSettings(
            employee_id=employee_id,
            days_before=days_before,
            reminder_time=reminder_time,
            text_template=template,
            enabled=enabled,
        )
        self.database.upsert_reminder_settings(settings)
        return settings
