from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path

from bot.models import (
    Employee,
    ReminderSettings,
    ScheduledNotification,
    Vacation,
    VacationView,
)

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    telegram_user_id INTEGER UNIQUE,
    telegram_username TEXT,
    telegram_first_name TEXT,
    telegram_last_name TEXT,
    telegram_tag TEXT,
    birth_date TEXT,
    phone TEXT,
    email TEXT,
    profile_completed INTEGER NOT NULL DEFAULT 1 CHECK (profile_completed IN (0, 1)),
    role TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('employee', 'manager', 'admin')),
    manager_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vacations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (end_date >= start_date),
    UNIQUE (employee_id, start_date, end_date)
);
CREATE INDEX IF NOT EXISTS idx_vacations_employee_dates
ON vacations(employee_id, start_date, end_date);
CREATE TABLE IF NOT EXISTS reminder_settings (
    employee_id INTEGER PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,
    days_before INTEGER NOT NULL DEFAULT 7 CHECK (days_before >= 0),
    reminder_time TEXT NOT NULL DEFAULT '09:00',
    text_template TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);
CREATE TABLE IF NOT EXISTS sent_reminders (
    vacation_id INTEGER NOT NULL REFERENCES vacations(id) ON DELETE CASCADE,
    reminder_date TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (vacation_id, reminder_date)
);
CREATE TABLE IF NOT EXISTS scheduled_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheduled_at TEXT NOT NULL,
    message_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'cancelled')),
    created_by_employee_id INTEGER NOT NULL REFERENCES employees(id),
    delivered_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_notifications_due
