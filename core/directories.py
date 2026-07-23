from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Directories:
    offices: tuple[str, ...]
    english_levels: dict[str, str]
    grades: tuple[str, ...]
    directions: tuple[str, ...]
    work_formats: dict[str, str]
    projects: tuple[str, ...]
    stop_words: tuple[str, ...]

    @classmethod
    def load(cls, path: Path | str) -> "Directories":
        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8"))
        return cls(
            offices=tuple(data["offices"]),
            english_levels=dict(data["english_levels"]),
            grades=tuple(data["grades"]),
            directions=tuple(data["directions"]),
            work_formats=dict(data["work_formats"]),
            projects=tuple(data["projects"]),
            stop_words=tuple(word.casefold() for word in data.get("stop_words", ())),
        )

    def label_english(self, value: str | None) -> str | None:
        if value is None:
            return None
        description = self.english_levels.get(value)
        return f"{value} ({description})" if description else value

    def ensure_allowed_text(
        self, value: str, *, minimum: int = 1, maximum: int = 200
    ) -> str:
        normalized = " ".join(value.split())
        if not minimum <= len(normalized) <= maximum:
            raise ValueError("Некорректная длина значения")
        lowered = normalized.casefold()
        if any(word and word in lowered for word in self.stop_words):
            raise ValueError("Значение содержит запрещённые слова")
        return normalized


def validate_employee_id(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,5}", value.strip()):
        raise ValueError("ID должен содержать от 1 до 6 цифр")
    return int(value)


def validate_email(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) > 254 or normalized.count("@") != 1:
        raise ValueError("Некорректный email")
    local, domain = normalized.rsplit("@", 1)
    if not local or len(local) > 64 or local.startswith(".") or local.endswith("."):
        raise ValueError("Некорректный email")
    if ".." in local or not re.fullmatch(
        r"[\w.!#$%&'*+/=?^`{|}~-]+", local, re.UNICODE
    ):
        raise ValueError("Некорректный email")
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("Некорректный домен email") from error
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        raise ValueError("Некорректный домен email")
    return normalized


def validate_phone(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"\+?[0-9][0-9 ()-]*", normalized):
        raise ValueError("Некорректный международный номер телефона")
    digits = re.sub(r"\D", "", normalized)
    if not 7 <= len(digits) <= 15:
        raise ValueError("Телефон должен содержать от 7 до 15 цифр")
    return f"+{digits}" if normalized.startswith("+") else digits


def validate_person_name(value: str) -> str:
    normalized = " ".join(value.split())
    parts = normalized.split()
    if not 2 <= len(parts) <= 6:
        raise ValueError("Укажите имя и фамилию")
    for part in parts:
        pieces = re.split(r"[-'’]", part)
        if any(
            len(piece) < 1
            or not all(unicodedata.category(char).startswith("L") for char in piece)
            for piece in pieces
        ):
            raise ValueError("Имя может содержать буквы, дефисы и апострофы")
    return " ".join(
        "".join(
            piece if piece in {"-", "'", "’"} else piece.capitalize()
            for piece in re.split(r"([-’'])", part)
        )
        for part in parts
    )


def validate_city(value: str, directories: Directories) -> str:
    normalized = directories.ensure_allowed_text(value, minimum=2, maximum=100)
    if not all(
        unicodedata.category(char).startswith("L") or char in " -'’."
        for char in normalized
    ):
        raise ValueError("Название города содержит недопустимые символы")
    return normalized
