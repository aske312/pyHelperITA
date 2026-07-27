from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from core.db import Database
from core.integrations.secrets import SecretStore
from core.integrations.service import IntegrationService

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"


@dataclass(frozen=True, slots=True)
class ImportedEvent:
    uid: str
    recurrence_id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    source_url: str


def _request(url: str, method: str, body: str, username: str, password: str) -> bytes:
    if urlparse(url).scheme != "https":
        raise ValueError("Разрешены только HTTPS CalDAV URL")
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    request = Request(
        url,
        data=body.encode("utf-8"),
        method=method,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/xml; charset=utf-8",
            "Depth": "1",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read()


def _calendar_url(root_url: str, username: str, password: str) -> str:
    principal_body = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>"""
    principal_xml = _request(root_url, "PROPFIND", principal_body, username, password)
    principal_element = ElementTree.fromstring(principal_xml).find(
        f".//{{{DAV}}}current-user-principal/{{{DAV}}}href"
    )
    if principal_element is None or not principal_element.text:
        raise ValueError("CalDAV principal не найден")
    principal = principal_element.text
    home_body = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>"""
    home_xml = _request(
        urljoin(root_url, principal), "PROPFIND", home_body, username, password
    )
    home_element = ElementTree.fromstring(home_xml).find(
        f".//{{{CALDAV}}}calendar-home-set/{{{DAV}}}href"
    )
    if home_element is None or not home_element.text:
        raise ValueError("CalDAV calendar-home-set не найден")
    home = home_element.text
    calendars_body = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>"""
    calendars_xml = _request(
        urljoin(root_url, home), "PROPFIND", calendars_body, username, password
    )
    document = ElementTree.fromstring(calendars_xml)
    for response in document.findall(f".//{{{DAV}}}response"):
        if response.find(f".//{{{CALDAV}}}calendar") is not None:
            href = response.find(f"{{{DAV}}}href")
            if href is not None and href.text:
                return urljoin(root_url, href.text)
    raise ValueError("CalDAV-календарь не найден")


def _unfold(value: str) -> list[str]:
    lines: list[str] = []
    for line in value.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _date(value: str, timezone_name: str = "UTC") -> tuple[datetime, bool]:
    if len(value) == 8:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc), True
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        ), False
    return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(
        tzinfo=ZoneInfo(timezone_name)
    ), False


def _parse_ics(value: str, source_url: str) -> list[ImportedEvent]:
    result: list[ImportedEvent] = []
    current: dict[str, str] | None = None
    for line in _unfold(value):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            if "UID" in current and "DTSTART" in current:
                start, all_day = _date(
                    current["DTSTART"], current.get("DTSTART_TZID", "UTC")
                )
                end = (
                    _date(current["DTEND"], current.get("DTEND_TZID", "UTC"))[0]
                    if current.get("DTEND")
                    else None
                )
                result.append(
                    ImportedEvent(
                        current["UID"],
                        current.get("RECURRENCE-ID", ""),
                        current.get("SUMMARY", "Без названия").replace("\\,", ","),
                        start,
                        end,
                        all_day,
                        source_url,
                    )
                )
            current = None
        elif current is not None and ":" in line:
            name, raw = line.split(":", 1)
            parts = name.split(";")
            current[parts[0]] = raw
            for parameter in parts[1:]:
                if parameter.startswith("TZID="):
                    current[f"{parts[0]}_TZID"] = parameter.split("=", 1)[1]
    return result


def fetch_events(
    root_url: str, username: str, password: str, start: datetime, end: datetime
) -> list[ImportedEvent]:
    calendar_url = _calendar_url(root_url, username, password)
    body = f"""<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}" end="{end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"""
    xml = _request(calendar_url, "REPORT", body, username, password)
    document = ElementTree.fromstring(xml)
    events: list[ImportedEvent] = []
    for response in document.findall(f".//{{{DAV}}}response"):
        href = response.find(f"{{{DAV}}}href")
        data = response.find(f".//{{{CALDAV}}}calendar-data")
        if data is not None and data.text:
            events.extend(_parse_ics(data.text, urljoin(root_url, href.text or "")))
    return events


class CalendarSyncService:
    def __init__(self, database: Database, secret_store: SecretStore):
        self.database = database
        self.integrations = IntegrationService(database, secret_store)

    async def sync_all(self) -> int:
        now = datetime.now(timezone.utc)
        synced = 0
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT employee_id FROM employee_integrations WHERE calendar_provider = 'caldav' AND calendar_account IS NOT NULL"
            ).fetchall()
        for row in rows:
            employee_id = int(row["employee_id"])
            item = self.integrations.get(employee_id)
            password = self.integrations.get_calendar_password(employee_id)
            if not item.calendar_username or not password:
                continue
            try:
                events = await asyncio.to_thread(
                    fetch_events,
                    item.calendar_account,
                    item.calendar_username,
                    password,
                    now - timedelta(days=30),
                    now + timedelta(days=365),
                )
                with self.database.connect() as connection:
                    connection.execute(
                        "DELETE FROM calendar_events WHERE employee_id = ?",
                        (employee_id,),
                    )
                    connection.executemany(
                        "INSERT INTO calendar_events(employee_id, external_uid, recurrence_id, title, starts_at, ends_at, all_day, source_url, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            (
                                employee_id,
                                event.uid,
                                event.recurrence_id,
                                event.title,
                                event.starts_at.isoformat(),
                                event.ends_at.isoformat() if event.ends_at else None,
                                int(event.all_day),
                                event.source_url,
                                now.isoformat(),
                            )
                            for event in events
                        ],
                    )
                    connection.execute(
                        "UPDATE employee_integrations SET calendar_status = 'connected', updated_at = ? WHERE employee_id = ?",
                        (now.isoformat(), employee_id),
                    )
                synced += len(events)
            except Exception:
                with self.database.connect() as connection:
                    connection.execute(
                        "UPDATE employee_integrations SET calendar_status = 'error', updated_at = ? WHERE employee_id = ?",
                        (now.isoformat(), employee_id),
                    )
        return synced
