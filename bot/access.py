from __future__ import annotations

from bot.models import Employee

ROLES = {"guest", "employee", "owner"}
NOTIFICATION_GROUPS = {"guest", "employee", "team_lead", "owner"}
ROLE_LABELS = {"guest": "Гость", "employee": "Сотрудник", "owner": "Владелец"}


def can_manage(actor: Employee, target: Employee) -> bool:
    if actor.role == "owner":
        return True
    if actor.is_team_lead:
        return target.id == actor.id or target.team_lead_id == actor.id
    return actor.id == target.id


def can_assign_roles(actor: Employee) -> bool:
    return actor.role == "owner"


def visible_contacts(actor: Employee, employees: list[Employee]) -> list[Employee]:
    if actor.role != "guest":
        return [item for item in employees if item.profile_completed]
    return [item for item in employees if item.id == actor.team_lead_id]