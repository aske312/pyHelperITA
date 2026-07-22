import asyncio
import sqlite3
from datetime import date, datetime, time
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from bot.access import can_manage, visible_contacts
from bot.config import Settings
from bot.db import Database, format_display_name, validate_full_name
from bot.export import export_vacations_xlsx
from bot.reminders import SystemNotificationSender
from bot.service import VacationService


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        database_path=tmp_path / "test.sqlite3",
        owner_telegram_id=None,
        owner_full_name="",
    )
    database = Database(settings.database_path)
    instance = VacationService(database, settings)
    instance.initialize()
    return instance


def test_employee_and_vacation_roundtrip(service):
    employee = service.register_employee("  Иванов   Иван Иванович  ", 123456)
    vacation = service.add_vacation(employee.id, date(2026, 8, 1), date(2026, 8, 14))

    stored = service.database.list_vacations(employee_id=employee.id, year=2026)

    assert employee.full_name == "Иванов Иван Иванович"
    assert vacation.days_count == 14
    assert len(stored) == 1
    assert stored[0].employee_name == employee.full_name


def test_overlapping_vacation_is_rejected(service):
    employee = service.register_employee("Петров Петр Петрович")
    service.add_vacation(employee.id, date(2026, 7, 1), date(2026, 7, 10))

    with pytest.raises(ValueError, match="пересекается"):
        service.add_vacation(employee.id, date(2026, 7, 10), date(2026, 7, 20))


def test_existing_employee_can_be_bound_to_telegram(service):
    employee = service.register_employee("Кузнецов Алексей")

    updated = service.database.bind_telegram(employee.id, 555)

    assert updated.telegram_user_id == 555
    assert service.database.get_employee_by_telegram(555) == updated


def test_database_backup_contains_data(service, tmp_path):
    service.register_employee("Орлова Мария")

    backup_path = service.database.backup(tmp_path / "backups" / "copy.sqlite3")
    backup_database = Database(backup_path)

    assert backup_path.is_file()
    assert [item.full_name for item in backup_database.list_employees()] == [
        "Орлова Мария"
    ]


def test_due_reminder_is_returned_once(service):
    employee = service.register_employee("Сидорова Анна Сергеевна", 777)
    vacation = service.add_vacation(employee.id, date(2026, 8, 1), date(2026, 8, 5))

    due = service.database.list_due_reminders(date(2026, 7, 18), time(9, 0))
    service.database.mark_reminder_sent(vacation.id, date(2026, 7, 18))
    due_after_send = service.database.list_due_reminders(date(2026, 7, 18), time(9, 30))
    due_one_day = service.database.list_due_reminders(date(2026, 7, 31), time(9, 0))

    assert len(due) == 1
    assert due[0][0].id == vacation.id
    assert due[0][1].days_before == 14
    assert due_after_send == []
    assert due_one_day[0][1].days_before == 1


def test_roles_manager_and_xlsx_export(service, tmp_path):
    manager = service.register_employee("Руководитель Отдела", 100)
    service.database.update_employee(manager.id, is_team_lead=True)
    employee = service.register_employee("Сотрудник Отдела", 200)
    updated = service.database.update_employee(
        employee.id, team_lead_id=manager.id, set_team_lead=True
    )
    service.add_vacation(updated.id, date(2026, 9, 1), date(2026, 9, 7))

    items = service.database.list_vacations(employee_id=updated.id)
    output = export_vacations_xlsx(items, tmp_path / "vacations.xlsx")

    assert updated.team_lead_id == manager.id
    assert items[0].team_lead_telegram_user_id == manager.telegram_user_id
    with ZipFile(output) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
        ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))


def test_telegram_onboarding_preserves_existing_user(service):
    created = service.database.upsert_telegram_user(
        9001, "old_name", "Иван", "Иванов", is_owner=False
    )
    completed = service.database.complete_profile(created.id, "Иванов Иван Иванович")
    service.add_vacation(completed.id, date(2027, 1, 1), date(2027, 1, 5))

    returned = service.database.upsert_telegram_user(
        9001, "new_name", "Иван", "Иванов", is_owner=False
    )

    assert returned.id == completed.id
    assert returned.full_name == "Иванов Иван Иванович"
    assert returned.telegram_username == "new_name"
    assert returned.telegram_tag == "@new_name"
    assert returned.profile_completed is True
    assert len(service.database.list_vacations(employee_id=returned.id)) == 1


