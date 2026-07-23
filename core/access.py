from __future__ import annotations

import json
from pathlib import Path

from core.models import Employee

ROLES = {"guest", "employee", "owner"}
NOTIFICATION_GROUPS = {"guest", "employee", "team_lead", "owner"}
ROLE_LABELS = {"guest": "Гость", "employee": "Сотрудник", "owner": "Владелец"}


class PermissionPolicy:
    """RBAC-политика из JSON; не зависит от конкретного интерфейса."""

    def __init__(self, path: Path | str = "config/permissions.json"):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.roles: dict[str, dict] = raw["roles"]
        unknown = {
            inherited
            for role in self.roles.values()
            for inherited in role.get("inherits", [])
            if inherited not in self.roles
        }
        if unknown:
            raise ValueError(f"Неизвестные наследуемые роли: {sorted(unknown)}")

    def permissions_for(self, role: str, *, is_team_lead: bool = False) -> set[str]:
        selected = "team_lead" if is_team_lead and role != "owner" else role
        visiting: set[str] = set()

        def collect(name: str) -> set[str]:
            if name in visiting:
                raise ValueError(f"Циклическое наследование роли {name}")
            if name not in self.roles:
                return set()
            visiting.add(name)
            result = set(self.roles[name].get("permissions", []))
            for parent in self.roles[name].get("inherits", []):
                result.update(collect(parent))
            visiting.remove(name)
            return result

        return collect(selected)

    def allows(self, actor: Employee, permission: str) -> bool:
        granted = self.permissions_for(actor.role, is_team_lead=actor.is_team_lead)
        return "*" in granted or permission in granted


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
