from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import typer

from bot.bot import start_bot
from bot.export import export_vacations_xlsx
from bot.runtime import build_service

app = typer.Typer(help="Корпоративный Telegram-бот помощник", no_args_is_help=True)
employee_app = typer.Typer(help="Управление сотрудниками", no_args_is_help=True)
vacation_app = typer.Typer(help="Управление отпусками", no_args_is_help=True)
database_app = typer.Typer(help="Обслуживание SQLite", no_args_is_help=True)
app.add_typer(employee_app, name="employee")
app.add_typer(vacation_app, name="vacation")
app.add_typer(database_app, name="database")


def parse_date(value: str) -> date:
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise typer.BadParameter("Дата должна быть в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД")


@app.command("init")
def initialize() -> None:
    """Создать структуру локальной базы данных."""
    service = build_service()
    typer.echo(f"База данных готова: {service.settings.database_path}")


@employee_app.command("add")
def add_employee(
    full_name: str = typer.Argument(..., help="ФИО сотрудника в кавычках"),
    telegram_id: int | None = typer.Option(
        None, "--telegram-id", help="Telegram user ID"
    ),
) -> None:
    """Добавить сотрудника и настройки напоминаний по умолчанию."""
    service = build_service()
    try:
        employee = service.register_employee(full_name, telegram_id)
    except Exception as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Добавлен сотрудник #{employee.id}: {employee.full_name}")


@employee_app.command("list")
def list_employees() -> None:
    """Показать активных сотрудников."""
    employees = build_service().database.list_employees()
    if not employees:
        typer.echo("Сотрудников пока нет.")
        return
    typer.echo("ID | Telegram ID | ФИО")
    typer.echo("-" * 60)
    for employee in employees:
        telegram_id = employee.telegram_user_id or "—"
        typer.echo(f"{employee.id} | {telegram_id} | {employee.full_name}")


@employee_app.command("bind-telegram")
def bind_employee_telegram(
    employee_id: int = typer.Argument(..., help="ID сотрудника"),
    telegram_id: int = typer.Argument(..., help="Telegram user ID из команды /my_id"),
) -> None:
    """Привязать Telegram ID к существующему сотруднику."""
    service = build_service()
    try:
        employee = service.database.bind_telegram(employee_id, telegram_id)
    except Exception as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Telegram ID {telegram_id} привязан к {employee.full_name}")


@employee_app.command("rename")
def rename_employee(
    employee_id: int = typer.Argument(..., help="ID сотрудника"),
    full_name: str = typer.Argument(..., help="Новое ФИО"),
) -> None:
    """Изменить ФИО сотрудника."""
    employee = build_service().database.update_employee(
        employee_id, full_name=full_name
    )
    typer.echo(f"Сотрудник #{employee.id} переименован: {employee.full_name}")


@employee_app.command("set-role")
def set_employee_role(
    employee_id: int = typer.Argument(..., help="ID сотрудника"),
    role: str = typer.Argument(..., help="employee, manager или admin"),
) -> None:
    """Назначить права сотрудника."""
    employee = build_service().database.update_employee(employee_id, role=role)
    typer.echo(f"Роль сотрудника #{employee.id}: {employee.role}")


@employee_app.command("set-manager")
def set_employee_manager(
    employee_id: int = typer.Argument(..., help="ID сотрудника"),
    manager_id: int | None = typer.Argument(
        None, help="ID руководителя; без значения — удалить"
    ),
) -> None:
    """Назначить или удалить руководителя сотрудника."""
    employee = build_service().database.update_employee(
        employee_id, manager_id=manager_id, set_manager=True
    )
    typer.echo(
        f"Руководитель сотрудника #{employee.id}: {employee.manager_id or 'не назначен'}"
    )


@vacation_app.command("add")
def add_vacation(
    employee_id: int = typer.Argument(..., help="ID сотрудника"),
    start: str = typer.Argument(..., help="Дата начала"),
    end: str = typer.Argument(..., help="Дата окончания"),
) -> None:
    """Добавить отпуск в SQLite."""
    service = build_service()
    try:
        vacation = service.add_vacation(employee_id, parse_date(start), parse_date(end))
    except Exception as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Отпуск #{vacation.id}: {vacation.start_date:%d.%m.%Y}–"
        f"{vacation.end_date:%d.%m.%Y}, {vacation.days_count} календ. дн."
    )


@vacation_app.command("list")
def list_vacations(
    employee: str | None = typer.Option(None, "--employee", "-e", help="Точное ФИО"),
    year: int | None = typer.Option(
        None, "--year", "-y", help="Год пересечения отпуска"
    ),
) -> None:
    """Показать отпуска всех сотрудников или конкретного сотрудника."""
    service = build_service()
    employee_id = None
    if employee:
        found = service.database.find_employee(employee)
        if found is None:
            raise typer.BadParameter(f"Сотрудник «{employee}» не найден")
        employee_id = found.id
    vacations = service.database.list_vacations(employee_id=employee_id, year=year)
    if not vacations:
        typer.echo("Отпусков по заданному фильтру нет.")
        return
    typer.echo("ID | Сотрудник | Начало | Окончание | Дней")
    typer.echo("-" * 90)
    for item in vacations:
        days_count = (item.end_date - item.start_date).days + 1
        typer.echo(
            f"{item.id} | {item.employee_name} | {item.start_date:%d.%m.%Y} | "
            f"{item.end_date:%d.%m.%Y} | {days_count}"
        )


@vacation_app.command("delete")
def delete_vacation(vacation_id: int = typer.Argument(..., help="ID отпуска")) -> None:
    """Удалить отпуск из SQLite."""
    service = build_service()
    try:
        service.database.delete_vacation(vacation_id)
    except LookupError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Отпуск #{vacation_id} удален")


@app.command("bot")
def run_telegram_bot() -> None:
    """Запустить Telegram-бота и планировщик напоминаний."""
    start_bot()


@vacation_app.command("export")
def export_vacations(
    output: Path = typer.Option(
        Path("exports/vacations.xlsx"), "--output", "-o", help="Путь к файлу XLSX"
    ),
    employee: str | None = typer.Option(None, "--employee", "-e", help="Точное ФИО"),
    year: int | None = typer.Option(
        None, "--year", "-y", help="Год пересечения отпуска"
    ),
) -> None:
    """Выгрузить календарь отпусков в XLSX."""
    service = build_service()
    employee_id = None
    if employee:
        found = service.database.find_employee(employee)
        if found is None:
            raise typer.BadParameter(f"Сотрудник «{employee}» не найден")
        employee_id = found.id
    items = service.database.list_vacations(employee_id=employee_id, year=year)
    result = export_vacations_xlsx(items, output)
    typer.echo(f"Выгружено записей: {len(items)}. Файл: {result}")


@database_app.command("backup")
def backup_database(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Путь к резервной копии"
    ),
) -> None:
    """Создать согласованную резервную копию SQLite."""
    service = build_service()
    destination = output or Path(
        f"backups/vacations-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    )
    result = service.database.backup(destination)
    typer.echo(f"Резервная копия создана: {result}")
