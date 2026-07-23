from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Feature:
    setting: str
    dependencies: tuple[str, ...] = ()


FEATURES: dict[str, Feature] = {
    "ONBOARDING": Feature("feature_onboarding", ("PROFILES",)),
    "PROFILES": Feature("feature_profiles"),
    "VACATIONS": Feature("feature_vacations", ("PROFILES",)),
    "OWNER_TOOLS": Feature("feature_owner", ("PROFILES",)),
    "EXPORTS": Feature("feature_exports", ("VACATIONS",)),
    "REMINDERS": Feature("feature_reminders", ("VACATIONS",)),
    "NOTIFICATIONS": Feature("feature_notifications", ("PROFILES",)),
    "EVENTS": Feature("feature_events", ("PROFILES",)),
    "TEAMS": Feature("feature_teams", ("PROFILES",)),
    "ABSENCES": Feature("feature_absences", ("PROFILES",)),
    "INTEGRATIONS": Feature("feature_integrations", ("PROFILES",)),
    "MAIL_INTEGRATIONS": Feature("feature_mail_integrations", ("INTEGRATIONS",)),
    "CALENDAR_INTEGRATIONS": Feature(
        "feature_calendar_integrations", ("INTEGRATIONS",)
    ),
}

COMMANDS = (
    "START", "CLEAR", "HELP", "VACATION", "ABSENCE", "SICK_LEAVE", "DAY_OFF",
    "MY_EVENTS", "PROFILE", "CONTACTS", "EVENTS", "EMPLOYEES", "INVITE_TEAM",
    "DISMISS_TEAM", "STAFF", "TEAMS", "TEAM_CREATE", "DELETE_TEAM", "GUEST",
    "NOTIFICATIONS", "EXPORT", "INTEGRATIONS",
)
AUTOMATIONS = (
    "AUTO_DAILY_EVENTS", "AUTO_BIRTHDAY_NOTIFICATIONS",
    "AUTO_PROBATION_NOTIFICATIONS", "AUTO_VACATION_NOTIFICATIONS",
    "DEFAULT_GUEST_ACCESS", "DEFAULT_SEND_ROLE_GUIDE", "PROFILE_RELATIONS",
)


def flag_mapping() -> dict[str, str]:
    result = {name: feature.setting for name, feature in FEATURES.items()}
    result.update({f"CMD_{name}": f"command_{name.lower()}" for name in COMMANDS})
    result.update({name: name.lower() for name in AUTOMATIONS})
    return result


def validate_dependencies(enabled: dict[str, bool]) -> None:
    errors = []
    for name, feature in FEATURES.items():
        if enabled.get(name):
            missing = [item for item in feature.dependencies if not enabled.get(item)]
            if missing:
                errors.append(f"{name} требует: {', '.join(missing)}")
    if errors:
        raise ValueError("Нарушены зависимости фичтоглов: " + "; ".join(errors))
