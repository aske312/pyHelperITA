from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def load_holidays(path: Path) -> dict[date, str]:
    """Load explicitly configured Russian non-working holidays."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    holidays: dict[date, str] = {}
    for raw_date, name in raw.get("holidays", {}).items():
        holidays[date.fromisoformat(raw_date)] = str(name)
    return holidays


def holidays_between(
    holidays: dict[date, str], start: date, end: date
) -> list[tuple[date, str]]:
    return sorted(
        (day, name) for day, name in holidays.items() if start <= day <= end
    )
