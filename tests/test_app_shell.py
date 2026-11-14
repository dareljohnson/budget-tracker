import flet as ft

from budget_tracker.main import _pinned_page


def test_pinned_page_pins_the_header_and_scrolls_the_body():
    header = ft.Text("Header")
    body = ft.Text("Body")
    pinned = _pinned_page(ft.Column([header, body], spacing=12))

    assert isinstance(pinned, ft.Column)
    assert pinned.controls[0] is header
    body_column = pinned.controls[1]
    assert isinstance(body_column, ft.Column)
    assert body_column.controls == [body]
    assert body_column.scroll == ft.ScrollMode.AUTO
    assert body_column.expand is True


def test_pinned_page_leaves_a_non_column_control_alone():
    control = ft.Text("Plain")
    assert _pinned_page(control) is control
