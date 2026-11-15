"""Local Pillow rendering for the cash-flow chart.

Flet 1.1.x has no chart control, so the daily balance line is drawn into an RGBA PNG and handed to
an ``ft.Image``. The renderer is deliberately Flet-free: callers pass theme colors and a text scale,
and the image carries only the line, the zero line, the grid, and the markers. Axis labels remain
the view's responsibility. Everything is drawn at 2x so the bitmap stays crisp on high-DPI screens.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageColor, ImageDraw

SCALE = 2
DEFAULT_WIDTH = 900
DEFAULT_HEIGHT = 260

NEGATIVE_COLOR = "#C62828"
_FALLBACK_RGB = (63, 81, 181)
_GRID_LINES = 3
_RANGE_PADDING = 0.08
_LINE_WIDTH = 2.0
_MARKER_RADIUS = 3.5
_END_MARKER_RADIUS = 2.5
_PAD = 8.0


@dataclass(frozen=True, slots=True)
class ChartColors:
    """Theme roles used by the renderer."""

    primary: str
    grid: str
    text: str
    zero: str
    lowest: str


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


def _pixel_size(width: int, height: int) -> tuple[int, int]:
    try:
        logical_width = max(1, int(width))
        logical_height = max(1, int(height))
    except (TypeError, ValueError):
        logical_width, logical_height = DEFAULT_WIDTH, DEFAULT_HEIGHT
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
    marker_color = NEGATIVE_COLOR if negative else colors.lowest
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
