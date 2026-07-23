from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from core.models import VacationView


def _cell(reference: str, value: object) -> str:
    text = escape(str(value))
    return f"<c r='{reference}' t='inlineStr'><is><t>{text}</t></is></c>"


def export_vacations_xlsx(
    items: Sequence[VacationView], destination: Path | str
) -> Path:
    """Create a compact Excel-compatible workbook without a heavyweight dependency."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("ID", "Сотрудник", "Дата начала", "Дата окончания", "Календарных дней"),
        *[
            (
                item.id,
                item.employee_name,
                item.start_date.strftime("%d.%m.%Y"),
                item.end_date.strftime("%d.%m.%Y"),
                (item.end_date - item.start_date).days + 1,
            )
            for item in items
        ],
    ]
    sheet_rows = []
    for row_number, values in enumerate(rows, start=1):
        cells = "".join(
            _cell(f"{chr(64 + column)}{row_number}", value)
            for column, value in enumerate(values, start=1)
        )
        sheet_rows.append(f"<row r='{row_number}'>{cells}</row>")
    sheet_data = "".join(sheet_rows)
    worksheet = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
        "<cols><col min='1' max='1' width='8' customWidth='1'/>"
        "<col min='2' max='2' width='38' customWidth='1'/>"
        "<col min='3' max='5' width='20' customWidth='1'/></cols>"
        f"<sheetData>{sheet_data}</sheetData><autoFilter ref='A1:E{len(rows)}'/>"
        "</worksheet>"
    )
    files = {
        "[Content_Types].xml": (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='rels' "
            "ContentType='application/vnd.openxmlformats-package.relationships+xml'/>"
            "<Default Extension='xml' ContentType='application/xml'/>"
            "<Override PartName='/xl/workbook.xml' "
            "ContentType='application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet.main+xml'/>"
            "<Override PartName='/xl/worksheets/sheet1.xml' "
            "ContentType='application/vnd.openxmlformats-officedocument."
            "spreadsheetml.worksheet+xml'/></Types>"
        ),
        "_rels/.rels": (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/"
            "officeDocument/2006/relationships/officeDocument' "
            "Target='xl/workbook.xml'/></Relationships>"
        ),
        "xl/workbook.xml": (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' "
            "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
            "<sheets><sheet name='Отпуска' sheetId='1' r:id='rId1'/></sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/"
            "officeDocument/2006/relationships/worksheet' "
            "Target='worksheets/sheet1.xml'/></Relationships>"
        ),
        "xl/worksheets/sheet1.xml": worksheet,
    }
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return target
