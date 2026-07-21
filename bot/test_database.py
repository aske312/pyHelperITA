from datetime import date, datetime, time
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from bot.config import Settings
from bot.db import Database, validate_full_name
from bot.export import export_vacations_xlsx
from bot.service import VacationService


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        database_path=tmp_path / "test.sqlite3",
        admin_telegram_id=None,
        admin_full_name="",
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
    manager = service.register_employee("Руководитель", 100)
    service.database.update_employee(manager.id, role="manager")
    employee = service.register_employee("Сотрудник", 200)
    updated = service.database.update_employee(
        employee.id, manager_id=manager.id, set_manager=True
    )
    service.add_vacation(updated.id, date(2026, 9, 1), date(2026, 9, 7))

    items = service.database.list_vacations(employee_id=updated.id)
    output = export_vacations_xlsx(items, tmp_path / "vacations.xlsx")

    assert updated.manager_id == manager.id
    assert items[0].manager_telegram_user_id == manager.telegram_user_id
    with ZipFile(output) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
        ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))


def test_telegram_onboarding_preserves_existing_user(service):
    created = service.database.upsert_telegram_user(
        9001, "old_name", "Иван", "Иванов", is_admin=False
    )
    completed = service.database.complete_profile(created.id, "Иванов Иван Иванович")
    service.add_vacation(completed.id, date(2027, 1, 1), date(2027, 1, 5))

    returned = service.database.upsert_telegram_user(
        9001, "new_name", "Иван", "Иванов", is_admin=False
    )

    assert returned.id == completed.id
    assert returned.full_name == "Иванов Иван Иванович"
    assert returned.telegram_username == "new_name"
    assert returned.telegram_tag == "@new_name"
    assert returned.profile_completed is True
    assert len(service.database.list_vacations(employee_id=returned.id)) == 1


def test_profile_fields_and_broadcast_recipients(service):
    employee = service.database.upsert_telegram_user(
        1234, "employee_tag", "Иван", "Иванов", is_admin=False
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
    assert service.database.list_notification_recipients() == [1234]


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
    service.database.update_employee(admin.id, role="admin")
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
    ["Иванов Иван", "Иванов Иван Иванович", "Иванов И.И.", "Сидорова А.С."],
)
def test_supported_name_formats(value):
    assert validate_full_name(value) == value


def test_admin_is_created_from_settings(tmp_path):
    settings = Settings(
        database_path=tmp_path / "admin.sqlite3",
        admin_telegram_id=42,
        admin_full_name="Петров Пётр Петрович",
    )
    service = VacationService(Database(settings.database_path), settings)

    service.initialize()
    admin = service.database.get_employee_by_telegram(42)

    assert admin is not None
    assert admin.role == "admin"
    assert admin.full_name == "Петров Пётр Петрович"
    assert admin.profile_completed is True


@pytest.mark.parametrize("value", ["Иванов", "Иван123 Иван", "Иванов И.", ""])
def test_full_name_is_required(value):
    with pytest.raises(ValueError):
        validate_full_name(value)
