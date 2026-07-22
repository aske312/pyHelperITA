from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.db import format_display_name
from bot.models import Employee, Team
from bot.service import VacationService


def _team_card(team: Team, members: list[Employee]) -> str:
    member_lines = [
        f"{index}. <b>{escape(format_display_name(member.full_name))}</b>"
        f" — {escape(member.grade or 'грейд не указан')}"
        for index, member in enumerate(members, 1)
    ]
    composition = "\n".join(member_lines) if member_lines else "Состав пока пуст."
    return (
        f"<b>Команда: {escape(team.name)}</b>\n"
        f"Тимлид: {escape(format_display_name(team.lead_name))}\n"
        f"Участников: {len(members)}\n\n"
        f"<b>Состав</b>\n{composition}"
    )


def create_team_router(service: VacationService) -> Router:
    router = Router(name="team")

    def actor(message: Message) -> Employee | None:
        if message.from_user is None:
            return None
        return service.database.get_employee_by_telegram(message.from_user.id)

    def can_edit(employee: Employee, team: Team) -> bool:
        return employee.role == "owner" or team.lead_id == employee.id

    @router.message(Command("team"))
    async def show_teams(message: Message) -> None:
        employee = actor(message)
        if employee is None or (employee.role != "owner" and not employee.is_team_lead):
            await message.answer("Команда доступна владельцу и тимлидам.")
            return
        teams = service.database.list_teams(
            None if employee.role == "owner" else employee.id
        )
        if not teams:
            await message.answer(
                "<b>Команды не созданы</b>\n\n"
                "Создание: <code>/team_create Название команды</code>",
                parse_mode="HTML",
            )
            return
        cards = [
            _team_card(team, service.database.list_team_members(team.id))
            for team in teams
        ]
        await message.answer("\n\n──────────\n\n".join(cards), parse_mode="HTML")

    @router.message(Command("team_create"))
    async def create_team(message: Message) -> None:
        employee = actor(message)
        if employee is None or (employee.role != "owner" and not employee.is_team_lead):
            await message.answer("Создавать команды могут владелец и тимлиды.")
            return
        arguments = (message.text or "").partition(" ")[2].strip()
        lead_id = employee.id
        name = arguments
        if employee.role == "owner" and arguments:
            first, separator, rest = arguments.partition(" ")
            if first.isdigit() and separator:
                lead_id, name = int(first), rest.strip()
        if not name:
            example = "/team_create ID_тимлида Название" if employee.role == "owner" else "/team_create Название"
            await message.answer(f"Формат: <code>{example}</code>", parse_mode="HTML")
            return
        try:
            team = service.database.create_team(name, lead_id)
        except (ValueError, LookupError) as error:
            await message.answer(f"Не удалось создать команду: {escape(str(error))}", parse_mode="HTML")
            return
        await message.answer(
            f"<b>Команда создана</b>\n\nНазвание: {escape(team.name)}\nID: <code>{team.id}</code>",
            parse_mode="HTML",
        )

    @router.message(Command("team_add"))
    async def add_member(message: Message) -> None:
        employee = actor(message)
        parts = (message.text or "").split()
        if employee is None or len(parts) != 3:
            await message.answer("Формат: <code>/team_add ID_команды ID_сотрудника</code>", parse_mode="HTML")
            return
        try:
            team = service.database.get_team(int(parts[1]))
            if not can_edit(employee, team):
                await message.answer("Недостаточно прав для этой команды.")
                return
            member = service.database.add_team_member(team.id, int(parts[2]))
        except (ValueError, LookupError) as error:
            await message.answer(f"Не удалось добавить участника: {escape(str(error))}", parse_mode="HTML")
            return
        await message.answer(
            f"<b>Участник добавлен</b>\n\n"
            f"Команда: {escape(team.name)}\n"
            f"Сотрудник: {escape(format_display_name(member.full_name))}",
            parse_mode="HTML",
        )

    @router.message(Command("team_remove"))
    async def remove_member(message: Message) -> None:
        employee = actor(message)
        parts = (message.text or "").split()
        if employee is None or len(parts) != 3:
            await message.answer("Формат: <code>/team_remove ID_команды ID_сотрудника</code>", parse_mode="HTML")
            return
        try:
            team = service.database.get_team(int(parts[1]))
            if not can_edit(employee, team):
                await message.answer("Недостаточно прав для этой команды.")
                return
            removed = service.database.remove_team_member(team.id, int(parts[2]))
        except (ValueError, LookupError) as error:
            await message.answer(f"Не удалось удалить участника: {escape(str(error))}", parse_mode="HTML")
            return
        text = "Участник удален из команды." if removed else "Участник не найден в этой команде."
        await message.answer(f"<b>{text}</b>", parse_mode="HTML")

    return router