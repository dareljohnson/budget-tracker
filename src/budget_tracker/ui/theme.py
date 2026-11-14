"""Visual design tokens and theme construction."""

from __future__ import annotations

import flet as ft

# Theme roles resolve against the active light/dark scheme, so accents, income,
# expense and warning colors stay readable when the theme changes.
SEED_COLOR = ft.Colors.PRIMARY
INCOME_COLOR = ft.Colors.TERTIARY
EXPENSE_COLOR = ft.Colors.ERROR
WARNING_COLOR = ft.Colors.SECONDARY
ON_SEED_COLOR = ft.Colors.ON_PRIMARY
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

LIGHT_SCHEME = ft.ColorScheme(
    primary="#3F51B5",
    on_primary="#FFFFFF",
    primary_container="#E8EAF6",
    on_primary_container="#1A237E",
    secondary="#EF6C00",
    on_secondary="#FFFFFF",
    secondary_container="#FFE0B2",
    on_secondary_container="#4A2800",
    tertiary="#2E7D32",
    on_tertiary="#FFFFFF",
    tertiary_container="#C8E6C9",
    on_tertiary_container="#0B3D0F",
    error="#C62828",
    on_error="#FFFFFF",
    error_container="#FFDAD6",
    on_error_container="#410002",
    surface="#FDFBFF",
    on_surface="#1B1B1F",
    on_surface_variant="#45464F",
    outline="#757780",
    outline_variant="#C5C6D0",
    surface_container_lowest="#FFFFFF",
    surface_container_low="#F7F2FA",
    surface_container="#F3EDF7",
    surface_container_high="#ECE6F0",
    surface_container_highest="#E6E0E9",
    surface_dim="#DED8E1",
    surface_bright="#FDFBFF",
    inverse_surface="#313033",
    on_inverse_surface="#F4EFF4",
    inverse_primary="#B0BCFF",
    shadow="#000000",
    scrim="#000000",
    surface_tint="#3F51B5",
)

DARK_SCHEME = ft.ColorScheme(
    primary="#B0BCFF",
    on_primary="#1A237E",
    primary_container="#303F9F",
    on_primary_container="#DDE1FF",
    secondary="#FFB74D",
    on_secondary="#3E2600",
    secondary_container="#5C3C00",
    on_secondary_container="#FFE0B2",
    tertiary="#81C784",
    on_tertiary="#0B3D0F",
    tertiary_container="#1B5E20",
    on_tertiary_container="#C8E6C9",
    error="#F2B8B5",
    on_error="#601410",
    error_container="#8C1D18",
    on_error_container="#F9DEDC",
    surface="#141318",
    on_surface="#E6E1E9",
    on_surface_variant="#CAC4D0",
    outline="#938F99",
    outline_variant="#49454F",
    surface_container_lowest="#0F0D13",
    surface_container_low="#1D1B20",
    surface_container="#211F26",
    surface_container_high="#2B2930",
    surface_container_highest="#36343B",
    surface_dim="#141318",
    surface_bright="#3B383E",
    inverse_surface="#E6E1E9",
    on_inverse_surface="#313033",
    inverse_primary="#3F51B5",
    shadow="#000000",
    scrim="#000000",
    surface_tint="#B0BCFF",
)


def build_theme(dark: bool = False) -> ft.Theme:
    return ft.Theme(
        color_scheme=DARK_SCHEME if dark else LIGHT_SCHEME,
        font_family=FONT_FAMILY,
        use_material3=True,
    )


_dark = False


def set_dark(dark: bool) -> None:
    """Remember the active mode for category colors, which are user-chosen hex values."""
    global _dark
    _dark = dark


def readable(color: str | None) -> str | None:
    """Lighten a user-chosen category color so it stays legible on a dark surface."""
    if not _dark or not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
        return color
    try:
        r, g, b = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    except ValueError:
        return color
    return "#{:02X}{:02X}{:02X}".format(
        round(r + (255 - r) * 0.4),
        round(g + (255 - g) * 0.4),
        round(b + (255 - b) * 0.4),
    )


def icon_for(name: str | None) -> ft.IconData:
    return getattr(ft.Icons, name or "", None) or ft.Icons.CATEGORY
