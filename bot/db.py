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
    Team,
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
    personal_email TEXT,
    english_level TEXT,
    employment_date TEXT,
    location TEXT,
    office_city TEXT,
    work_format TEXT CHECK (work_format IN ('hybrid', 'remote', 'office')),
    grade TEXT CHECK (grade IN ('Intern', 'Junior', 'Middle', 'Senior', 'RM1')),
    direction TEXT CHECK (direction IN ('SA', 'QA', 'DEV', 'HR')),
    project_name TEXT,
    project_start_date TEXT,
    profile_completed INTEGER NOT NULL DEFAULT 1 CHECK (profile_completed IN (0, 1)),
    role TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('guest', 'employee', 'owner')),
    is_team_lead INTEGER NOT NULL DEFAULT 0 CHECK (is_team_lead IN (0, 1)),
    team_lead_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    mentor_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    lead_id INTEGER NOT NULL UNIQUE REFERENCES employees(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL UNIQUE REFERENCES employees(id) ON DELETE CASCADE,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (team_id, employee_id)
);
CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);CREATE TABLE IF NOT EXISTS vacations (
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
    recipient_roles TEXT NOT NULL DEFAULT 'owner,team_lead,employee',
    delivered_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_notifications_due
ON scheduled_notifications(status, scheduled_at);
"""


def validate_full_name(value: str) -> str:
    parts = " ".join(value.split()).split()
    if len(parts) not in {2, 3}:
        raise ValueError("Введите полное ФИО")
    for part in parts:
        letters = part.replace("-", "")
        if len(letters) < 2 or not letters.isalpha():
            raise ValueError("ФИО должно содержать только буквы и дефис")
    return " ".join(
        "-".join(piece.capitalize() for piece in part.split("-")) for part in parts
    )


def format_display_name(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) < 2:
        return full_name
    initials = "".join(f"{part[0].upper()}." for part in parts[1:])
    return f"{parts[0]} {initials}"
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

    @staticmethod
    def _migrate_employee_roles(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'employees'"
        ).fetchone()
        if row is None or "'admin'" not in str(row["sql"]):
            return
        connection.execute("PRAGMA foreign_keys = OFF")
        columns = {item["name"] for item in connection.execute("PRAGMA table_info(employees)")}
        lead_column = "manager_id" if "manager_id" in columns else "team_lead_id"
        connection.executescript(
            f"""
            CREATE TABLE employees_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                telegram_user_id INTEGER UNIQUE,
                telegram_username TEXT, telegram_first_name TEXT,
                telegram_last_name TEXT, telegram_tag TEXT, birth_date TEXT,
                phone TEXT, email TEXT, personal_email TEXT, english_level TEXT,
                employment_date TEXT, location TEXT, office_city TEXT,
                work_format TEXT CHECK (work_format IN ('hybrid', 'remote', 'office')),
    grade TEXT CHECK (grade IN ('Intern', 'Junior', 'Middle', 'Senior', 'RM1')),
    direction TEXT CHECK (direction IN ('SA', 'QA', 'DEV', 'HR')),
    project_name TEXT,
    project_start_date TEXT,
                profile_completed INTEGER NOT NULL DEFAULT 1 CHECK (profile_completed IN (0, 1)),
                role TEXT NOT NULL DEFAULT 'employee'
                    CHECK (role IN ('employee', 'team_lead', 'owner')),
                team_lead_id INTEGER REFERENCES employees_new(id) ON DELETE SET NULL,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL
            );
            INSERT INTO employees_new(
                id, full_name, telegram_user_id, telegram_username,
                telegram_first_name, telegram_last_name, telegram_tag, birth_date,
                phone, email, profile_completed, role, team_lead_id, is_active, created_at
            )
            SELECT id, full_name, telegram_user_id, telegram_username,
                telegram_first_name, telegram_last_name, telegram_tag, birth_date,
                phone, email, profile_completed,
                CASE role WHEN 'admin' THEN 'owner'
                          WHEN 'manager' THEN 'team_lead' ELSE 'employee' END,
                {lead_column}, is_active, created_at
            FROM employees;
            DROP TABLE employees;
            ALTER TABLE employees_new RENAME TO employees;
            """
        )
        connection.execute("PRAGMA foreign_keys = ON")
    @staticmethod
    def _migrate_guest_role(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'employees'"
        ).fetchone()
        if row is None or "'guest'" in str(row["sql"]):
            return
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            CREATE TABLE employees_guest_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                telegram_user_id INTEGER UNIQUE,
                telegram_username TEXT, telegram_first_name TEXT,
                telegram_last_name TEXT, telegram_tag TEXT, birth_date TEXT,
                phone TEXT, email TEXT, personal_email TEXT, english_level TEXT,
                employment_date TEXT, location TEXT, office_city TEXT,
                work_format TEXT CHECK (work_format IN ('hybrid', 'remote', 'office')),
                grade TEXT CHECK (grade IN ('Intern', 'Junior', 'Middle', 'Senior', 'RM1')),
                direction TEXT CHECK (direction IN ('SA', 'QA', 'DEV', 'HR')),
                project_name TEXT, project_start_date TEXT,
                profile_completed INTEGER NOT NULL DEFAULT 1 CHECK (profile_completed IN (0, 1)),
                role TEXT NOT NULL DEFAULT 'employee'
                    CHECK (role IN ('guest', 'employee', 'owner')),
                is_team_lead INTEGER NOT NULL DEFAULT 0,
                team_lead_id INTEGER REFERENCES employees_guest_new(id) ON DELETE SET NULL,
                mentor_id INTEGER REFERENCES employees_guest_new(id) ON DELETE SET NULL,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL
            );
            INSERT INTO employees_guest_new (id, full_name, telegram_user_id, telegram_username, telegram_first_name, telegram_last_name, telegram_tag, birth_date, phone, email, personal_email, english_level, employment_date, location, office_city, work_format, grade, direction, project_name, project_start_date, profile_completed, role, is_team_lead, team_lead_id, mentor_id, is_active, created_at)
            SELECT id, full_name, telegram_user_id, telegram_username, telegram_first_name, telegram_last_name, telegram_tag, birth_date, phone, email, personal_email, english_level, employment_date, location, office_city, work_format, grade, direction, project_name, project_start_date, profile_completed, role, is_team_lead, team_lead_id, mentor_id, is_active, created_at FROM employees;
            DROP TABLE employees;
            ALTER TABLE employees_guest_new RENAME TO employees;
            """
        )
        connection.execute("PRAGMA foreign_keys = ON")
    def initialize(self) -> None:
        with self.connect() as connection:
            self._migrate_employee_roles(connection)
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(employees)")
            }
            if "role" not in columns:
                connection.execute(
                    "ALTER TABLE employees ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'"
                )
            if "manager_id" in columns and "team_lead_id" not in columns:
                connection.execute(
                    "ALTER TABLE employees RENAME COLUMN manager_id TO team_lead_id"
                )
                columns.remove("manager_id")
                columns.add("team_lead_id")
            if "team_lead_id" not in columns:
                connection.execute(
                    "ALTER TABLE employees ADD COLUMN team_lead_id INTEGER"
                )
            if "is_team_lead" not in columns:
                connection.execute(
                    "ALTER TABLE employees ADD COLUMN is_team_lead INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                "UPDATE employees SET is_team_lead = 1, role = 'employee' "
                "WHERE role = 'team_lead'"
            )
            pre_guest_migrations = {
                "telegram_username": "TEXT", "telegram_first_name": "TEXT",
                "telegram_last_name": "TEXT", "telegram_tag": "TEXT",
                "birth_date": "TEXT", "phone": "TEXT", "email": "TEXT",
                "personal_email": "TEXT", "english_level": "TEXT",
                "employment_date": "TEXT", "mentor_id": "INTEGER",
                "location": "TEXT", "office_city": "TEXT", "work_format": "TEXT",
                "grade": "TEXT", "direction": "TEXT", "project_name": "TEXT",
                "project_start_date": "TEXT", "profile_completed": "INTEGER NOT NULL DEFAULT 1",
            }
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(employees)")}
            for name, definition in pre_guest_migrations.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE employees ADD COLUMN {name} {definition}")
            self._migrate_guest_role(connection)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(employees)")}
            migrations = {
                "telegram_username": "TEXT",
                "telegram_first_name": "TEXT",
                "telegram_last_name": "TEXT",
                "telegram_tag": "TEXT",
                "birth_date": "TEXT",
                "phone": "TEXT",
                "email": "TEXT",
                "location": "TEXT",
                "office_city": "TEXT",
                "work_format": "TEXT",
                "grade": "TEXT",
                "direction": "TEXT",
                "project_name": "TEXT",
                "project_start_date": "TEXT",
                "profile_completed": "INTEGER NOT NULL DEFAULT 1",
                "is_team_lead": "INTEGER NOT NULL DEFAULT 0",
                "mentor_id": "INTEGER",
                "personal_email": "TEXT",
                "english_level": "TEXT",
                "employment_date": "TEXT",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE employees ADD COLUMN {name} {definition}"
                    )
            notification_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(scheduled_notifications)"
                )
            }
            if "recipient_roles" not in notification_columns:
                connection.execute(
                    "ALTER TABLE scheduled_notifications ADD COLUMN recipient_roles "
                    "TEXT NOT NULL DEFAULT 'owner,team_lead,employee'"
                )

    def upsert_telegram_user(
        self,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        *,
        is_owner: bool = False,
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
                        "owner" if is_owner else "employee",
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                employee_id = int(cursor.lastrowid)
            else:
                employee_id = existing.id
                role = "owner" if is_owner else existing.role
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
        personal_email: str | None = None,
        english_level: str | None = None,
        employment_date: date | None = None,
        location: str | None = None,
        office_city: str | None = None,
        work_format: str | None = None,
        grade: str | None = None,
        direction: str | None = None,
        project_name: str | None = None,
        project_start_date: date | None = None,
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
        if personal_email is not None:
            values["personal_email"] = personal_email.strip().lower()
        if english_level is not None:
            values["english_level"] = english_level.strip()
        if employment_date is not None:
            values["employment_date"] = employment_date.isoformat()
        if location is not None:
            values["location"] = location.strip()
        if office_city is not None:
            values["office_city"] = office_city.strip()
        if work_format is not None:
            if work_format not in {"hybrid", "remote", "office"}:
                raise ValueError("Формат работы: hybrid, remote или office")
            values["work_format"] = work_format
        if grade is not None:
            if grade not in {"Intern", "Junior", "Middle", "Senior", "RM1"}:
                raise ValueError("Неизвестный грейд")
            values["grade"] = grade
        if direction is not None:
            if direction not in {"SA", "QA", "DEV", "HR"}:
                raise ValueError("Неизвестное направление")
            values["direction"] = direction
        if project_name is not None:
            values["project_name"] = project_name.strip()
            if project_name.strip() == "Нет проекта":
                values["project_start_date"] = None
        if project_start_date is not None:
            values["project_start_date"] = project_start_date.isoformat()
        if values:
            assignments = ", ".join(f"{name} = ?" for name in values)
            with self.connect() as connection:
                connection.execute(
                    f"UPDATE employees SET {assignments} WHERE id = ?",
                    (*values.values(), employee_id),
                )
        return self.get_employee(employee_id)

    def list_notification_recipients(self, roles: tuple[str, ...]) -> list[int]:
        if not roles:
            return []
        role_values = tuple(role for role in roles if role != "team_lead")
        conditions: list[str] = []
        params: list[object] = []
        if role_values:
            conditions.append(f"role IN ({', '.join('?' for _ in role_values)})")
            params.extend(role_values)
        if "team_lead" in roles:
            conditions.append("is_team_lead = 1")
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT DISTINCT telegram_user_id FROM employees
                    WHERE is_active = 1 AND profile_completed = 1
                      AND telegram_user_id IS NOT NULL
                      AND ({' OR '.join(conditions)})""",
                tuple(params),
            ).fetchall()
        return [int(row["telegram_user_id"]) for row in rows]

    def add_scheduled_notification(
        self, scheduled_at: datetime, message_text: str, created_by_employee_id: int,
        recipient_roles: tuple[str, ...] = ("owner", "team_lead", "employee"),
    ) -> ScheduledNotification:
        text = message_text.strip()
        if not text:
            raise ValueError("Текст уведомления не может быть пустым")
        invalid_roles = set(recipient_roles) - {"guest", "employee", "team_lead", "owner"}
        if invalid_roles or not recipient_roles:
            raise ValueError("Некорректные группы получателей")
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO scheduled_notifications(
                       scheduled_at, message_text, created_by_employee_id, recipient_roles, created_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    scheduled_at.isoformat(timespec="minutes"),
                    text,
                    created_by_employee_id,
                    ",".join(recipient_roles),
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

    def update_notification_roles(
        self, notification_id: int, recipient_roles: tuple[str, ...]
    ) -> ScheduledNotification:
        invalid_roles = set(recipient_roles) - {"guest", "employee", "team_lead", "owner"}
        if invalid_roles or not recipient_roles:
            raise ValueError("Некорректные группы получателей")
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE scheduled_notifications SET recipient_roles = ?
                   WHERE id = ? AND status = 'pending'""",
                (",".join(recipient_roles), notification_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("Активное уведомление не найдено")
        return self.get_scheduled_notification(notification_id)
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

    def ensure_owner(self, telegram_user_id: int, full_name: str) -> Employee:
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
                """UPDATE employees SET role = 'owner', full_name = ?,
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
        normalized_name = validate_full_name(full_name)
        if self.find_employee(normalized_name) is not None:
            raise ValueError("Сотрудник с таким ФИО уже существует")
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
        team_lead_id: int | None = None,
        set_team_lead: bool = False,
        is_team_lead: bool | None = None,
        mentor_id: int | None = None,
        set_mentor: bool = False,
    ) -> Employee:
        values: dict[str, object] = {}
        if full_name is not None:
            normalized = validate_full_name(full_name)
            duplicate = self.find_employee(normalized)
            if duplicate is not None and duplicate.id != employee_id:
                raise ValueError("Сотрудник с таким ФИО уже существует")
            values["full_name"] = normalized
        if role is not None:
            if role not in {"guest", "employee", "owner"}:
                raise ValueError("Роль должна быть guest, employee или owner")
            values["role"] = role
        if is_team_lead is not None:
            values["is_team_lead"] = int(is_team_lead)
        if set_mentor:
            if mentor_id == employee_id:
                raise ValueError("Сотрудник не может быть ментором самому себе")
            if mentor_id is not None:
                self.get_employee(mentor_id)
            values["mentor_id"] = mentor_id
        if set_team_lead:
            if team_lead_id == employee_id:
                raise ValueError("Сотрудник не может быть руководителем самому себе")
            if team_lead_id is not None:
                lead = self.get_employee(team_lead_id)
                if not lead.is_team_lead:
                    raise ValueError("Назначенный сотрудник не является тимлидом")
            values["team_lead_id"] = team_lead_id
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
        lookup_key = " ".join(full_name.split()).casefold()
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM employees").fetchall()
        row = next(
            (
                item
                for item in rows
                if " ".join(str(item["full_name"]).split()).casefold() == lookup_key
            ),
            None,
        )
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

    def create_team(self, name: str, lead_id: int) -> Team:
        normalized = " ".join(name.split())
        if len(normalized) < 2:
            raise ValueError("Название команды слишком короткое")
        lead = self.get_employee(lead_id)
        if not lead.is_team_lead:
            raise ValueError("Руководитель команды должен быть тимлидом")
        with self.connect() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO teams(name, lead_id, created_at) VALUES (?, ?, ?)",
                    (normalized, lead_id, datetime.now().isoformat(timespec="seconds")),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("Название или тимлид уже используются другой командой") from error
        return self.get_team(int(cursor.lastrowid))

    def get_team(self, team_id: int) -> Team:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT t.*, e.full_name AS lead_name FROM teams t
                   JOIN employees e ON e.id = t.lead_id WHERE t.id = ?""",
                (team_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"Команда #{team_id} не найдена")
        return self._team_from_row(row)

    def list_teams(self, lead_id: int | None = None) -> list[Team]:
        where = "WHERE t.lead_id = ?" if lead_id is not None else ""
        params: tuple[object, ...] = (lead_id,) if lead_id is not None else ()
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT t.*, e.full_name AS lead_name FROM teams t
                    JOIN employees e ON e.id = t.lead_id {where}
                    ORDER BY t.name COLLATE NOCASE""",
                params,
            ).fetchall()
        return [self._team_from_row(row) for row in rows]

    def list_team_members(self, team_id: int) -> list[Employee]:
        self.get_team(team_id)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT e.* FROM team_members tm
                   JOIN employees e ON e.id = tm.employee_id
                   WHERE tm.team_id = ? ORDER BY e.full_name COLLATE NOCASE""",
                (team_id,),
            ).fetchall()
        return [self._employee_from_row(row) for row in rows]

    def add_team_member(self, team_id: int, employee_id: int) -> Employee:
        team = self.get_team(team_id)
        employee = self.get_employee(employee_id)
        if employee.id == team.lead_id:
            raise ValueError("Тимлида не нужно добавлять в состав команды")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO team_members(team_id, employee_id, joined_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(employee_id) DO UPDATE SET
                     team_id=excluded.team_id, joined_at=excluded.joined_at""",
                (team_id, employee_id, datetime.now().isoformat(timespec="seconds")),
            )
            connection.execute(
                "UPDATE employees SET team_lead_id = ? WHERE id = ?",
                (team.lead_id, employee_id),
            )
        return self.get_employee(employee_id)

    def remove_team_member(self, team_id: int, employee_id: int) -> bool:
        team = self.get_team(team_id)
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM team_members WHERE team_id = ? AND employee_id = ?",
                (team_id, employee_id),
            )
            if cursor.rowcount:
                connection.execute(
                    """UPDATE employees SET team_lead_id = NULL
                       WHERE id = ? AND team_lead_id = ?""",
                    (employee_id, team.lead_id),
                )
        return cursor.rowcount > 0
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

    def get_vacation(self, vacation_id: int) -> Vacation:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM vacations WHERE id = ?", (vacation_id,)
            ).fetchone()
        if row is None:
            raise LookupError(f"Отпуск #{vacation_id} не найден")
        return self._vacation_from_row(row)

    def update_vacation(
        self, vacation_id: int, start_date: date, end_date: date
    ) -> Vacation:
        if end_date < start_date:
            raise ValueError("Дата окончания отпуска раньше даты начала")
        current = self.get_vacation(vacation_id)
        with self.connect() as connection:
            overlap = connection.execute(
                """SELECT 1 FROM vacations
                   WHERE employee_id = ? AND id != ?
                     AND start_date <= ? AND end_date >= ? LIMIT 1""",
                (current.employee_id, vacation_id, end_date.isoformat(), start_date.isoformat()),
            ).fetchone()
            if overlap:
                raise ValueError("Отпуск пересекается с уже сохраненным периодом")
            connection.execute(
                "UPDATE vacations SET start_date = ?, end_date = ? WHERE id = ?",
                (start_date.isoformat(), end_date.isoformat(), vacation_id),
            )
            connection.execute(
                "DELETE FROM sent_reminders WHERE vacation_id = ?", (vacation_id,)
            )
        return self.get_vacation(vacation_id)
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
                           team_lead.telegram_user_id AS team_lead_telegram_user_id
                    FROM vacations v JOIN employees e ON e.id = v.employee_id
                    LEFT JOIN employees team_lead ON team_lead.id = e.team_lead_id
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
                          team_lead.telegram_user_id AS team_lead_telegram_user_id,
                          CAST(julianday(v.start_date) - julianday(?) AS INTEGER)
                              AS notification_days,
                          rs.days_before, rs.reminder_time, rs.text_template, rs.enabled
                   FROM vacations v
                   JOIN employees e ON e.id = v.employee_id
                   LEFT JOIN employees team_lead ON team_lead.id = e.team_lead_id
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
            is_team_lead=bool(row["is_team_lead"]),
            team_lead_id=int(row["team_lead_id"])
            if row["team_lead_id"] is not None
            else None,
            mentor_id=int(row["mentor_id"]) if row["mentor_id"] is not None else None,
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
            location=str(row["location"]) if row["location"] is not None else None,
            office_city=str(row["office_city"]) if row["office_city"] is not None else None,
            work_format=str(row["work_format"]) if row["work_format"] is not None else None,
            email=str(row["email"]) if row["email"] is not None else None,
            personal_email=(str(row["personal_email"])
                            if row["personal_email"] is not None else None),
            english_level=(str(row["english_level"])
                           if row["english_level"] is not None else None),
            employment_date=(date.fromisoformat(str(row["employment_date"]))
                             if row["employment_date"] is not None else None),
            grade=str(row["grade"]) if row["grade"] is not None else None,
            direction=str(row["direction"]) if row["direction"] is not None else None,
            project_name=str(row["project_name"]) if row["project_name"] is not None else None,
            project_start_date=(date.fromisoformat(str(row["project_start_date"]))
                                if row["project_start_date"] is not None else None),
            profile_completed=bool(row["profile_completed"]),
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _team_from_row(row: sqlite3.Row) -> Team:
        return Team(
            id=int(row["id"]),
            name=str(row["name"]),
            lead_id=int(row["lead_id"]),
            lead_name=str(row["lead_name"]),
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
        team_lead_telegram_id = row["team_lead_telegram_user_id"]
        return VacationView(
            id=int(row["id"]),
            employee_id=int(row["employee_id"]),
            employee_name=str(row["full_name"]),
            telegram_user_id=int(telegram_id) if telegram_id is not None else None,
            team_lead_telegram_user_id=(
                int(team_lead_telegram_id) if team_lead_telegram_id is not None else None
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
            recipient_roles=tuple(str(row["recipient_roles"]).split(",")),
            delivered_count=int(row["delivered_count"]),
            failed_count=int(row["failed_count"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            sent_at=(
                datetime.fromisoformat(str(row["sent_at"]))
                if row["sent_at"] is not None
                else None
            ),
        )
