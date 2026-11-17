"""Print-ready charts for the PDF reports, rendered locally with Pillow.

The report PDF is generated without a browser or a network. These renderers draw on a white
page at roughly 2x the printed size; the PDF places each PNG at the report's content width.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Sequence
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .formatting import short_month_label
from .models import MonthTotal

WHITE = "#FFFFFF"
INK = "#1B1B1F"
MUTED = "#6B7180"
GRID = "#E7E9F0"
AXIS = "#C3C6D2"
INCOME = "#2A78D6"
SPENDING = "#EB6834"
SPENDING_WASH = "#FDF0EA"
NET_WORTH_WASH = "#EAF2FC"


def _font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=max(8, int(size)))


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text or " ", font=font)
    return box[2] - box[0], box[3] - box[1]


def _compact(cents: float) -> str:
    """A short axis label: $0, $62.5, $1.25k, $70k, -$5k."""
    dollars = float(cents) / 100.0
    sign = "-" if dollars < 0 else ""
    magnitude = abs(dollars)
    if magnitude >= 1000:
        value = f"{magnitude / 1000.0:.1f}".rstrip("0").rstrip(".")
        return f"{sign}${value}k"
    if abs(magnitude - round(magnitude)) < 1e-9:
        return f"{sign}${int(round(magnitude))}"
    return f"{sign}${magnitude:.2f}".rstrip("0").rstrip(".")


def _whole_money(cents: int) -> str:
    dollars = abs(int(cents)) / 100.0
    return f"-${dollars:,.0f}" if cents < 0 else f"${dollars:,.0f}"


def _nice_top(value: int) -> float:
    """Round a maximum up to a readable axis top (1, 2, 2.5, or 5 times a power of ten)."""
    if value <= 0:
        return 100.0
    magnitude = 10.0 ** math.floor(math.log10(value))
    for step in (1.0, 2.0, 2.5, 5.0):
        if value <= step * magnitude:
            return step * magnitude
    return 10.0 * magnitude


def _tick_values(top: float, count: int = 5) -> list[float]:
    return [top * index / (count - 1) for index in range(count)]


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    right: int,
    top: int,
    bottom: int,
    top_value: float,
    low_value: float = 0.0,
) -> None:
    """Five horizontal rules with compact labels, including zero when it is in range."""
    span = top_value - low_value or 1.0
    for offset in _tick_values(top_value - low_value):
        actual = low_value + offset
        y = bottom - offset / span * (bottom - top)
        color = AXIS if abs(actual) < 1e-9 else GRID
        draw.line((left, y, right, y), fill=color, width=2)
        draw.text((left - 14, y), _compact(actual), font=_font(26), fill=MUTED, anchor="rm")


def _draw_legend(
    draw: ImageDraw.ImageDraw,
    entries: Sequence[tuple[str, str, str]],
    *,
    x: int,
    y: int,
) -> None:
    """entries: (label, color, style) where style is fill, dash, or dot."""
    cursor = x
    font = _font(25)
    for label, color, style in entries:
        if style == "fill":
            draw.rounded_rectangle((cursor, y + 8, cursor + 20, y + 28), radius=4, fill=color)
        elif style == "dash":
            for start in range(cursor, cursor + 34, 14):
                draw.line((start, y + 18, start + 8, y + 18), fill=color, width=4)
        else:
            for start in range(cursor, cursor + 34, 12):
                draw.ellipse((start, y + 15, start + 5, y + 20), fill=color)
        width, _ = _text_size(draw, label, font)
        draw.text((cursor + 42, y + 18), label, font=font, fill=MUTED, anchor="lm")
        cursor += 42 + width + 34


def _draw_zero_line(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    right: int,
    top: int,
    bottom: int,
    low: float,
    high: float,
) -> None:
    """A visible baseline when a chart includes negative values."""
    if low >= 0 or high <= low:
        return
    y = bottom - (0 - low) / (high - low) * (bottom - top)
    draw.line((left, y, right, y), fill=AXIS, width=3)


def _dashed_line(
    draw: ImageDraw.ImageDraw,
    points: Sequence[tuple[float, float]],
    *,
    fill: str,
    width: int = 4,
    dash: float = 18,
    gap: float = 10,
) -> None:
    for start, end in zip(points, points[1:]):
        x1, y1 = start
        x2, y2 = end
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= 0:
            continue
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        position = 0.0
        while position < length:
            end_position = min(position + dash, length)
            draw.line(
                (
                    x1 + ux * position,
                    y1 + uy * position,
                    x1 + ux * end_position,
                    y1 + uy * end_position,
                ),
                fill=fill,
                width=width,
            )
            position = end_position + gap


def _dotted_line(
    draw: ImageDraw.ImageDraw,
    points: Sequence[tuple[float, float]],
    *,
    fill: str,
    radius: int = 4,
) -> None:
    """Small dots along each segment, used for a forecast rather than a measured value."""
    for start, end in zip(points, points[1:]):
        x1, y1 = start
        x2, y2 = end
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(length // 14))
        for step in range(steps + 1):
            ratio = step / steps
            x = x1 + (x2 - x1) * ratio
            y = y1 + (y2 - y1) * ratio
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def monthly_chart_png(
    trend: Sequence[MonthTotal],
    *,
    width: int = 1600,
    height: int = 520,
) -> bytes:
    """Grouped income and spending bars for the last six months."""
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    if not trend:
        draw.text((width / 2, height / 2), "No months to chart", font=_font(30), fill=MUTED, anchor="mm")
        return _png_bytes(image)

    left, right, top, bottom = 150, 48, 112, 96
    plot_left, plot_right = left, width - right
    plot_top, plot_bottom = top, height - bottom
    values = [value for month in trend for value in (month.income_cents, month.expense_cents)]
    low = min(0, min(values))
    high = max(0, max(values))
    axis_low = -_nice_top(-low) if low < 0 else 0.0
    axis_high = _nice_top(high) if high > 0 else 100.0
    span = axis_high - axis_low or 1.0

    def y_at(value: float) -> float:
        return plot_bottom - (value - axis_low) / span * (plot_bottom - plot_top)

    _draw_grid(
        draw,
        left=plot_left,
        right=plot_right,
        top=plot_top,
        bottom=plot_bottom,
        top_value=axis_high,
        low_value=axis_low,
    )
    _draw_zero_line(
        draw,
        left=plot_left,
        right=plot_right,
        top=plot_top,
        bottom=plot_bottom,
        low=axis_low,
        high=axis_high,
    )
    _draw_legend(
        draw,
        [("Income", INCOME, "fill"), ("Spending", SPENDING, "fill")],
        x=plot_left,
        y=24,
    )

    groups = len(trend)
    group_width = (plot_right - plot_left) / groups
    bar_width = min(72, max(24, group_width * 0.30))
    gap = 10

    if not any(values):
        draw.text(
            (width / 2, height / 2),
            "No income or spending in this period",
            font=_font(28),
            fill=MUTED,
            anchor="mm",
        )
        return _png_bytes(image)

    zero_y = y_at(0)

    def draw_bar(x: float, value: int, color: str) -> None:
        if value == 0:
            return
        end_y = y_at(value)
        rect_top = min(zero_y, end_y)
        rect_bottom = max(zero_y, end_y)
        radius = min(8, max(1, int((rect_bottom - rect_top) / 2)))
        draw.rounded_rectangle(
            (x, rect_top, x + bar_width, rect_bottom),
            radius=radius,
            fill=color,
        )

    for index, month in enumerate(trend):
        center = plot_left + group_width * (index + 0.5)
        income_x = center - bar_width - gap / 2
        spending_x = center + gap / 2
        draw_bar(income_x, month.income_cents, INCOME)
        draw_bar(spending_x, month.expense_cents, SPENDING)
        draw.text(
            (center, plot_bottom + 24),
            short_month_label(month.month),
            font=_font(26),
            fill=MUTED,
            anchor="ma",
        )
        if index == groups - 1:
            label_font = _font(24)
            if month.income_cents > 0:
                draw.text(
                    (income_x + bar_width / 2, y_at(month.income_cents) - 16),
                    _whole_money(month.income_cents),
                    font=label_font,
                    fill=INK,
                    anchor="mb",
                )
            if month.expense_cents > 0:
                draw.text(
                    (spending_x + bar_width / 2, y_at(month.expense_cents) - 16),
                    _whole_money(month.expense_cents),
                    font=label_font,
                    fill=INK,
                    anchor="mb",
                )
            elif month.expense_cents < 0:
                draw.text(
                    (spending_x + bar_width / 2, y_at(month.expense_cents) + 16),
                    _whole_money(month.expense_cents),
                    font=label_font,
                    fill=INK,
                    anchor="mt",
                )
    return _png_bytes(image)


def cumulative_chart_png(
    labels: Sequence[str],
    budget: Sequence[int],
    spending: Sequence[int],
    actual_months: int,
    *,
    width: int = 1600,
    height: int = 620,
) -> bytes:
    """Cumulative budget against spending, with the future months drawn as a forecast."""
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    count = min(len(labels), len(budget), len(spending))
    if count == 0:
        draw.text((width / 2, height / 2), "No months to chart", font=_font(30), fill=MUTED, anchor="mm")
        return _png_bytes(image)

    left, right, top, bottom = 170, 270, 112, 92
    plot_left, plot_right = left, width - right
    plot_top, plot_bottom = top, height - bottom
    values = [*budget[:count], *spending[:count]]
    if not any(values):
        draw.text(
            (width / 2, height / 2),
            "No budget or spending in this period",
            font=_font(28),
            fill=MUTED,
            anchor="mm",
        )
        return _png_bytes(image)
    low = min(0, min(values))
    high = max(0, max(values))
    axis_low = -_nice_top(-low) if low < 0 else 0.0
    axis_high = _nice_top(high) if high > 0 else 100.0
    span = axis_high - axis_low or 1.0
    _draw_grid(
        draw,
        left=plot_left,
        right=plot_right,
        top=plot_top,
        bottom=plot_bottom,
        top_value=axis_high,
        low_value=axis_low,
    )
    _draw_zero_line(
        draw,
        left=plot_left,
        right=plot_right,
        top=plot_top,
        bottom=plot_bottom,
        low=axis_low,
        high=axis_high,
    )
    _draw_legend(
        draw,
        [("Budget", INCOME, "dash"), ("Spending", SPENDING, "fill"), ("Projection", SPENDING, "dot")],
        x=plot_left,
        y=24,
    )

    def x_at(index: int) -> float:
        return plot_left + (plot_right - plot_left) * index / max(1, count - 1)

    def y_at(value: float) -> float:
        return plot_bottom - (value - axis_low) / span * (plot_bottom - plot_top)

    actual = max(0, min(actual_months, count))
    zero_y = y_at(0)
    if actual > 0:
        actual_points = [(x_at(index), y_at(spending[index])) for index in range(actual)]
        for start, end in zip(actual_points, actual_points[1:]):
            draw.polygon(
                [
                    start,
                    end,
                    (end[0], zero_y),
                    (start[0], zero_y),
                ],
                fill=SPENDING_WASH,
            )
        if len(actual_points) > 1:
            draw.line(actual_points, fill=SPENDING, width=5, joint="curve")
        else:
            x, y = actual_points[0]
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=SPENDING)

    if any(value > 0 for value in budget[:count]):
        budget_points = [(x_at(index), y_at(budget[index])) for index in range(count)]
        _dashed_line(draw, budget_points, fill=INCOME, width=4)
        end_x, end_y = budget_points[-1]
        draw.ellipse((end_x - 5, end_y - 5, end_x + 5, end_y + 5), fill=INCOME)

    if 0 < actual < count:
        projected_points = [(x_at(index), y_at(spending[index])) for index in range(actual - 1, count)]
        _dotted_line(draw, projected_points, fill=SPENDING)
        end_x, end_y = projected_points[-1]
        draw.ellipse((end_x - 6, end_y - 6, end_x + 6, end_y + 6), fill=SPENDING)
        divider_x = x_at(actual - 1)
        _dashed_line(
            draw,
            [(divider_x, plot_top), (divider_x, plot_bottom)],
            fill=AXIS,
            width=2,
            dash=8,
            gap=8,
        )
        draw.text(
            (divider_x + 10, plot_top + 4),
            "actuals end",
            font=_font(23),
            fill=MUTED,
            anchor="la",
        )

    if any(value > 0 for value in budget[:count]):
        budget_end = y_at(budget[count - 1])
        _end_label(draw, plot_right + 20, budget_end - 20, "Budget", _compact(budget[count - 1]), INCOME)
    if actual > 0:
        spending_end = y_at(spending[count - 1])
        label = "Projected" if actual < count else "Spending"
        _end_label(draw, plot_right + 20, spending_end + 26, label, _compact(spending[count - 1]), SPENDING)

    for index, label in enumerate(labels[:count]):
        draw.text(
            (x_at(index), plot_bottom + 22),
            str(label),
            font=_font(24),
            fill=MUTED,
            anchor="ma",
        )
    return _png_bytes(image)


def _end_label(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    prefix: str,
    value: str,
    color: str,
) -> None:
    """A colored swatch and a value label at the right edge of a line chart."""
    draw.rounded_rectangle((x, y - 7, x + 14, y + 7), radius=3, fill=color)
    draw.text((x + 24, y), f"{prefix} {value}", font=_font(24), fill=INK, anchor="lm")


def net_worth_chart_png(
    labels: Sequence[str],
    values: Sequence[int],
    *,
    width: int = 1600,
    height: int = 480,
) -> bytes:
    """A twelve-month net-worth line with a soft area fill and an endpoint label."""
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    count = min(len(labels), len(values))
    if count == 0:
        draw.text((width / 2, height / 2), "No account history yet", font=_font(30), fill=MUTED, anchor="mm")
        return _png_bytes(image)

    left, right, top, bottom = 170, 230, 100, 86
    plot_left, plot_right = left, width - right
    plot_top, plot_bottom = top, height - bottom
    low = min(values[:count])
    high = max(values[:count])
    if low == high:
        padding = max(50_000, abs(high) * 0.1)
    else:
        padding = (high - low) * 0.10
    axis_low = low - padding
    axis_high = high + padding
    span = axis_high - axis_low or 1.0

    for value in _tick_values(span):
        actual = axis_low + value
        y = plot_bottom - value / span * (plot_bottom - plot_top)
        color = AXIS if axis_low <= 0 <= axis_high and abs(actual) < span * 0.02 else GRID
        draw.line((plot_left, y, plot_right, y), fill=color, width=2)
        draw.text((plot_left - 14, y), _compact(actual), font=_font(25), fill=MUTED, anchor="rm")

    def x_at(index: int) -> float:
        return plot_left + (plot_right - plot_left) * index / max(1, count - 1)

    def y_at(value: float) -> float:
        return plot_bottom - (value - axis_low) / span * (plot_bottom - plot_top)

    points = [(x_at(index), y_at(values[index])) for index in range(count)]
    area = [(points[0][0], plot_bottom)] + points + [(points[-1][0], plot_bottom)]
    draw.polygon(area, fill=NET_WORTH_WASH)
    draw.line(points, fill=INCOME, width=5, joint="curve")
    end_x, end_y = points[-1]
    draw.ellipse((end_x - 7, end_y - 7, end_x + 7, end_y + 7), fill=INCOME, outline=WHITE, width=3)
    draw.text(
        (end_x + 18, end_y),
        _whole_money(values[count - 1]),
        font=_font(27),
        fill=INK,
        anchor="lm",
    )
    for index, label in enumerate(labels[:count]):
        draw.text(
            (x_at(index), plot_bottom + 22),
            str(label),
            font=_font(24),
            fill=MUTED,
            anchor="ma",
        )
    return _png_bytes(image)


def month_labels(months: Sequence[str]) -> list[str]:
    """Short month labels for a sequence of YYYY-MM keys."""
    return [calendar.month_abbr[int(month.split("-")[1])] for month in months]