def test_profile_fields_and_broadcast_recipients(service):
    employee = service.database.upsert_telegram_user(
        1234, "employee_tag", "Иван", "Иванов", is_owner=False
    )
    employee = service.database.complete_profile(employee.id, "Иванов Иван Иванович")
    updated = service.database.update_profile(
        employee.id,
        birth_date=date(1990, 5, 17),
        phone="+7 999 123-45-67",
        email="USER@EXAMPLE.COM",
    )

    assert updated.telegram_tag == "@employee_tag"
    assert updated.birth_date == date(1990, 5, 17)
    assert updated.phone == "+7 999 123-45-67"
    assert updated.email == "user@example.com"
    assert service.database.list_notification_recipients(("employee",)) == [1234]


def test_vacation_anomalies_are_detected(service):
    employee = service.register_employee("Смирнов Сергей Сергеевич", 5678)
    service.add_vacation(employee.id, date(2027, 6, 1), date(2027, 6, 28))
    vacation = service.add_vacation(employee.id, date(2027, 7, 3), date(2027, 7, 3))

    anomalies = service.vacation_anomalies(vacation)

    assert any("суммарно 29" in item for item in anomalies)
    assert any("один день" in item for item in anomalies)
    assert any("выходной" in item for item in anomalies)


def test_scheduled_notification_lifecycle(service):
    admin = service.register_employee("Админов Андрей", 42)
    service.database.update_employee(admin.id, role="owner")
    scheduled = service.database.add_scheduled_notification(
        datetime(2027, 8, 10, 9, 30), "Общее уведомление", admin.id
    )

    assert (
        service.database.list_due_scheduled_notifications(datetime(2027, 8, 10, 9, 29))
        == []
    )
    assert service.database.list_due_scheduled_notifications(
        datetime(2027, 8, 10, 9, 30)
    ) == [scheduled]

    service.database.finish_scheduled_notification(scheduled.id, 10, 2)
    finished = service.database.get_scheduled_notification(scheduled.id)

    assert finished.status == "sent"
    assert finished.delivered_count == 10
    assert finished.failed_count == 2
    assert finished.sent_at is not None


@pytest.mark.parametrize(
    "value",
    ["Иванов Иван", "Иванов Иван Иванович"],
)
def test_supported_name_formats(value):
    assert validate_full_name(value) == value


def test_admin_is_created_from_settings(tmp_path):
    settings = Settings(
        database_path=tmp_path / "admin.sqlite3",
        owner_telegram_id=42,
        owner_full_name="Петров Пётр Петрович",
    )
    service = VacationService(Database(settings.database_path), settings)

    service.initialize()
    admin = service.database.get_employee_by_telegram(42)

    assert admin is not None
    assert admin.role == "owner"
    assert admin.full_name == "Петров Пётр Петрович"
    assert admin.profile_completed is True


@pytest.mark.parametrize("value", ["Иванов", "Иван123 Иван", "Иванов И.", ""])
def test_full_name_is_required(value):
    with pytest.raises(ValueError):
        validate_full_name(value)


def test_profile_workplace_fields_and_vacation_update(service):
    employee = service.register_employee("Новиков Николай", 321)
    updated = service.database.update_profile(
        employee.id,
        location="Санкт-Петербург",
        office_city="Москва",
        work_format="hybrid",
        grade="Middle",
        direction="DEV",
        project_name="Лаба",
        project_start_date=date(2026, 10, 1),
    )
    vacation = service.add_vacation(employee.id, date(2027, 2, 1), date(2027, 2, 7))
    changed = service.database.update_vacation(
        vacation.id, date(2027, 2, 2), date(2027, 2, 10)
    )

    assert updated.location == "Санкт-Петербург"
    assert updated.office_city == "Москва"
    assert updated.work_format == "hybrid"
    assert updated.grade == "Middle"
    assert updated.direction == "DEV"
    assert updated.project_name == "Лаба"
    assert updated.project_start_date == date(2026, 10, 1)
    assert changed.start_date == date(2027, 2, 2)
    assert changed.end_date == date(2027, 2, 10)


def test_new_roles_are_supported(service):
    team_lead = service.register_employee("Лидер Команды", 654)
    owner = service.register_employee("Владелец Компании", 987)

    team_lead = service.database.update_employee(team_lead.id, is_team_lead=True)
    owner = service.database.update_employee(owner.id, role="owner")

    assert team_lead.role == "employee"
    assert team_lead.is_team_lead
    assert owner.role == "owner"
    with pytest.raises(ValueError):
        service.database.update_employee(team_lead.id, role="admin")


