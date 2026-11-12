"""Visual design tokens and theme construction."""

from __future__ import annotations

import flet as ft

SEED_COLOR = "#3F51B5"
INCOME_COLOR = "#2E7D32"
EXPENSE_COLOR = "#C62828"
WARNING_COLOR = "#EF6C00"
FONT_FAMILY = "Segoe UI"

RADIUS = 14
GAP = 16

PALETTE = [
    "#E53935", "#D81B60", "#8E24AA", "#5E35B1", "#3949AB", "#1E88E5",
    "#039BE5", "#00897B", "#43A047", "#2E7D32", "#7CB342", "#F9A825",
    "#FB8C00", "#F4511E", "#6D4C41", "#757575", "#546E7A", "#00ACC1",
]

ICON_CHOICES = [
    "CATEGORY", "HOME", "SHOPPING_CART", "RESTAURANT", "LOCAL_CAFE", "DIRECTIONS_CAR",
    "LOCAL_GAS_STATION", "BOLT", "WATER_DROP", "WIFI", "PHONE_ANDROID", "LOCAL_HOSPITAL",
    "FITNESS_CENTER", "MOVIE", "SPORTS_ESPORTS", "SHOPPING_BAG", "CHECKROOM", "PETS",
    "SCHOOL", "CHILD_CARE", "FLIGHT", "CARD_GIFTCARD", "SAVINGS", "RECEIPT_LONG",
    "WORK", "LAPTOP_MAC", "TRENDING_UP", "ATTACH_MONEY", "ACCOUNT_BALANCE", "PAYMENTS",
]


def build_theme() -> ft.Theme:
    return ft.Theme(color_scheme_seed=SEED_COLOR, font_family=FONT_FAMILY)


def icon_for(name: str | None) -> ft.IconData:
    return getattr(ft.Icons, name or "", None) or ft.Icons.CATEGORY
