"""Local Pillow rendering for the cash-flow, cumulative year and debt charts.

Flet 1.1.x has no chart control, so charts are drawn into an RGBA PNG and handed to an ``ft.Image``.
The renderers are deliberately Flet-free: callers pass theme colors and a text scale. The cash-flow
line carries only the line, the zero line, the grid, and the markers; axis labels remain the view's
responsibility. The cumulative year chart is self-contained: it draws its own compact y-axis labels
and month labels. The debt chart stacks one colored band per debt and adds direct labels, a total
line, year labels and a visible surface gap between bands. Everything is drawn at 2x so the bitmap
stays crisp on high-DPI screens.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

SCALE = 2
DEFAULT_WIDTH = 900
DEFAULT_HEIGHT = 260
CUMULATIVE_WIDTH = 1200
CUMULATIVE_HEIGHT = 320
DEBT_WIDTH = 900
DEBT_HEIGHT = 280

NEGATIVE_COLOR = "#C62828"

# The categorical order is fixed. The dark steps are the same hues selected for a dark card
# surface, not an automatic lightening of the light values.
DEBT_SERIES_COLORS_LIGHT = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
)
DEBT_SERIES_COLORS_DARK = (
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#0ca30c",
    "#9085e9",
    "#e66767",
)
DEBT_OTHER_COLOR_LIGHT = "#757780"
DEBT_OTHER_COLOR_DARK = "#938F99"
_FALLBACK_RGB = (63, 81, 181)
_GRID_LINES = 3
_CUMULATIVE_STEPS = 4
_RANGE_PADDING = 0.08
_LINE_WIDTH = 2.0
_MARKER_RADIUS = 3.5
_END_MARKER_RADIUS = 2.5
_PAD = 8.0
_LABEL_FONT_SIZE = 11
_DEBT_LABEL_FONT_SIZE = 11
_BAND_ALPHA = 26
_FILL_ALPHA = 30
_GRID_ALPHA = 80
_BASELINE_ALPHA = 120
_LABEL_ALPHA = 210
_DASH_LENGTH = 6.0
_DASH_GAP = 4.0


@dataclass(frozen=True, slots=True)
class ChartColors:
    """Theme roles used by the renderer."""

    primary: str
    grid: str
    text: str
    zero: str
    lowest: str
    negative: str = NEGATIVE_COLOR
    surface: str = "#F7F2FA"


DEFAULT_COLORS = ChartColors(
    primary="#3F51B5",
    grid="#C5C6D0",
    text="#1B1B1F",
    zero="#757780",
    lowest="#B45309",
)


def line_chart_png(
    points: Sequence[Any] | None,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    colors: ChartColors | None = None,
    text_scale: float = 1.0,
    lowest_index: int | None = None,
) -> bytes:
    """Render ``points`` as a transparent PNG at twice the requested logical size.

    ``points`` may be ``CashFlowPoint`` values or any object with a ``balance_cents`` attribute.
    An empty sequence, a non-sequence, or a point without an integer balance yields a blank chart
    instead of raising. ``lowest_index`` marks a specific point; otherwise the lowest balance wins,
    with the first occurrence breaking ties.
    """
    colors = colors or DEFAULT_COLORS
    text_scale = _positive_float(text_scale, 1.0)
    pixel_width, pixel_height = _pixel_size(width, height)
    image = Image.new("RGBA", (pixel_width, pixel_height), (0, 0, 0, 0))

    balances = _balances(points)
    if balances:
        _draw_chart(
            image,
            balances,
            colors=colors,
            text_scale=text_scale,
            lowest_index=lowest_index,
        )
    return _png_bytes(image)


@dataclass(frozen=True, slots=True)
class CumulativeMonth:
    """One month of the year chart; the renderer accumulates these across the year."""

    label: str
    budget_cents: int
    spending_cents: int
    is_current: bool = False


@dataclass(frozen=True, slots=True)
class DebtSeries:
    """One stacked debt band; balances start at today and continue at each month end."""

    label: str
    balances_cents: Sequence[int]
    color: str


def cumulative_chart_png(
    months: Sequence[CumulativeMonth] | None,
    *,
    width: int = CUMULATIVE_WIDTH,
    height: int = CUMULATIVE_HEIGHT,
    colors: ChartColors | None = None,
    text_scale: float = 1.0,
) -> bytes:
    """Render cumulative budget and spending lines as a transparent PNG at twice the logical size.

    Each month's values are running totals: the chart plots cumulative budget against cumulative
    spending, so a crossing point shows the month the year moved over budget. An empty or invalid
    sequence yields a blank chart instead of raising. The current month gets a shaded band and a
    bold label; y-axis labels are compact (``$0`` … ``$5k``) and scale to the tallest series.
    """
    colors = colors or DEFAULT_COLORS
    text_scale = _positive_float(text_scale, 1.0)
    pixel_width, pixel_height = _pixel_size(width, height, CUMULATIVE_WIDTH, CUMULATIVE_HEIGHT)
    image = Image.new("RGBA", (pixel_width, pixel_height), (0, 0, 0, 0))

    rows = _cumulative_rows(months)
    if rows:
        _draw_cumulative_chart(image, rows, colors=colors, text_scale=text_scale)
    return _png_bytes(image)


def debt_stack_chart_png(
    series: Sequence[DebtSeries] | None,
    month_keys: Sequence[str] | None,
    *,
    width: int = DEBT_WIDTH,
    height: int = DEBT_HEIGHT,
    colors: ChartColors | None = None,
    text_scale: float = 1.0,
) -> bytes:
    """Render debt balances as a stacked area chart with a debt name in each band.

    ``balances_cents`` includes the current balance at index 0, then the ending balance for
    each simulation month. ``month_keys`` are ``YYYY-MM`` values for those points. The chart
    keeps its own compact axis labels and draws a 2px surface-colored gap between bands.
    """
    colors = colors or DEFAULT_COLORS
    text_scale = _positive_float(text_scale, 1.0)
    pixel_width, pixel_height = _pixel_size(width, height, DEBT_WIDTH, DEBT_HEIGHT)
    image = Image.new("RGBA", (pixel_width, pixel_height), (0, 0, 0, 0))

    rows, keys = _debt_rows(series, month_keys)
    if rows:
        _draw_debt_stack(image, rows, keys, colors=colors, text_scale=text_scale)
    return _png_bytes(image)


def _debt_rows(
    series: Sequence[Any] | None, month_keys: Sequence[str] | None
) -> tuple[list[tuple[str, tuple[int, ...], str]], list[str]]:
    try:
        keys = [str(key) for key in (month_keys or ())]
        rows: list[tuple[str, tuple[int, ...], str]] = []
        for item in series or ():
            balances = tuple(max(0, int(value)) for value in item.balances_cents)
            rows.append((str(item.label), balances, str(item.color)))
    except (AttributeError, TypeError, ValueError):
        return [], []
    if not rows or not keys:
        return [], []
    count = min([len(keys), *(len(balances) for _, balances, _ in rows)])
    if count <= 0:
        return [], []
    return (
        [(label, balances[:count], color) for label, balances, color in rows if balances],
        keys[:count],
    )


def _draw_debt_stack(
    image: Image.Image,
    rows: list[tuple[str, tuple[int, ...], str]],
    keys: list[str],
    *,
    colors: ChartColors,
    text_scale: float,
) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    pad = max(2, round(_PAD * text_scale * SCALE))
    gap = max(2, round(4 * text_scale * SCALE))
    font = ImageFont.load_default(size=max(1, int(_DEBT_LABEL_FONT_SIZE * text_scale * SCALE)))

    totals = [sum(balances[index] for _, balances, _ in rows) for index in range(len(keys))]
    axis_top, ticks = _debt_axis(max(totals, default=0))
    tick_labels = [_money_label(tick) for tick in ticks]
    tick_sizes = [_text_size(draw, label, font) for label in tick_labels]
    label_height = max(size[1] for size in tick_sizes)

    left = pad + max(size[0] for size in tick_sizes) + gap
    right = max(left + 1, width - pad)
    top = pad
    bottom = max(top + 1, height - pad - label_height - gap)

    def x_for(index: int) -> float:
        if len(keys) == 1:
            return left + (right - left) / 2
        return left + (right - left) * index / (len(keys) - 1)

    def y_for(cents: float) -> float:
        clamped = min(max(float(cents), 0.0), axis_top)
        return bottom - clamped / axis_top * (bottom - top)

    for index, (tick, label) in enumerate(zip(ticks, tick_labels, strict=True)):
        y = y_for(tick)
        draw.line(
            (left, y, right, y),
            fill=_rgba(colors.zero if index == 0 else colors.grid, 170 if index == 0 else 90),
            width=SCALE,
        )
        draw.text(
            (left - gap, y),
            label,
            font=font,
            fill=_rgba(colors.text, _LABEL_ALPHA),
            anchor="rm",
        )

    cumulative = [0] * len(keys)
    boundaries: list[list[tuple[float, float]]] = []
    direct_labels: list[tuple[str, float, float]] = []
    for label, balances, color in rows:
        tops = [cumulative[index] + balances[index] for index in range(len(keys))]
        top_points = [(x_for(index), y_for(value)) for index, value in enumerate(tops)]
        bottom_points = [
            (x_for(index), y_for(value)) for index, value in enumerate(cumulative)
        ]
        draw.polygon(
            [*top_points, *reversed(bottom_points)],
            fill=_rgba(color),
        )
        boundaries.append(top_points)
        direct_labels.append((label, cumulative[0], tops[0]))
        cumulative = tops

    # The gap belongs to the surface, so the bands stay separable even when their hues meet.
    for points in boundaries[:-1]:
        draw.line(
            points,
            fill=_rgba(colors.surface),
            width=max(1, round(2 * SCALE)),
            joint="curve",
        )

    total_points = [
        (x_for(index), y_for(value)) for index, value in enumerate(cumulative)
    ]
    draw.line(
        total_points,
        fill=_rgba(colors.zero, 210),
        width=max(1, round(1.6 * SCALE)),
        joint="curve",
    )
    if cumulative and cumulative[-1] <= 1:
        _disc(
            draw,
            total_points[-1][0],
            total_points[-1][1],
            max(2.0, 4.0 * SCALE),
            _rgba(colors.primary),
        )

    _draw_debt_direct_labels(
        draw,
        direct_labels,
        left=left,
        plot_width=right - left,
        y_for=y_for,
        font=font,
        text_scale=text_scale,
        colors=colors,
    )
    _draw_debt_x_labels(
        draw,
        keys,
        x_for=x_for,
        bottom=bottom,
        gap=gap,
        pad=pad,
        width=width,
        font=font,
        colors=colors,
    )


def _debt_axis(max_cents: int) -> tuple[int, list[int]]:
    """A zero-based axis with four intervals and a readable money step."""
    if max_cents <= 0:
        return 100, [0, 25, 50, 75, 100]
    raw_step = max_cents / 4
    magnitude = 10.0 ** math.floor(math.log10(raw_step))
    step = 10.0 * magnitude
    for candidate in (1.0, 2.0, 2.5, 5.0, 10.0):
        proposed = candidate * magnitude
        if proposed * 4 >= max_cents:
            step = proposed
            break
    top = int(round(step * 4))
    return top, [int(round(step * index)) for index in range(5)]


def _draw_debt_direct_labels(
    draw: ImageDraw.ImageDraw,
    labels: list[tuple[str, float, float]],
    *,
    left: float,
    plot_width: float,
    y_for: Callable[[float], float],
    font: ImageFont.FreeTypeFont,
    text_scale: float,
    colors: ChartColors,
) -> None:
    label_height = _text_size(draw, "Ag", font)[1]
    max_width = max(1.0, plot_width * 0.34)
    for label, first_bottom, first_top in labels:
        if first_top - first_bottom < label_height + 5 * SCALE:
            continue
        text = _truncate_text(draw, label, font, max_width)
        draw.text(
            (left + round(6 * text_scale * SCALE), y_for((first_bottom + first_top) / 2)),
            text,
            font=font,
            fill=_rgba(colors.text, _LABEL_ALPHA),
            anchor="lm",
            stroke_width=max(1, SCALE),
            stroke_fill=_rgba(colors.surface),
        )


def _draw_debt_x_labels(
    draw: ImageDraw.ImageDraw,
    keys: list[str],
    *,
    x_for: Callable[[int], float],
    bottom: float,
    gap: int,
    pad: int,
    width: int,
    font: ImageFont.FreeTypeFont,
    colors: ChartColors,
) -> None:
    candidates: list[tuple[int, str]] = []
    for index, key in enumerate(keys):
        year, month = _month_parts(key)
        if month == 1:
            candidates.append((index, str(year)))
    last_index = len(keys) - 1
    last_year, last_month = _month_parts(keys[last_index])
    if last_month != 1 and 1 <= last_month <= 12:
        candidates.append((last_index, f"{_SHORT_MONTHS[last_month - 1]} ’{str(last_year)[-2:]}"))

    placed: list[tuple[float, float]] = []
    for index, text in sorted(candidates):
        text_width = _text_size(draw, text, font)[0]
        center = min(max(x_for(index), pad + text_width / 2), width - pad - text_width / 2)
        if any(
            abs(center - previous_center) < (text_width + previous_width) / 2 + gap
            for previous_center, previous_width in placed
        ):
            continue
        draw.text(
            (center, bottom + gap),
            text,
            font=font,
            fill=_rgba(colors.text, _LABEL_ALPHA),
            anchor="mt",
        )
        placed.append((center, text_width))
    if not placed and keys:
        text = keys[-1]
        text_width = _text_size(draw, text, font)[0]
        draw.text(
            (min(max(x_for(last_index), pad + text_width / 2), width - pad - text_width / 2), bottom + gap),
            text,
            font=font,
            fill=_rgba(colors.text, _LABEL_ALPHA),
            anchor="mt",
        )


_SHORT_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _month_parts(key: str) -> tuple[int, int]:
    try:
        year, month = key.split("-")[:2]
        return int(year), int(month)
    except (TypeError, ValueError):
        return 0, 0


def _truncate_text(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float
) -> str:
    if _text_size(draw, text, font)[0] <= max_width:
        return text
    ellipsis = "…"
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _text_size(draw, text[:middle] + ellipsis, font)[0] <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low] + ellipsis if low else ellipsis


def _pixel_size(
    width: int,
    height: int,
    fallback_width: int = DEFAULT_WIDTH,
    fallback_height: int = DEFAULT_HEIGHT,
) -> tuple[int, int]:
    try:
        logical_width = max(1, int(width))
    except (TypeError, ValueError):
        logical_width = fallback_width
    try:
        logical_height = max(1, int(height))
    except (TypeError, ValueError):
        logical_height = fallback_height
    return logical_width * SCALE, logical_height * SCALE


def _positive_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number or number <= 0:  # NaN or non-positive
        return fallback
    return min(number, 4.0)


def _balances(points: Sequence[Any] | None) -> list[int]:
    try:
        values = [int(point.balance_cents) for point in points or ()]
    except (AttributeError, TypeError, ValueError):
        return []
    return values


def _cumulative_rows(months: Sequence[Any] | None) -> list[tuple[str, int, int, bool]]:
    rows: list[tuple[str, int, int, bool]] = []
    try:
        for month in months or ():
            rows.append(
                (
                    str(month.label),
                    int(month.budget_cents),
                    int(month.spending_cents),
                    bool(month.is_current),
                )
            )
    except (AttributeError, TypeError, ValueError):
        return []
    return rows


def _cumulative_values(rows: list[tuple[str, int, int, bool]]) -> tuple[list[int], list[int]]:
    budgets: list[int] = []
    spendings: list[int] = []
    running_budget = 0
    running_spending = 0
    for _, budget, spending, _ in rows:
        running_budget += budget
        running_spending += spending
        budgets.append(running_budget)
        spendings.append(running_spending)
    return budgets, spendings


def _axis_top(highest_cents: int) -> float:
    """Round a maximum up to a readable axis top (1, 2, 2.5, or 5 times a power of ten)."""
    value = float(highest_cents)
    if value <= 0:
        return 100.0
    magnitude = 10.0 ** math.floor(math.log10(value))
    for step in (1.0, 2.0, 2.5, 5.0):
        candidate = step * magnitude
        if value <= candidate:
            return candidate
    return 10.0 * magnitude


def _money_label(cents: float) -> str:
    """Format cents as a compact axis label: ``$0``, ``$62.5``, ``$1.25k``."""
    dollars = cents / 100.0
    if abs(dollars) >= 1000:
        return f"${dollars / 1000.0:g}k"
    if abs(dollars - round(dollars)) < 1e-9:
        return f"${int(round(dollars))}"
    return f"${dollars:.2f}".rstrip("0").rstrip(".")


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[float, float]:
    box = draw.textbbox((0, 0), text or " ", font=font)
    return float(box[2] - box[0]), float(box[3] - box[1])


def _draw_cumulative_chart(
    image: Image.Image,
    rows: list[tuple[str, int, int, bool]],
    *,
    colors: ChartColors,
    text_scale: float,
) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    pad = max(2, round(_PAD * text_scale * SCALE))
    gap = max(2, round(4 * text_scale * SCALE))
    font = ImageFont.load_default(size=max(1, int(_LABEL_FONT_SIZE * text_scale * SCALE)))

    budgets, spendings = _cumulative_values(rows)
    axis_top = _axis_top(max([*budgets, *spendings, 0]))
    tick_values = [axis_top * step / _CUMULATIVE_STEPS for step in range(_CUMULATIVE_STEPS + 1)]
    tick_labels = [_money_label(round(value)) for value in tick_values]
    tick_sizes = [_text_size(draw, label, font) for label in tick_labels]
    label_height = max(size[1] for size in tick_sizes)

    left = pad + max(size[0] for size in tick_sizes) + gap
    right = max(left + 1, width - pad)
    top = pad + label_height
    bottom = max(top + 1, height - pad - label_height - gap)

    def x_for(index: int) -> float:
        if len(rows) == 1:
            return left + (right - left) / 2
        return left + (right - left) * index / (len(rows) - 1)

    def y_for(cents: float) -> float:
        clamped = min(max(float(cents), 0.0), axis_top)
        return bottom - clamped / axis_top * (bottom - top)

    current_index = next((index for index, row in enumerate(rows) if row[3]), None)
    if current_index is not None:
        band_left = left if current_index == 0 else (x_for(current_index - 1) + x_for(current_index)) / 2
        band_right = right if current_index == len(rows) - 1 else (x_for(current_index) + x_for(current_index + 1)) / 2
        draw.rectangle((band_left, top, band_right, bottom), fill=_rgba(colors.primary, _BAND_ALPHA))

    for index, (value, label) in enumerate(zip(tick_values, tick_labels, strict=True)):
        y = y_for(value)
        if index == 0:
            draw.line((left, y, right, y), fill=_rgba(colors.zero, _BASELINE_ALPHA), width=SCALE)
        else:
            draw.line((left, y, right, y), fill=_rgba(colors.grid, _GRID_ALPHA), width=SCALE)
        draw.text((left - gap, y), label, font=font, fill=_rgba(colors.text, _LABEL_ALPHA), anchor="rm")

    budget_points = [(x_for(index), y_for(cents)) for index, cents in enumerate(budgets)]
    spending_points = [(x_for(index), y_for(cents)) for index, cents in enumerate(spendings)]
    over_budget = any(spending > budget for budget, spending in zip(budgets, spendings, strict=True))
    spending_color = colors.negative if over_budget else colors.lowest

    if len(spending_points) > 1:
        area = [(spending_points[0][0], bottom), *spending_points, (spending_points[-1][0], bottom)]
        draw.polygon(area, fill=_rgba(spending_color, _FILL_ALPHA))

    _dashed_polyline(
        draw,
        budget_points,
        fill=_rgba(colors.primary),
        width=max(1, round(_LINE_WIDTH * SCALE)),
        dash=_DASH_LENGTH * SCALE,
        gap=_DASH_GAP * SCALE,
    )
    _disc(
        draw,
        *budget_points[-1],
        _END_MARKER_RADIUS * text_scale * SCALE,
        _rgba(colors.primary),
    )

    if len(spending_points) > 1:
        draw.line(
            spending_points,
            fill=_rgba(spending_color),
            width=max(1, round(_LINE_WIDTH * SCALE)),
            joint="curve",
        )
    _disc(
        draw,
        *spending_points[-1],
        _END_MARKER_RADIUS * text_scale * SCALE,
        _rgba(spending_color),
    )

    _draw_month_labels(
        draw,
        rows,
        x_for=x_for,
        bottom=bottom,
        gap=gap,
        pad=pad,
        width=width,
        font=font,
        colors=colors,
    )


def _draw_month_labels(
    draw: ImageDraw.ImageDraw,
    rows: list[tuple[str, int, int, bool]],
    *,
    x_for: Callable[[int], float],
    bottom: float,
    gap: int,
    pad: int,
    width: int,
    font: ImageFont.FreeTypeFont,
    colors: ChartColors,
) -> None:
    sizes = [_text_size(draw, row[0], font) for row in rows]
    spacing = x_for(1) - x_for(0) if len(rows) > 1 else float(width)
    widest = max(size[0] for size in sizes)
    step = 1
    if spacing > 0 and widest > 0:
        step = max(1, math.ceil((widest + gap) / spacing))

    for index, row in enumerate(rows):
        label = row[0]
        is_current = row[3]
        if not label or (step > 1 and index % step and not is_current):
            continue
        text_width = sizes[index][0]
        center = min(max(x_for(index), pad + text_width / 2), width - pad - text_width / 2)
        color = colors.primary if is_current else colors.text
        stroke = max(1, SCALE // 2) if is_current else 0
        draw.text(
            (center, bottom + gap),
            label,
            font=font,
            fill=_rgba(color, _LABEL_ALPHA),
            anchor="mt",
            stroke_width=stroke,
            stroke_fill=_rgba(color, _LABEL_ALPHA),
        )


def _dashed_polyline(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    fill: tuple[int, int, int, int],
    width: int,
    dash: float,
    gap: float,
) -> None:
    for start, end in zip(points, points[1:]):
        _dashed_segment(draw, start, end, fill=fill, width=width, dash=dash, gap=gap)


def _dashed_segment(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    fill: tuple[int, int, int, int],
    width: int,
    dash: float,
    gap: float,
) -> None:
    x1, y1 = start
    x2, y2 = end
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 0 or dash <= 0 or gap < 0:
        return
    unit_x = (x2 - x1) / length
    unit_y = (y2 - y1) / length
    position = 0.0
    while position < length:
        segment_end = min(position + dash, length)
        draw.line(
            (x1 + unit_x * position, y1 + unit_y * position, x1 + unit_x * segment_end, y1 + unit_y * segment_end),
            fill=fill,
            width=width,
        )
        position = segment_end + gap


def _draw_chart(
    image: Image.Image,
    balances: list[int],
    *,
    colors: ChartColors,
    text_scale: float,
    lowest_index: int | None,
) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    pad = max(2, round(_PAD * text_scale * SCALE))
    left, top = pad, pad
    right = max(left + 1, width - pad)
    bottom = max(top + 1, height - pad)
    plot_width = right - left
    plot_height = bottom - top

    lowest_value = min(balances)
    highest_value = max(balances)
    low, high = _value_range(lowest_value, highest_value)

    def y_for(cents: int) -> float:
        return bottom - (cents - low) / (high - low) * plot_height

    def x_for(index: int) -> float:
        if len(balances) == 1:
            return left + plot_width / 2
        return left + plot_width * index / (len(balances) - 1)

    grid_color = _rgba(colors.grid, 90)
    for step in range(1, _GRID_LINES + 1):
        grid_y = top + plot_height * step / (_GRID_LINES + 1)
        draw.line((left, grid_y, right, grid_y), fill=grid_color, width=SCALE)

    coordinates = [(x_for(index), y_for(cents)) for index, cents in enumerate(balances)]

    if lowest_value <= 0 <= highest_value:
        zero_y = y_for(0)
        draw.line((left, zero_y, right, zero_y), fill=_rgba(colors.zero, 170), width=SCALE)

    if len(coordinates) > 1:
        area = [(coordinates[0][0], bottom), *coordinates, (coordinates[-1][0], bottom)]
        draw.polygon(area, fill=_rgba(colors.primary, 32))
        draw.line(
            coordinates,
            fill=_rgba(colors.primary),
            width=max(1, round(_LINE_WIDTH * SCALE)),
            joint="curve",
        )

    end_x, end_y = coordinates[-1]
    _disc(draw, end_x, end_y, _END_MARKER_RADIUS * text_scale * SCALE, _rgba(colors.primary))

    marker_index = _lowest_position(balances, lowest_index)
    marker_x, marker_y = coordinates[marker_index]
    negative = balances[marker_index] < 0
    marker_color = colors.negative if negative else colors.lowest
    marker_radius = max(1.0, _MARKER_RADIUS * text_scale * SCALE)
    # A translucent halo keeps the marker visible over the line without hiding the series.
    _disc(draw, marker_x, marker_y, marker_radius * 2.0, _rgba(marker_color, 45))
    _disc(draw, marker_x, marker_y, marker_radius, _rgba(marker_color))


def _value_range(lowest: int, highest: int) -> tuple[float, float]:
    if lowest == highest:
        padding = max(100.0, abs(lowest) * 0.1)
    else:
        padding = (highest - lowest) * _RANGE_PADDING
    return float(lowest) - padding, float(highest) + padding


def _lowest_position(balances: list[int], lowest_index: int | None) -> int:
    if lowest_index is not None:
        try:
            index = int(lowest_index)
        except (TypeError, ValueError):
            index = -1
        if 0 <= index < len(balances):
            return index
    return min(range(len(balances)), key=balances.__getitem__)


def _disc(draw: ImageDraw.ImageDraw, x: float, y: float, radius: float, fill: tuple[int, int, int, int]) -> None:
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _rgba(value: Any, alpha: int = 255) -> tuple[int, int, int, int]:
    rgb: Any
    try:
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            rgb = value[:3]
        else:
            rgb = ImageColor.getrgb(str(value))
    except (TypeError, ValueError):
        rgb = _FALLBACK_RGB
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]), alpha)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