def test_legacy_admin_and_manager_roles_are_migrated(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                telegram_user_id INTEGER UNIQUE,
                telegram_username TEXT, telegram_first_name TEXT,
                telegram_last_name TEXT, telegram_tag TEXT, birth_date TEXT,
                phone TEXT, email TEXT,
                profile_completed INTEGER NOT NULL DEFAULT 1,
                role TEXT NOT NULL DEFAULT 'employee'
                    CHECK (role IN ('employee', 'manager', 'admin')),
                manager_id INTEGER, is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            INSERT INTO employees(full_name, role, created_at)
            VALUES ('Старый Администратор', 'admin', '2026-01-01T00:00:00'),
                   ('Старый Руководитель', 'manager', '2026-01-01T00:00:00');
            """
        )
    database = Database(path)
    database.initialize()

    roles = {item.full_name: item.role for item in database.list_employees()}

    assert roles["Старый Администратор"] == "owner"
    assert roles["Старый Руководитель"] == "employee"
    migrated_lead = database.find_employee("Старый Руководитель")
    assert migrated_lead is not None and migrated_lead.is_team_lead


def test_employee_vacation_can_be_deleted(service):
    employee = service.register_employee("Удаляев Андрей", 741)
    vacation = service.add_vacation(employee.id, date(2027, 4, 1), date(2027, 4, 5))

    service.database.delete_vacation(vacation.id)

    assert service.database.list_vacations(employee_id=employee.id) == []
    with pytest.raises(LookupError):
        service.database.get_vacation(vacation.id)


def test_intermediate_owner_schema_renames_team_lead_column(tmp_path):
    path = tmp_path / "intermediate.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                telegram_user_id INTEGER UNIQUE,
                role TEXT NOT NULL DEFAULT 'employee'
                    CHECK (role IN ('employee', 'team_lead', 'owner')),
                manager_id INTEGER,
                profile_completed INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            INSERT INTO employees(full_name, role, created_at)
            VALUES ('Владелец Старой Схемы', 'owner', '2026-01-01T00:00:00');
            """
        )
    database = Database(path)
    database.initialize()

    employee = database.list_employees()[0]

    assert employee.role == "owner"
    assert employee.team_lead_id is None


def test_role_access_matrix_and_guest_contacts(service):
    owner = service.register_employee("Владелец Матрицы", 1001)
    lead = service.register_employee("Тимлид Матрицы", 1002)
    employee = service.register_employee("Сотрудник Матрицы", 1003)
    outsider = service.register_employee("Чужой Сотрудник", 1004)
    guest = service.register_employee("Гость Матрицы", 1005)
    owner = service.database.update_employee(owner.id, role="owner")
    lead = service.database.update_employee(lead.id, is_team_lead=True)
    employee = service.database.update_employee(
        employee.id, team_lead_id=lead.id, set_team_lead=True
    )
    guest = service.database.update_employee(
        guest.id, role="guest", team_lead_id=lead.id, set_team_lead=True
    )

    assert can_manage(owner, outsider)
    assert can_manage(lead, employee)
    assert can_manage(lead, guest)
    assert not can_manage(lead, outsider)
    assert can_manage(employee, employee)
    assert not can_manage(employee, guest)
    assert visible_contacts(guest, service.database.list_employees()) == [lead]


def test_notification_role_groups(service):
    owner = service.register_employee("Владелец Рассылки", 2001)
    employee = service.register_employee("Получатель Рассылки", 2002)
    guest = service.register_employee("Гость Рассылки", 2003)
    owner = service.database.update_employee(owner.id, role="owner")
    service.database.update_employee(guest.id, role="guest")
    notification = service.database.add_scheduled_notification(
        datetime(2028, 1, 1, 10, 0), "Для сотрудников", owner.id, ("employee",)
    )

    assert service.database.list_notification_recipients(
        notification.recipient_roles
    ) == [employee.telegram_user_id]

    changed = service.database.update_notification_roles(
        notification.id, ("team_lead", "guest")
    )

    assert changed.recipient_roles == ("team_lead", "guest")
    assert service.database.list_notification_recipients(changed.recipient_roles) == [
        guest.telegram_user_id
    ]


def test_name_normalization_deduplication_and_display(service):
    employee = service.register_employee("  иВАНОВ   иВАН иВАНОВИЧ ")

    assert employee.full_name == "Иванов Иван Иванович"
    assert format_display_name(employee.full_name) == "Иванов И.И."
    assert service.database.find_employee("ИВАНОВ ИВАН ИВАНОВИЧ") == employee
    with pytest.raises(ValueError, match="уже существует"):
        service.register_employee("иванов иван иванович")


