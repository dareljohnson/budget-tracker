"""Reports: choose a report and period, preview the PDF, and export it."""

from __future__ import annotations

import base64
import logging
from datetime import date
from pathlib import Path

import flet as ft

from ...config import export_dir
from ...formatting import month_label
from ...report_pdf import page_count, page_png, render_report_pdf, write_report_pdf
from ...reports import ReportKind, ReportService
from ..components import card, page_header
from ..context import AppContext
from ..theme import GAP, INCOME_COLOR, SEED_COLOR, fs

log = logging.getLogger(__name__)

PREVIEW_HEIGHT = 760
REPORT_INFO: dict[ReportKind, dict] = {
    ReportKind.MONTHLY: {
        "title": "Monthly summary",
        "description": (
            "A compact record of the month: what came in, what went out, and how it "
            "compared with the budget you set."
        ),
        "points": [
            "The month at a glance and savings rate",
            "Six-month income and spending",
            "Every budget, with its status",
            "Largest expenses and account balances",
        ],
        "best": "Monthly check-ins, spotting a category that drifted, and sharing the month with a partner.",
    },
    ReportKind.YEAR: {
        "title": "Year in review",
        "description": (
            "The annual story: how the year tracked against its budget, where the money "
            "went, and whether it beat last year."
        ),
        "points": [
            "Year-to-date totals and savings rate",
            "Cumulative budget versus spending",
            "Month-by-month budget performance",
            "Category totals and a prior-year comparison",
        ],
        "best": "Annual planning, a tax-season review, and seeing whether a category is a one-month blip or a habit.",
    },
    ReportKind.NET_WORTH: {
        "title": "Net worth statement",
        "description": (
            "A balance sheet rather than a budget: what you own, what you owe, and how "
            "both changed over the year."
        ),
        "points": [
            "Net worth, with month and year-to-date change",
            "A twelve-month net worth trend",
            "Liquid and other assets, plus liabilities",
            "Debt payoff estimates and savings goal progress",
        ],
        "best": "Quarterly reviews, loan applications, and tracking long-term progress instead of a single month.",
    },
}


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class ReportsView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.reports = ReportService(ctx.service)
        self.kind = ReportKind.MONTHLY
        self._pdf_bytes: bytes | None = None
        self._page_count = 0
        self._page_index = 0
        self._page_cache: dict[int, str] = {}
        self._filename = "budget_tracker_report.pdf"
        self._file_picker = ft.FilePicker()

        self.kind_switch = ft.SegmentedButton(
            segments=[
                ft.Segment(value=kind.value, label=ft.Text(kind.label))
                for kind in ReportKind
            ],
            selected=[self.kind.value],
            allow_empty_selection=False,
            allow_multiple_selection=False,
            on_change=self._switch_kind,
        )
        self.month_dropdown = ft.Dropdown(
            label="Month",
            width=190,
            dense=True,
            options=[],
            on_select=lambda _: self._render(),
        )
        self.year_dropdown = ft.Dropdown(
            label="Year",
            width=130,
            dense=True,
            options=[],
            on_select=lambda _: self._render(),
        )
        self.period_slot = ft.Container(content=self.month_dropdown)

        self.export_button = ft.FilledButton(
            content="Export PDF",
            icon=ft.Icons.PICTURE_AS_PDF,
            tooltip="Save the previewed report as a print-ready PDF",
            on_click=self._export,
        )
        self.excel_button = ft.OutlinedButton(
            content="Excel workbook",
            icon=ft.Icons.TABLE_CHART_OUTLINED,
            tooltip="Full workbook with every transaction, budget, account, and bill",
            on_click=self._export_excel,
        )

        self.preview_error = ft.Text(
            "The report preview is not available.",
            size=fs(13),
            color=ft.Colors.ON_SURFACE_VARIANT,
            text_align=ft.TextAlign.CENTER,
        )
        self.preview_image = ft.Container(
            height=PREVIEW_HEIGHT,
            bgcolor=ft.Colors.WHITE,
            border_radius=10,
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=self.preview_error,
        )
        self.preview_meta = ft.Text("", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT)
        self.prev_page = ft.IconButton(
            ft.Icons.CHEVRON_LEFT,
            tooltip="Previous page",
            disabled=True,
            on_click=lambda _: self._show_page(self._page_index - 1),
        )
        self.next_page = ft.IconButton(
            ft.Icons.CHEVRON_RIGHT,
            tooltip="Next page",
            disabled=True,
            on_click=lambda _: self._show_page(self._page_index + 1),
        )
        self.page_label = ft.Text("", size=fs(12), color=ft.Colors.ON_SURFACE_VARIANT)

        self.info_title = ft.Text("", size=fs(21), weight=ft.FontWeight.W_600)
        self.info_description = ft.Text(
            "",
            size=fs(13),
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.info_points = ft.Column(spacing=7)
        self.info_best = ft.Text("", size=fs(13), color=ft.Colors.ON_SURFACE_VARIANT)

        self.control = ft.Column(
            [
                page_header(
                    "Reports",
                    "Turn your ledger into a monthly summary, a year in review, or a net worth statement",
                    [self.excel_button, self.export_button],
                ),
                card(
                    ft.ResponsiveRow(
                        [
                            ft.Container(self.kind_switch, col={"xs": 12, "lg": 7}),
                            ft.Container(
                                ft.Row(
                                    [self.period_slot],
                                    alignment=ft.MainAxisAlignment.END,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                col={"xs": 12, "lg": 5},
                            ),
                        ],
                        spacing=12,
                        run_spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=14,
                ),
                ft.ResponsiveRow(
                    [
                        card(
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Column(
                                                [
                                                    ft.Text(
                                                        "Preview",
                                                        size=fs(16),
                                                        weight=ft.FontWeight.W_600,
                                                    ),
                                                    self.preview_meta,
                                                ],
                                                spacing=0,
                                            ),
                                            ft.Row(
                                                [self.prev_page, self.page_label, self.next_page],
                                                alignment=ft.MainAxisAlignment.END,
                                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                spacing=2,
                                            ),
                                        ],
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                        wrap=True,
                                    ),
                                    self.preview_image,
                                ],
                                spacing=12,
                            ),
                            col={"xs": 12, "lg": 8},
                        ),
                        card(self._info_panel(), col={"xs": 12, "lg": 4}),
                    ],
                    spacing=GAP,
                    run_spacing=GAP,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            spacing=GAP,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        ctx.on_data_changed(self.refresh)
        self.refresh()

    # ---------- layout ----------

    def _info_panel(self) -> ft.Column:
        return ft.Column(
            [
                ft.Text(
                    "REPORT",
                    size=fs(10),
                    weight=ft.FontWeight.BOLD,
                    color=SEED_COLOR,
                ),
                self.info_title,
                self.info_description,
                ft.Divider(height=1),
                ft.Text("What's inside", size=fs(11), weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE_VARIANT),
                self.info_points,
                ft.Divider(height=1),
                ft.Text("Best for", size=fs(11), weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE_VARIANT),
                self.info_best,
                ft.Divider(height=1),
                ft.Text("Format", size=fs(11), weight=ft.FontWeight.BOLD, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(
                    "PDF, generated locally and ready to print or share. "
                    "The Excel workbook remains available for analysis.",
                    size=fs(13),
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=10,
        )

    # ---------- state ----------

    def _period_control(self) -> ft.Control:
        if self.kind is ReportKind.MONTHLY:
            return self.month_dropdown
        if self.kind is ReportKind.YEAR:
            return self.year_dropdown
        today = date.today()
        return ft.Row(
            [
                ft.Icon(ft.Icons.TODAY, size=18, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(
                    f"As of {today:%B} {today.day}, {today.year}",
                    size=fs(14),
                    weight=ft.FontWeight.W_500,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _update_info(self) -> None:
        info = REPORT_INFO[self.kind]
        self.info_title.value = info["title"]
        self.info_description.value = info["description"]
        self.info_best.value = info["best"]
        self.info_points.controls = [
            ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK, size=16, color=INCOME_COLOR),
                    ft.Text(point, size=fs(13), expand=True),
                ],
                spacing=7,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
            for point in info["points"]
        ]

    def _refresh_options(self) -> None:
        months = self.reports.available_months()
        self.month_dropdown.options = [
            ft.DropdownOption(key=month, text=month_label(month)) for month in months
        ]
        current_month = self.ctx.service.current_month()
        if self.month_dropdown.value not in months:
            self.month_dropdown.value = (
                current_month if current_month in months else months[0] if months else current_month
            )

        years = sorted(self.reports.available_years(), reverse=True)
        self.year_dropdown.options = [
            ft.DropdownOption(key=str(year), text=str(year)) for year in years
        ]
        current_year = date.today().year
        values = [str(year) for year in years]
        if self.year_dropdown.value not in values:
            self.year_dropdown.value = str(current_year if current_year in years else years[0] if years else current_year)

    def _switch_kind(self, event: ft.Event[ft.SegmentedButton]) -> None:
        selected = event.control.selected
        if isinstance(selected, str):
            value = selected
        else:
            try:
                value = next(iter(selected or ()), self.kind.value)
            except TypeError:
                value = self.kind.value
        self.kind = ReportKind(value) if value in {kind.value for kind in ReportKind} else ReportKind.MONTHLY
        self.period_slot.content = self._period_control()
        self._update_info()
        self._render()

    def refresh(self) -> None:
        self._refresh_options()
        self._update_info()
        self._render()

    # ---------- preview ----------

    def _page_uri(self, index: int) -> str:
        if index not in self._page_cache:
            assert self._pdf_bytes is not None
            self._page_cache[index] = _data_uri(page_png(self._pdf_bytes, index, scale=1.6))
        return self._page_cache[index]

    def _show_page(self, index: int, *, update: bool = True) -> None:
        if not self._pdf_bytes or not self._page_count:
            return
        self._page_index = max(0, min(index, self._page_count - 1))
        self.preview_image.image = ft.DecorationImage(
            src=self._page_uri(self._page_index),
            fit=ft.BoxFit.CONTAIN,
        )
        self.preview_image.content = None
        self.page_label.value = f"Page {self._page_index + 1} of {self._page_count}"
        self.prev_page.disabled = self._page_index == 0
        self.next_page.disabled = self._page_index >= self._page_count - 1
        if update:
            self.ctx.page.update()

    def _render(self) -> None:
        try:
            report = self.reports.build(
                self.kind,
                month=self.month_dropdown.value,
                year=int(self.year_dropdown.value) if self.year_dropdown.value else None,
            )
            pdf = render_report_pdf(report)
            count = page_count(pdf)
            if count < 1:
                raise ValueError("The report has no pages")
            self._pdf_bytes = pdf
            self._page_count = count
            self._page_index = 0
            self._page_cache.clear()
            self._filename = report.filename
            self._show_page(0, update=False)
            self.preview_meta.value = report.period_label
            self.export_button.disabled = False
        except Exception as exc:  # noqa: BLE001 - a report failure must not break the page
            log.exception("Could not build the report preview")
            self._pdf_bytes = None
            self._page_count = 0
            self._page_cache.clear()
            self.preview_image.image = None
            self.preview_error.value = f"The report could not be generated: {exc}"
            self.preview_image.content = self.preview_error
            self.preview_meta.value = "Preview unavailable"
            self.page_label.value = ""
            self.prev_page.disabled = True
            self.next_page.disabled = True
            self.export_button.disabled = True
        self.ctx.page.update()

    # ---------- exports ----------

    async def _choose_path(self, title: str, filename: str, extension: str) -> Path | None:
        try:
            chosen = await self._file_picker.save_file(
                dialog_title=title,
                file_name=filename,
                initial_directory=str(export_dir()),
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=[extension],
            )
        except Exception:  # noqa: BLE001 - fall back to the default folder if the dialog is unavailable
            chosen = str(export_dir() / filename)
        if not chosen:
            return None
        path = Path(chosen)
        return path if path.suffix.lower() == f".{extension}" else path.with_name(f"{path.name}.{extension}")

    async def _export(self, _: ft.Event) -> None:
        if self._pdf_bytes is None:
            self.ctx.notify("The report is not ready yet", error=True)
            return
        chosen = await self._choose_path("Export report", self._filename, "pdf")
        if chosen is None:
            return
        try:
            write_report_pdf(chosen, self._pdf_bytes)
        except OSError as exc:
            self.ctx.notify(
                f"Export failed (is the file open in another program?): {exc.strerror or exc}",
                error=True,
            )
            return
        self.ctx.notify(f"Exported report to {chosen}")

    async def _export_excel(self, _: ft.Event) -> None:
        chosen = await self._choose_path(
            "Export Excel workbook",
            self.ctx.service.export_xlsx_filename(),
            "xlsx",
        )
        if chosen is None:
            return
        try:
            path = self.ctx.service.export_xlsx(chosen)
        except OSError as exc:
            self.ctx.notify(
                f"Export failed (is the file open in another program?): {exc.strerror or exc}",
                error=True,
            )
            return
        self.ctx.notify(f"Exported workbook to {path}")
