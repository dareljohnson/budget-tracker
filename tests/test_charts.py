import types
from datetime import date, timedelta
from io import BytesIO

import pytest
from PIL import Image

from budget_tracker.cashflow import CashFlowPoint
from budget_tracker.ui.charts import (
    NEGATIVE_COLOR,
    ChartColors,
    CumulativeMonth,
    cumulative_chart_png,
    line_chart_png,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
COLORS = ChartColors(
    primary="#3F51B5",
    grid="#C5C6D0",
    text="#1B1B1F",
    zero="#757780",
    lowest="#B45309",
)


def _points(*balances: int) -> list[CashFlowPoint]:
    start = date(2026, 11, 14)
    return [CashFlowPoint(start + timedelta(days=index), cents) for index, cents in enumerate(balances)]


def _image(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data)).convert("RGBA")


def _rgb(hex_color: str) -> tuple[int, int, int, int]:
    digits = hex_color.lstrip("#")
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), 255)


def _colors(image: Image.Image) -> set[tuple[int, int, int, int]]:
    return {color for _, color in image.getcolors(maxcolors=2_000_000) or []}


def test_png_has_magic_and_renders_at_twice_the_requested_size():
    data = line_chart_png(_points(100, 250), width=300, height=120, colors=COLORS)

    assert data.startswith(PNG_MAGIC)
    assert _image(data).size == (600, 240)


@pytest.mark.parametrize(
    "balances",
    [
        [5000] * 10,
        [100, 250, 400, 550, 700],
        [500, -100, -250, 200],
        [0, 0, 0],
    ],
)
def test_flat_rising_and_negative_series_render(balances):
    data = line_chart_png(_points(*balances), width=200, height=100, colors=COLORS)

    assert data.startswith(PNG_MAGIC)
    assert _image(data).size == (400, 200)


def test_accepts_duck_typed_points_and_an_out_of_range_lowest_index():
    points = [
        types.SimpleNamespace(date=date(2026, 11, 14), balance_cents=1000),
        types.SimpleNamespace(date=date(2026, 11, 15), balance_cents=-500),
        types.SimpleNamespace(date=date(2026, 11, 16), balance_cents=250),
    ]

    data = line_chart_png(points, width=200, height=100, colors=COLORS, lowest_index=99)

    assert data.startswith(PNG_MAGIC)


def test_lowest_marker_is_amber_normally_and_red_when_negative():
    positive = _colors(_image(line_chart_png(_points(500, 900, 700), width=200, height=100, colors=COLORS)))
    negative = _colors(_image(line_chart_png(_points(500, -100, 700), width=200, height=100, colors=COLORS)))

    assert _rgb(COLORS.lowest) in positive
    assert _rgb(NEGATIVE_COLOR) not in positive
    assert _rgb(NEGATIVE_COLOR) in negative
    assert _rgb(COLORS.lowest) not in negative


def test_text_scale_changes_the_output():
    points = _points(100, 300, 200)

    normal = line_chart_png(points, width=200, height=100, colors=COLORS, text_scale=1.0)
    large = line_chart_png(points, width=200, height=100, colors=COLORS, text_scale=1.5)

    assert normal != large
    assert _image(normal).size == _image(large).size


@pytest.mark.parametrize(
    "points",
    [
        [],
        None,
        [object()],
        [CashFlowPoint(date(2026, 11, 14), None)],
    ],
)
def test_empty_or_invalid_points_return_a_blank_chart(points):
    data = line_chart_png(points, width=120, height=80, colors=COLORS)

    image = _image(data)
    assert data.startswith(PNG_MAGIC)
    assert image.size == (240, 160)
    assert image.getextrema()[3] == (0, 0)


MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _months(
    budgets: list[int],
    spendings: list[int],
    *,
    current: int | None = None,
) -> list[CumulativeMonth]:
    return [
        CumulativeMonth(
            label=label,
            budget_cents=budget,
            spending_cents=spending,
            is_current=index == current,
        )
        for index, (label, budget, spending) in enumerate(zip(MONTH_LABELS, budgets, spendings, strict=True))
    ]


def test_cumulative_png_has_magic_and_renders_at_twice_the_requested_size():
    months = _months([100_000] * 12, [90_000] * 12, current=10)

    data = cumulative_chart_png(months, width=600, height=200, colors=COLORS)

    assert data.startswith(PNG_MAGIC)
    assert _image(data).size == (1200, 400)


@pytest.mark.parametrize("months", [[], None])
def test_cumulative_empty_months_return_a_blank_chart(months):
    data = cumulative_chart_png(months, width=120, height=80, colors=COLORS)

    image = _image(data)
    assert data.startswith(PNG_MAGIC)
    assert image.size == (240, 160)
    assert image.getextrema()[3] == (0, 0)


@pytest.mark.parametrize(
    "budgets, spendings",
    [
        ([0] * 12, [0] * 12),
        ([0] * 10 + [50_000, 0], [0] * 10 + [40_000, 0]),
        ([120_000] * 12, [100_000] * 12),
        ([100_000] * 12, [130_000] * 12),
    ],
    ids=["all-zero", "one-active-month", "under-budget", "over-budget"],
)
def test_cumulative_series_render(budgets, spendings):
    data = cumulative_chart_png(_months(budgets, spendings, current=10), width=300, height=120, colors=COLORS)

    assert data.startswith(PNG_MAGIC)
    assert _image(data).size == (600, 240)


def test_cumulative_spending_is_red_over_budget_and_amber_under_budget():
    over = _colors(_image(cumulative_chart_png(_months([100_000] * 12, [130_000] * 12, current=10), colors=COLORS)))
    under = _colors(_image(cumulative_chart_png(_months([120_000] * 12, [100_000] * 12, current=10), colors=COLORS)))

    assert _rgb(COLORS.primary) in over
    assert _rgb(NEGATIVE_COLOR) in over
    assert _rgb(COLORS.lowest) not in over
    assert _rgb(COLORS.primary) in under
    assert _rgb(COLORS.lowest) in under
    assert _rgb(NEGATIVE_COLOR) not in under


def test_cumulative_text_scale_changes_the_output():
    months = _months([120_000] * 12, [100_000] * 12, current=10)

    normal = cumulative_chart_png(months, width=300, height=120, colors=COLORS, text_scale=1.0)
    large = cumulative_chart_png(months, width=300, height=120, colors=COLORS, text_scale=1.5)

    assert normal != large
    assert _image(normal).size == _image(large).size


@pytest.mark.parametrize("invalid", [0, -1, None, "large", float("nan")])
def test_cumulative_invalid_text_scale_falls_back_to_the_default(invalid):
    months = _months([120_000] * 12, [100_000] * 12, current=10)

    normal = cumulative_chart_png(months, width=300, height=120, colors=COLORS, text_scale=1.0)
    fallback = cumulative_chart_png(months, width=300, height=120, colors=COLORS, text_scale=invalid)

    assert fallback == normal