ON scheduled_notifications(status, scheduled_at);
"""


def validate_full_name(value: str) -> str:
    normalized = " ".join(value.split())
    parts = normalized.split()
    if len(parts) == 2 and parts[1].count(".") == 2:
        surname, initials = parts
        letters = surname.replace("-", "")
        compact_initials = initials.replace(".", "")
        if (
            len(letters) >= 2
            and letters.isalpha()
            and len(compact_initials) == 2
            and compact_initials.isalpha()
            and initials.endswith(".")
        ):
            return normalized
    if len(parts) not in {2, 3}:
        raise ValueError("Введите Фамилию Имя, полное ФИО или Фамилию И.О.")
    for part in parts:
        letters = part.replace("-", "")
        if len(letters) < 2 or not letters.isalpha():
            raise ValueError("ФИО должно содержать только буквы и дефис")
    return normalized


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(employees)")
            }
            if "role" not in columns:
                connection.execute(
                    "ALTER TABLE employees ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'"
                )
            if "manager_id" not in columns:
                connection.execute(
                    "ALTER TABLE employees ADD COLUMN manager_id INTEGER"
                )
            migrations = {
                "telegram_username": "TEXT",
                "telegram_first_name": "TEXT",
                "telegram_last_name": "TEXT",
                "telegram_tag": "TEXT",
                "birth_date": "TEXT",
                "phone": "TEXT",
                "email": "TEXT",
                "profile_completed": "INTEGER NOT NULL DEFAULT 1",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE employees ADD COLUMN {name} {definition}"
                    )

    def upsert_telegram_user(
        self,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        *,
        is_admin: bool = False,
    ) -> Employee:
        existing = self.get_employee_by_telegram(telegram_user_id)
        with self.connect() as connection:
            if existing is None:
                placeholder = f"telegram:{telegram_user_id}"
                cursor = connection.execute(
                    """INSERT INTO employees(
                           full_name, telegram_user_id, telegram_username,
                           telegram_first_name, telegram_last_name, telegram_tag,
                           profile_completed,
                           role, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                    (
                        placeholder,
                        telegram_user_id,
                        username,
                        first_name,
                        last_name,
                        f"@{username}" if username else None,
                        "admin" if is_admin else "employee",
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                employee_id = int(cursor.lastrowid)
            else:
                employee_id = existing.id
                role = "admin" if is_admin else existing.role
                connection.execute(
                    """UPDATE employees SET telegram_username = ?,
                           telegram_first_name = ?, telegram_last_name = ?,
                           telegram_tag = ?, role = ?,
                           is_active = 1 WHERE id = ?""",
                    (
                        username,
                        first_name,
                        last_name,
                        f"@{username}" if username else None,
                        role,
                        employee_id,
                    ),
                )
        return self.get_employee(employee_id)

    def complete_profile(self, employee_id: int, full_name: str) -> Employee:
        normalized = validate_full_name(full_name)
        current = self.get_employee(employee_id)
        matching = self.find_employee(normalized)
        if matching is not None and matching.id != employee_id:
            if matching.telegram_user_id not in (None, current.telegram_user_id):
                raise ValueError("Пользователь с таким ФИО уже зарегистрирован")
            with self.connect() as connection:
                connection.execute(
                    """UPDATE employees SET telegram_user_id = ?,
                           telegram_username = ?, telegram_first_name = ?,
                           telegram_last_name = ?, telegram_tag = ?, profile_completed = 1,
                           is_active = 1 WHERE id = ?""",
                    (
                        current.telegram_user_id,
                        current.telegram_username,
                        current.telegram_first_name,
                        current.telegram_last_name,
                        current.telegram_tag,
                        matching.id,
                    ),
                )
                connection.execute("DELETE FROM employees WHERE id = ?", (current.id,))
            return self.get_employee(matching.id)
        with self.connect() as connection:
            connection.execute(
                "UPDATE employees SET full_name = ?, profile_completed = 1 WHERE id = ?",
                (normalized, employee_id),
            )
        return self.get_employee(employee_id)

    def update_profile(
        self,
        employee_id: int,
        *,
        full_name: str | None = None,
        birth_date: date | None = None,
        phone: str | None = None,
        email: str | None = None,
    ) -> Employee:
        values: dict[str, object] = {}
        if full_name is not None:
            values["full_name"] = validate_full_name(full_name)
        if birth_date is not None:
            values["birth_date"] = birth_date.isoformat()
        if phone is not None:
            values["phone"] = phone.strip()
        if email is not None:
            values["email"] = email.strip().lower()
        if values:
            assignments = ", ".join(f"{name} = ?" for name in values)
            with self.connect() as connection:
                connection.execute(
                    f"UPDATE employees SET {assignments} WHERE id = ?",
                    (*values.values(), employee_id),
                )
        return self.get_employee(employee_id)

    def list_notification_recipients(self) -> list[int]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT telegram_user_id FROM employees
                   WHERE is_active = 1 AND profile_completed = 1
                     AND telegram_user_id IS NOT NULL"""
            ).fetchall()
        return [int(row["telegram_user_id"]) for row in rows]

    def add_scheduled_notification(
        self, scheduled_at: datetime, message_text: str, created_by_employee_id: int
    ) -> ScheduledNotification:
        text = message_text.strip()
        if not text:
            raise ValueError("Текст уведомления не может быть пустым")
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO scheduled_notifications(
                       scheduled_at, message_text, created_by_employee_id, created_at
                   ) VALUES (?, ?, ?, ?)""",
                (
                    scheduled_at.isoformat(timespec="minutes"),
                    text,
                    created_by_employee_id,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            notification_id = int(cursor.lastrowid)
        return self.get_scheduled_notification(notification_id)

    def get_scheduled_notification(self, notification_id: int) -> ScheduledNotification:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduled_notifications WHERE id = ?",
                (notification_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"Уведомление #{notification_id} не найдено")
        return self._scheduled_notification_from_row(row)

    def list_scheduled_notifications(
        self, *, pending_only: bool = False
    ) -> list[ScheduledNotification]:
        where = "WHERE status = 'pending'" if pending_only else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM scheduled_notifications {where}
                    ORDER BY scheduled_at, id"""
            ).fetchall()
        return [self._scheduled_notification_from_row(row) for row in rows]

    def list_due_scheduled_notifications(
        self, now: datetime
    ) -> list[ScheduledNotification]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM scheduled_notifications
                   WHERE status = 'pending' AND scheduled_at <= ?
                   ORDER BY scheduled_at, id""",
                (now.isoformat(timespec="minutes"),),
            ).fetchall()
        return [self._scheduled_notification_from_row(row) for row in rows]

    def finish_scheduled_notification(
        self, notification_id: int, delivered_count: int, failed_count: int
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE scheduled_notifications
                   SET status = 'sent', delivered_count = ?, failed_count = ?, sent_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (
                    delivered_count,
                    failed_count,
                    datetime.now().isoformat(timespec="seconds"),
                    notification_id,
                ),
            )

    def cancel_scheduled_notification(self, notification_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE scheduled_notifications SET status = 'cancelled'
                   WHERE id = ? AND status = 'pending'""",
                (notification_id,),
            )
        return cursor.rowcount > 0

    def ensure_admin(self, telegram_user_id: int, full_name: str) -> Employee:
        normalized = validate_full_name(full_name)
        existing = self.get_employee_by_telegram(telegram_user_id)
        if existing is None:
            employee = self.find_employee(normalized)
            if employee is None:
                employee = self.add_employee(normalized, telegram_user_id)
            else:
                employee = self.bind_telegram(employee.id, telegram_user_id)
        else:
            employee = existing
        with self.connect() as connection:
            connection.execute(
                """UPDATE employees SET role = 'admin', full_name = ?,
                       profile_completed = 1, is_active = 1 WHERE id = ?""",
                (normalized, employee.id),
            )
        return self.get_employee(employee.id)

    def backup(self, destination: Path | str) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            with sqlite3.connect(target) as backup_connection:
                source.backup(backup_connection)
        return target

    def add_employee(
        self, full_name: str, telegram_user_id: int | None = None
    ) -> Employee:
        normalized_name = " ".join(full_name.split())
        if not normalized_name:
            raise ValueError("ФИО не может быть пустым")
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO employees(full_name, telegram_user_id, created_at) VALUES (?, ?, ?)",
                (normalized_name, telegram_user_id, now),
            )
            employee_id = int(cursor.lastrowid)
        return self.get_employee(employee_id)

    def update_employee(
        self,
        employee_id: int,
        *,
        full_name: str | None = None,
        role: str | None = None,
        manager_id: int | None = None,
        set_manager: bool = False,
    ) -> Employee:
        values: dict[str, object] = {}
        if full_name is not None:
            normalized = " ".join(full_name.split())
            if not normalized:
                raise ValueError("ФИО не может быть пустым")
            values["full_name"] = normalized
        if role is not None:
            if role not in {"employee", "manager", "admin"}:
                raise ValueError("Роль должна быть employee, manager или admin")
            values["role"] = role
        if set_manager:
            if manager_id == employee_id:
                raise ValueError("Сотрудник не может быть руководителем самому себе")
            if manager_id is not None:
                self.get_employee(manager_id)
            values["manager_id"] = manager_id
        if not values:
            return self.get_employee(employee_id)
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE employees SET {assignments} WHERE id = ?",
                (*values.values(), employee_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"Сотрудник #{employee_id} не найден")
        return self.get_employee(employee_id)

    def get_employee(self, employee_id: int) -> Employee:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM employees WHERE id = ?", (employee_id,)
            ).fetchone()
        if row is None:
            raise LookupError(f"Сотрудник #{employee_id} не найден")
        return self._employee_from_row(row)

    def get_employee_by_telegram(self, telegram_user_id: int) -> Employee | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM employees WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        return self._employee_from_row(row) if row else None

    def bind_telegram(self, employee_id: int, telegram_user_id: int) -> Employee:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE employees SET telegram_user_id = ? WHERE id = ?",
                (telegram_user_id, employee_id),
            )
            if cursor.rowcount == 0:
                raise LookupError(f"Сотрудник #{employee_id} не найден")
        return self.get_employee(employee_id)

    def find_employee(self, full_name: str) -> Employee | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM employees WHERE full_name = ? COLLATE NOCASE",
                (" ".join(full_name.split()),),
            ).fetchone()
        return self._employee_from_row(row) if row else None

    def list_employees(self, active_only: bool = True) -> list[Employee]:
        sql = "SELECT * FROM employees"
        params: tuple[object, ...] = ()
        if active_only:
            sql += " WHERE is_active = ?"
            params = (1,)
        sql += " ORDER BY full_name COLLATE NOCASE"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._employee_from_row(row) for row in rows]

    def add_vacation(
        self, employee_id: int, start_date: date, end_date: date
    ) -> Vacation:
        if end_date < start_date:
            raise ValueError("Дата окончания отпуска раньше даты начала")
        with self.connect() as connection:
            overlap = connection.execute(
                """SELECT 1 FROM vacations
                   WHERE employee_id = ? AND start_date <= ? AND end_date >= ? LIMIT 1""",
                (employee_id, end_date.isoformat(), start_date.isoformat()),
            ).fetchone()
            if overlap:
                raise ValueError("Отпуск пересекается с уже сохраненным периодом")
            now = datetime.now().isoformat(timespec="seconds")
            cursor = connection.execute(
                """INSERT INTO vacations(employee_id, start_date, end_date, created_at)
                   VALUES (?, ?, ?, ?)""",
                (employee_id, start_date.isoformat(), end_date.isoformat(), now),
            )
            vacation_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT * FROM vacations WHERE id = ?", (vacation_id,)
            ).fetchone()
        assert row is not None
        return self._vacation_from_row(row)

    def delete_vacation(self, vacation_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM vacations WHERE id = ?", (vacation_id,)
            )
            if cursor.rowcount == 0:
                raise LookupError(f"Отпуск #{vacation_id} не найден")

    def list_vacations(
        self, employee_id: int | None = None, year: int | None = None
    ) -> list[VacationView]:
        conditions: list[str] = []
        params: list[object] = []
        if employee_id is not None:
            conditions.append("v.employee_id = ?")
            params.append(employee_id)
        if year is not None:
            conditions.append("v.start_date <= ? AND v.end_date >= ?")
            params.extend((f"{year}-12-31", f"{year}-01-01"))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT v.*, e.full_name, e.telegram_user_id,
                           manager.telegram_user_id AS manager_telegram_user_id
                    FROM vacations v JOIN employees e ON e.id = v.employee_id
                    LEFT JOIN employees manager ON manager.id = e.manager_id
                    {where} ORDER BY v.start_date, e.full_name COLLATE NOCASE""",
                tuple(params),
            ).fetchall()
        return [self._vacation_view_from_row(row) for row in rows]

    def upsert_reminder_settings(self, settings: ReminderSettings) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO reminder_settings(
                       employee_id, days_before, reminder_time, text_template, enabled
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(employee_id) DO UPDATE SET
                     days_before=excluded.days_before, reminder_time=excluded.reminder_time,
                     text_template=excluded.text_template, enabled=excluded.enabled""",
                (
                    settings.employee_id,
                    settings.days_before,
                    settings.reminder_time.strftime("%H:%M"),
                    settings.text_template,
                    int(settings.enabled),
                ),
            )

    def get_reminder_settings(
        self, employee_id: int, default_days: int, default_time: str, default_text: str
    ) -> ReminderSettings:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reminder_settings WHERE employee_id = ?", (employee_id,)
            ).fetchone()
        if row:
            return ReminderSettings(
                employee_id=int(row["employee_id"]),
                days_before=int(row["days_before"]),
                reminder_time=time.fromisoformat(str(row["reminder_time"])),
                text_template=str(row["text_template"]),
                enabled=bool(row["enabled"]),
            )
        return ReminderSettings(
            employee_id=employee_id,
            days_before=default_days,
            reminder_time=time.fromisoformat(default_time),
            text_template=default_text,
            enabled=True,
        )

    def list_due_reminders(
        self, today: date, current_time: time
    ) -> list[tuple[VacationView, ReminderSettings]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT v.*, e.full_name, e.telegram_user_id,
                          manager.telegram_user_id AS manager_telegram_user_id,
                          CAST(julianday(v.start_date) - julianday(?) AS INTEGER)
                              AS notification_days,
                          rs.days_before, rs.reminder_time, rs.text_template, rs.enabled
                   FROM vacations v
                   JOIN employees e ON e.id = v.employee_id
                   LEFT JOIN employees manager ON manager.id = e.manager_id
                   JOIN reminder_settings rs ON rs.employee_id = e.id
                   LEFT JOIN sent_reminders sr
                     ON sr.vacation_id = v.id AND sr.reminder_date = ?
                   WHERE rs.enabled = 1 AND e.telegram_user_id IS NOT NULL
                     AND (date(v.start_date, '-14 days') = ?
                          OR date(v.start_date, '-1 day') = ?)
                     AND rs.reminder_time <= ? AND sr.vacation_id IS NULL
                   ORDER BY v.start_date""",
                (
                    today.isoformat(),
                    today.isoformat(),
                    today.isoformat(),
                    today.isoformat(),
                    current_time.strftime("%H:%M"),
                ),
            ).fetchall()
        result = []
        for row in rows:
            vacation = self._vacation_view_from_row(row)
            settings = ReminderSettings(
                employee_id=vacation.employee_id,
                days_before=int(row["notification_days"]),
                reminder_time=time.fromisoformat(str(row["reminder_time"])),
                text_template=str(row["text_template"]),
                enabled=bool(row["enabled"]),
            )
            result.append((vacation, settings))
        return result

    def mark_reminder_sent(self, vacation_id: int, reminder_date: date) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO sent_reminders(vacation_id, reminder_date, sent_at)
                   VALUES (?, ?, ?)""",
                (
                    vacation_id,
                    reminder_date.isoformat(),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    @staticmethod
    def _employee_from_row(row: sqlite3.Row) -> Employee:
        telegram_id = row["telegram_user_id"]
        return Employee(
            id=int(row["id"]),
            full_name=str(row["full_name"]),
            telegram_user_id=int(telegram_id) if telegram_id is not None else None,
            role=str(row["role"]),
            manager_id=int(row["manager_id"])
            if row["manager_id"] is not None
            else None,
            telegram_username=(
                str(row["telegram_username"])
                if row["telegram_username"] is not None
                else None
            ),
            telegram_first_name=(
                str(row["telegram_first_name"])
                if row["telegram_first_name"] is not None
                else None
            ),
            telegram_last_name=(
                str(row["telegram_last_name"])
                if row["telegram_last_name"] is not None
                else None
            ),
            telegram_tag=(
                str(row["telegram_tag"]) if row["telegram_tag"] is not None else None
            ),
            birth_date=(
                date.fromisoformat(str(row["birth_date"]))
                if row["birth_date"] is not None
                else None
            ),
            phone=str(row["phone"]) if row["phone"] is not None else None,
            email=str(row["email"]) if row["email"] is not None else None,
            profile_completed=bool(row["profile_completed"]),
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _vacation_from_row(row: sqlite3.Row) -> Vacation:
        return Vacation(
            id=int(row["id"]),
            employee_id=int(row["employee_id"]),
            start_date=date.fromisoformat(str(row["start_date"])),
            end_date=date.fromisoformat(str(row["end_date"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _vacation_view_from_row(row: sqlite3.Row) -> VacationView:
        telegram_id = row["telegram_user_id"]
        manager_telegram_id = row["manager_telegram_user_id"]
        return VacationView(
            id=int(row["id"]),
            employee_id=int(row["employee_id"]),
            employee_name=str(row["full_name"]),
            telegram_user_id=int(telegram_id) if telegram_id is not None else None,
            manager_telegram_user_id=(
                int(manager_telegram_id) if manager_telegram_id is not None else None
            ),
            start_date=date.fromisoformat(str(row["start_date"])),
            end_date=date.fromisoformat(str(row["end_date"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _scheduled_notification_from_row(row: sqlite3.Row) -> ScheduledNotification:
        return ScheduledNotification(
            id=int(row["id"]),
            scheduled_at=datetime.fromisoformat(str(row["scheduled_at"])),
            message_text=str(row["message_text"]),
            status=str(row["status"]),
            created_by_employee_id=int(row["created_by_employee_id"]),
            delivered_count=int(row["delivered_count"]),
            failed_count=int(row["failed_count"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            sent_at=(
                datetime.fromisoformat(str(row["sent_at"]))
                if row["sent_at"] is not None
                else None
            ),
        )