def test_mentor_and_optional_profile_fields(service):
    mentor = service.register_employee("Петров Петр Петрович")
    employee = service.register_employee("Сидоров Семен Сергеевич")

    employee = service.database.update_employee(
        employee.id, mentor_id=mentor.id, set_mentor=True
    )
    employee = service.database.update_profile(
        employee.id,
        email="work@example.com",
        personal_email="personal@example.com",
        english_level="B2",
        employment_date=date(2026, 7, 1),
        work_format="remote",
    )

    assert employee.mentor_id == mentor.id
    assert employee.personal_email == "personal@example.com"
    assert employee.english_level == "B2"
    assert employee.employment_date == date(2026, 7, 1)
    assert employee.work_format == "remote"
    with pytest.raises(ValueError, match="самому себе"):
        service.database.update_employee(
            employee.id, mentor_id=employee.id, set_mentor=True
        )


def test_team_lead_notification_group_is_property_based(service):
    lead = service.register_employee("Лебедев Леонид", 3001)
    employee = service.register_employee("Орлов Олег", 3002)
    service.database.update_employee(lead.id, is_team_lead=True)

    assert service.database.list_notification_recipients(("team_lead",)) == [
        lead.telegram_user_id
    ]
    assert (
        employee.telegram_user_id
        not in service.database.list_notification_recipients(("team_lead",))
    )


def test_team_lifecycle_syncs_team_lead_assignment(service):
    lead = service.register_employee("Командов Алексей", 4101)
    member = service.register_employee("Участников Борис", 4102)
    lead = service.database.update_employee(lead.id, is_team_lead=True)

    team = service.database.create_team("Платформа", lead.id)
    added = service.database.add_team_member(team.id, member.id)

    assert team.name == "Платформа"
    assert team.lead_id == lead.id
    assert added.team_lead_id == lead.id
    assert service.database.list_team_members(team.id) == [added]

    assert service.database.remove_team_member(team.id, member.id)
    assert service.database.list_team_members(team.id) == []
    assert service.database.get_employee(member.id).team_lead_id is None


def test_employee_can_only_belong_to_one_team(service):
    first_lead = service.register_employee("Первый Руководитель", 4201)
    second_lead = service.register_employee("Второй Руководитель", 4202)
    member = service.register_employee("Общий Сотрудник", 4203)
    first_lead = service.database.update_employee(first_lead.id, is_team_lead=True)
    second_lead = service.database.update_employee(second_lead.id, is_team_lead=True)
    first = service.database.create_team("Первая команда", first_lead.id)
    second = service.database.create_team("Вторая команда", second_lead.id)

    service.database.add_team_member(first.id, member.id)
    service.database.add_team_member(second.id, member.id)

    assert service.database.list_team_members(first.id) == []
    assert [item.id for item in service.database.list_team_members(second.id)] == [
        member.id
    ]
    assert service.database.get_employee(member.id).team_lead_id == second_lead.id


def test_command_menus_hide_owner_commands(service):
    from bot.bot import commands_for_employee

    employee = service.register_employee("Меню Сотрудника", 4301)
    lead = service.register_employee("Меню Тимлида", 4302)
    owner = service.register_employee("Меню Владельца", 4303)
    lead = service.database.update_employee(lead.id, is_team_lead=True)
    owner = service.database.update_employee(owner.id, role="owner")

    employee_commands = {item.command for item in commands_for_employee(employee)}
    lead_commands = {item.command for item in commands_for_employee(lead)}
    owner_commands = {item.command for item in commands_for_employee(owner)}

    assert {"broadcast", "export", "guest"}.isdisjoint(employee_commands)
    assert {"broadcast", "export", "guest"}.isdisjoint(lead_commands)
    assert {"employees", "invite_team", "dismiss_team"} <= lead_commands
    assert {"team", "team_create", "delete_team"}.isdisjoint(lead_commands)
    assert {
        "staff",
        "notifications",
        "export",
        "guest",
        "team_create",
        "delete_team",
    } <= owner_commands
    assert {"broadcast", "reminder", "employees"}.isdisjoint(owner_commands)


def test_delete_team_clears_member_assignments(service):
    lead = service.register_employee("Удаляев Руководитель", 5101)
    member = service.register_employee("Освобожденов Сотрудник", 5102)
    lead = service.database.update_employee(lead.id, is_team_lead=True)
    team = service.database.create_team("Временная команда", lead.id)
    service.database.add_team_member(team.id, member.id)

    service.database.delete_team(team.id)

    assert service.database.list_teams() == []
    assert service.database.get_employee(member.id).team_lead_id is None
    assert service.database.get_employee(lead.id).is_team_lead is True


