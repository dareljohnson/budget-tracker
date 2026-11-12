"""A small, dependency-free .xlsx (Office Open XML) writer.

Supports what the export needs: typed cells, formulas with cached results, a fixed set of
number formats/styles, column widths, frozen header rows and auto-filters. Text is always
written as a string cell, so user data can never be interpreted as a formula.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import IntEnum
from pathlib import Path
from xml.sax.saxutils import escape

_EPOCH = date(1899, 12, 30)
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEET_NAME_INVALID = re.compile(r"[\[\]:*?/\\]")


class Style(IntEnum):
    """Indexes into ``cellXfs`` in the generated styles.xml (order must match ``_CELL_XFS``)."""

    DEFAULT = 0
    HEADER = 1
    CURRENCY = 2
    DATE = 3
    MONTH = 4
    PERCENT = 5
    INTEGER = 6
    BOLD = 7
    BOLD_CURRENCY = 8
    TITLE = 9
    BOLD_PERCENT = 10
    MUTED = 11


@dataclass(slots=True, frozen=True)
class Cell:
    value: str | int | float | bool | date | None = None
    style: Style | None = None
    formula: str | None = None  # without the leading "="; ``value`` is the cached result


@dataclass(slots=True)
class Sheet:
    name: str
    rows: list[list[Cell | str | int | float | date | None]] = field(default_factory=list)
    widths: list[float] = field(default_factory=list)
    freeze_rows: int = 0
    filter_header_row: int | None = None  # 1-based row of a header to attach an auto-filter to
    tab_color: str | None = None

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 31 or _SHEET_NAME_INVALID.search(self.name):
            raise ValueError(f"Invalid sheet name: {self.name!r}")

    def append(self, row: list[Cell | str | int | float | date | None]) -> int:
        """Add a row and return its 1-based row number."""
        self.rows.append(row)
        return len(self.rows)


def column_letter(index: int) -> str:
    """1-based column index to letters: 1 -> A, 27 -> AA."""
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _text(value: str) -> str:
    return escape(_INVALID_XML.sub("", value))


def _serial(value: date) -> int | float:
    if isinstance(value, datetime):
        delta = value - datetime.combine(_EPOCH, datetime.min.time(), tzinfo=value.tzinfo)
        return delta.days + delta.seconds / 86400
    return (value - _EPOCH).days


def _default_style(value: object) -> Style:
    if isinstance(value, date):
        return Style.DATE
    return Style.DEFAULT


def _cell_xml(ref: str, cell: Cell | str | int | float | date | None) -> str:
    if not isinstance(cell, Cell):
        cell = Cell(cell)
    value = cell.value
    style = cell.style if cell.style is not None else _default_style(value)
    s = f' s="{int(style)}"' if style else ""
    if cell.formula is not None:
        formula = f"<f>{_text(cell.formula)}</f>"
        if value is None:
            return f'<c r="{ref}"{s}>{formula}</c>'
        if isinstance(value, bool):
            return f'<c r="{ref}"{s} t="b">{formula}<v>{int(value)}</v></c>'
        if isinstance(value, str):
            return f'<c r="{ref}"{s} t="str">{formula}<v>{_text(value)}</v></c>'
        number = _serial(value) if isinstance(value, date) else value
        return f'<c r="{ref}"{s}>{formula}<v>{number}</v></c>'
    if value is None:
        return f'<c r="{ref}"{s}/>' if s else ""
    if isinstance(value, bool):
        return f'<c r="{ref}"{s} t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    if isinstance(value, date):
        return f'<c r="{ref}"{s}><v>{_serial(value)}</v></c>'
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{_text(str(value))}</t></is></c>'


def _sheet_xml(sheet: Sheet) -> str:
    max_cols = max((len(r) for r in sheet.rows), default=1)
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
    ]
    if sheet.tab_color:
        parts.append(f'<sheetPr><tabColor rgb="FF{sheet.tab_color.lstrip("#").upper()}"/></sheetPr>')
    parts.append(f'<dimension ref="A1:{column_letter(max_cols)}{max(len(sheet.rows), 1)}"/>')
    if sheet.freeze_rows:
        top = f"A{sheet.freeze_rows + 1}"
        parts.append(
            '<sheetViews><sheetView workbookViewId="0">'
            f'<pane ySplit="{sheet.freeze_rows}" topLeftCell="{top}" activePane="bottomLeft" state="frozen"/>'
            f'<selection pane="bottomLeft" activeCell="{top}" sqref="{top}"/></sheetView></sheetViews>'
        )
    else:
        parts.append('<sheetViews><sheetView workbookViewId="0"/></sheetViews>')
    parts.append('<sheetFormatPr defaultRowHeight="15"/>')
    if sheet.widths:
        cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(sheet.widths, 1))
        parts.append(f"<cols>{cols}</cols>")
    parts.append("<sheetData>")
    for r, row in enumerate(sheet.rows, 1):
        cells = "".join(_cell_xml(f"{column_letter(c)}{r}", cell) for c, cell in enumerate(row, 1))
        height = ' ht="22" customHeight="1"' if any(isinstance(x, Cell) and x.style == Style.HEADER for x in row) else ""
        parts.append(f'<row r="{r}"{height}>{cells}</row>')
    parts.append("</sheetData>")
    if sheet.filter_header_row:
        header = sheet.rows[sheet.filter_header_row - 1]
        last = max(len(sheet.rows), sheet.filter_header_row)
        parts.append(f'<autoFilter ref="A{sheet.filter_header_row}:{column_letter(len(header))}{last}"/>')
    parts.append('<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>')
    parts.append('<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>')
    parts.append("</worksheet>")
    return "".join(parts)


_CURRENCY_FMT = '"$"#,##0.00;[Red]-"$"#,##0.00'
_NUM_FMTS = {164: _CURRENCY_FMT, 165: "yyyy-mm-dd", 166: "mmm yyyy", 167: "0.0%"}
# (numFmtId, fontId, fillId, applyAlignment-center)
_CELL_XFS = [
    (0, 0, 0, False),  # DEFAULT
    (0, 1, 2, True),  # HEADER
    (164, 0, 0, False),  # CURRENCY
    (165, 0, 0, False),  # DATE
    (166, 0, 0, False),  # MONTH
    (167, 0, 0, False),  # PERCENT
    (1, 0, 0, False),  # INTEGER
    (0, 2, 0, False),  # BOLD
    (164, 2, 0, False),  # BOLD_CURRENCY
    (0, 3, 0, False),  # TITLE
    (167, 2, 0, False),  # BOLD_PERCENT
    (0, 4, 0, False),  # MUTED
]


def _styles_xml(header_color: str) -> str:
    fmts = "".join(f'<numFmt numFmtId="{i}" formatCode="{escape(f, {chr(34): "&quot;"})}"/>' for i, f in _NUM_FMTS.items())
    def xf(num_fmt: int, font: int, fill: int, center: bool) -> str:
        attrs = f'numFmtId="{num_fmt}" fontId="{font}" fillId="{fill}" borderId="0" xfId="0"'
        attrs += ' applyNumberFormat="1"' if num_fmt else ""
        attrs += ' applyFont="1"' if font else ""
        attrs += ' applyFill="1"' if fill else ""
        if center:
            return f'<xf {attrs} applyAlignment="1"><alignment vertical="center"/></xf>'
        return f"<xf {attrs}/>"

    xfs = "".join(xf(*spec) for spec in _CELL_XFS)
    color = header_color.lstrip("#").upper()
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<numFmts count="{len(_NUM_FMTS)}">{fmts}</numFmts>'
        '<fonts count="5">'
        '<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="16"/><name val="Calibri"/><family val="2"/></font>'
        '<font><i/><sz val="10"/><color rgb="FF666666"/><name val="Calibri"/><family val="2"/></font>'
        "</fonts>"
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{color}"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(_CELL_XFS)}">{xfs}</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def write_workbook(path: Path, sheets: list[Sheet], *, title: str = "", app_name: str = "", header_color: str = "#3949AB") -> Path:
    """Write ``sheets`` to ``path`` as an .xlsx file. Formulas are recalculated when the file is opened."""
    if not sheets:
        raise ValueError("A workbook needs at least one sheet")
    names = [s.name.lower() for s in sheets]
    if len(set(names)) != len(names):
        raise ValueError("Sheet names must be unique")
    n = len(sheets)
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, n + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{sheet_overrides}"
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )
    defined_names = "".join(
        f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
        f"'{_text(s.name)}'!$A${s.filter_header_row}:${column_letter(len(s.rows[s.filter_header_row - 1]))}"
        f"${max(len(s.rows), s.filter_header_row)}</definedName>"
        for i, s in enumerate(sheets)
        if s.filter_header_row
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<bookViews><workbookView activeTab="0"/></bookViews><sheets>'
        + "".join(f'<sheet name="{_text(s.name)}" sheetId="{i}" r:id="rId{i}"/>' for i, s in enumerate(sheets, 1))
        + "</sheets>"
        + (f"<definedNames>{defined_names}</definedNames>" if defined_names else "")
        + '<calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, n + 1)
        )
        + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_text(title)}</dc:title><dc:creator>{_text(app_name)}</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        "</cp:coreProperties>"
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        f"<Application>{_text(app_name)}</Application></Properties>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", _styles_xml(header_color))
        for i, sheet in enumerate(sheets, 1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheet))
    tmp.replace(path)
    return path
