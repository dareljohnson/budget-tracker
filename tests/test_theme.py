import flet as ft

from budget_tracker.ui import theme


def test_semantic_colors_use_theme_roles():
    assert theme.SEED_COLOR == ft.Colors.PRIMARY
    assert theme.INCOME_COLOR == ft.Colors.TERTIARY
    assert theme.EXPENSE_COLOR == ft.Colors.ERROR
    assert theme.WARNING_COLOR == ft.Colors.SECONDARY


def test_light_and_dark_schemes_have_readable_semantic_colors():
    assert theme.build_theme().color_scheme.error == "#C62828"
    assert theme.build_theme(dark=True).color_scheme.error == "#F2B8B5"
    assert theme.build_theme().color_scheme.tertiary == "#2E7D32"
    assert theme.build_theme(dark=True).color_scheme.tertiary == "#81C784"


def test_text_scale_multiplies_explicit_sizes():
    theme.set_text_scale(1.0)
    assert theme.fs(12) == 12
    theme.set_text_scale(1.15)
    try:
        assert theme.fs(12) == 14
        assert theme.fs(13) == 15
    finally:
        theme.set_text_scale(1.0)


def test_category_colors_lighten_only_in_dark_mode():
    theme.set_dark(False)
    assert theme.readable("#2E7D32") == "#2E7D32"
    theme.set_dark(True)
    try:
        assert theme.readable("#2E7D32") == "#82B184"
        assert theme.readable(None) is None
        assert theme.readable(ft.Colors.PRIMARY) == ft.Colors.PRIMARY
    finally:
        theme.set_dark(False)