def test_repeating_team_notification_lifecycle(service):
    lead = service.register_employee("Оповещаев Руководитель", 6101)
    member = service.register_employee("Получаев Сотрудник", 6102)
    lead = service.database.update_employee(lead.id, is_team_lead=True)
    team = service.database.create_team("Команда оповещений", lead.id)
    service.database.add_team_member(team.id, member.id)

    notification = service.database.add_scheduled_notification(
        datetime(2028, 2, 1, 10, 0),
        "Командное оповещение",
        lead.id,
        (),
        target_team_id=team.id,
        repeat_interval_minutes=60,
        repeat_count=3,
    )

    assert service.database.list_notification_recipients((), team.id) == [
        lead.telegram_user_id,
        member.telegram_user_id,
    ]

    service.database.finish_scheduled_notification(notification.id, 2, 0)
    repeated = service.database.get_scheduled_notification(notification.id)

    assert repeated.status == "pending"
    assert repeated.scheduled_at == datetime(2028, 2, 1, 11, 0)
    assert repeated.repeats_remaining == 2
    assert repeated.delivered_count == 2

    service.database.update_notification_schedule(notification.id, 1440, 5)
    changed = service.database.get_scheduled_notification(notification.id)

    assert changed.repeat_interval_minutes == 1440
    assert changed.repeats_remaining == 5


def test_delete_employee_removes_regular_employee(service):
    employee = service.register_employee("Удаляемов Сотрудник", 6201)

    service.database.delete_employee(employee.id)

    with pytest.raises(LookupError):
        service.database.get_employee(employee.id)


def test_notification_supports_selected_employees(service):
    owner = service.register_employee("Точечный Владелец", 7101)
    first = service.register_employee("Первый Получатель", 7102)
    second = service.register_employee("Второй Получатель", 7103)
    owner = service.database.update_employee(owner.id, role="owner")

    notification = service.database.add_scheduled_notification(
        datetime(2028, 3, 1, 9, 0),
        "Точечное сообщение",
        owner.id,
        (),
        recipient_employee_ids=(first.id, second.id),
    )

    assert notification.recipient_employee_ids == (first.id, second.id)
    assert service.database.list_notification_recipients(
        (), recipient_employee_ids=notification.recipient_employee_ids
    ) == [first.telegram_user_id, second.telegram_user_id]


class RecordingBot:
    def __init__(self):
        self.messages = []
        self.command_menus = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def set_my_commands(self, commands, scope=None):
        self.command_menus.append((commands, scope))


def test_start_command_menu_helper_exists(service):
    from bot.bot import set_employee_command_menu

    employee = service.register_employee("Стартов Проверочный", 8101)
    bot = RecordingBot()

    asyncio.run(set_employee_command_menu(bot, employee))

    assert len(bot.command_menus) == 1


def test_mandatory_team_lead_notifications(service):
    lead = service.register_employee("Лид Тестовый", 8201)
    member = service.register_employee("Сотрудник Тестовый", 8202)
    service.database.update_employee(lead.id, is_team_lead=True)
    service.database.update_employee(
        member.id, team_lead_id=lead.id, set_team_lead=True
    )
    service.database.update_profile(
        member.id,
        birth_date=date(1995, 7, 23),
        employment_date=date(2026, 4, 23),
    )
    service.add_vacation(member.id, date(2026, 8, 10), date(2026, 8, 20))
    bot = RecordingBot()
    sender = SystemNotificationSender(service.database, service.settings, bot)

    sent = asyncio.run(sender.send_due(datetime(2026, 7, 23, 9, 30)))
    repeated = asyncio.run(sender.send_due(datetime(2026, 7, 23, 9, 31)))

    assert sent == 3
    assert repeated == 0
    assert len(bot.messages) == 3
    assert all(chat_id == lead.telegram_user_id for chat_id, _ in bot.messages)
    assert any("день рождения" in text for _, text in bot.messages)
    assert any("испытательный срок" in text for _, text in bot.messages)
    assert any("добавил отпуск" in text for _, text in bot.messages)


def test_birthday_and_probation_wait_until_0930(service):
    lead = service.register_employee("Утренний Руководитель", 8301)
    member = service.register_employee("Утренний Сотрудник", 8302)
    service.database.update_employee(lead.id, is_team_lead=True)
    service.database.update_employee(
        member.id, team_lead_id=lead.id, set_team_lead=True
    )
    service.database.update_profile(
        member.id,
        birth_date=date(1990, 7, 23),
        employment_date=date(2026, 4, 23),
    )
    bot = RecordingBot()
    sender = SystemNotificationSender(service.database, service.settings, bot)

    sent = asyncio.run(sender.send_due(datetime(2026, 7, 23, 9, 29)))

    assert sent == 0
    assert bot.messages == []
