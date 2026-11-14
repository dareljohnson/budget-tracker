"""Visual design tokens and theme construction."""

from __future__ import annotations

from dataclasses import dataclass, fields

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

_text_scale = 1.0


def set_text_scale(scale: float) -> None:
    global _text_scale
    _text_scale = scale


def fs(size: int) -> int:
    """Scale an explicit font/icon size with the user's text-size setting."""
    return max(1, round(size * _text_scale))


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
    secondary="#B45309",
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


@dataclass(frozen=True)
class ThemePreset:
    label: str
    light: dict[str, str]
    dark: dict[str, str]


PRESETS: dict[str, ThemePreset] = {
    "indigo": ThemePreset("Indigo", {}, {}),
    "ocean": ThemePreset(
        "Ocean",
        {
            "primary": "#1565C0",
            "on_primary": "#FFFFFF",
            "primary_container": "#D6E4FF",
            "on_primary_container": "#001B3D",
            "tertiary": "#00695C",
            "on_tertiary": "#FFFFFF",
            "tertiary_container": "#B2DFDB",
            "on_tertiary_container": "#00251F",
        },
        {
            "primary": "#90CAF9",
            "on_primary": "#003258",
            "primary_container": "#0D47A1",
            "on_primary_container": "#D6E4FF",
            "tertiary": "#80CBC4",
            "on_tertiary": "#003731",
            "tertiary_container": "#005048",
            "on_tertiary_container": "#B2DFDB",
        },
    ),
    "forest": ThemePreset(
        "Forest",
        {
            "primary": "#2E7D32",
            "on_primary": "#FFFFFF",
            "primary_container": "#C8E6C9",
            "on_primary_container": "#0B3D0F",
            "tertiary": "#00695C",
            "on_tertiary": "#FFFFFF",
            "tertiary_container": "#B2DFDB",
            "on_tertiary_container": "#00251F",
        },
        {
            "primary": "#A5D6A7",
            "on_primary": "#0B3D0F",
            "primary_container": "#1B5E20",
            "on_primary_container": "#C8E6C9",
            "tertiary": "#80CBC4",
            "on_tertiary": "#003731",
            "tertiary_container": "#005048",
            "on_tertiary_container": "#B2DFDB",
        },
    ),
    "slate": ThemePreset(
        "Slate",
        {
            "primary": "#455A64",
            "on_primary": "#FFFFFF",
            "primary_container": "#CFD8DC",
            "on_primary_container": "#1C313A",
        },
        {
            "primary": "#B0BEC5",
            "on_primary": "#1C313A",
            "primary_container": "#37474F",
            "on_primary_container": "#CFD8DC",
        },
    ),
    "high_contrast": ThemePreset(
        "High contrast",
        {
            "primary": "#0D47A1",
            "on_primary": "#FFFFFF",
            "primary_container": "#D6E4FF",
            "on_primary_container": "#000000",
            "secondary": "#7A3E00",
            "on_secondary": "#FFFFFF",
            "secondary_container": "#FFE0B2",
            "on_secondary_container": "#000000",
            "tertiary": "#1B5E20",
            "on_tertiary": "#FFFFFF",
            "tertiary_container": "#C8E6C9",
            "on_tertiary_container": "#000000",
            "error": "#B71C1C",
            "on_error": "#FFFFFF",
            "error_container": "#FFDAD6",
            "on_error_container": "#000000",
            "surface": "#FFFFFF",
            "on_surface": "#000000",
            "on_surface_variant": "#1A1A1A",
            "outline": "#000000",
            "outline_variant": "#000000",
            "surface_container_lowest": "#FFFFFF",
            "surface_container_low": "#FFFFFF",
            "surface_container": "#FFFFFF",
            "surface_container_high": "#F2F2F2",
            "surface_container_highest": "#E6E6E6",
            "surface_dim": "#FFFFFF",
            "surface_bright": "#FFFFFF",
            "inverse_surface": "#000000",
            "on_inverse_surface": "#FFFFFF",
            "inverse_primary": "#90CAF9",
            "shadow": "#000000",
            "scrim": "#000000",
            "surface_tint": "#0D47A1",
        },
        {
            "primary": "#FFFFFF",
            "on_primary": "#000000",
            "primary_container": "#333333",
            "on_primary_container": "#FFFFFF",
            "secondary": "#FFD180",
            "on_secondary": "#000000",
            "secondary_container": "#4A2C00",
            "on_secondary_container": "#FFFFFF",
            "tertiary": "#69F0AE",
            "on_tertiary": "#000000",
            "tertiary_container": "#00391F",
            "on_tertiary_container": "#FFFFFF",
            "error": "#FF8A80",
            "on_error": "#000000",
            "error_container": "#5C0000",
            "on_error_container": "#FFFFFF",
            "surface": "#000000",
            "on_surface": "#FFFFFF",
            "on_surface_variant": "#F5F5F5",
            "outline": "#FFFFFF",
            "outline_variant": "#FFFFFF",
            "surface_container_lowest": "#000000",
            "surface_container_low": "#0A0A0A",
            "surface_container": "#111111",
            "surface_container_high": "#1A1A1A",
            "surface_container_highest": "#222222",
            "surface_dim": "#000000",
            "surface_bright": "#2A2A2A",
            "inverse_surface": "#FFFFFF",
            "on_inverse_surface": "#000000",
            "inverse_primary": "#0D47A1",
            "shadow": "#000000",
            "scrim": "#000000",
            "surface_tint": "#FFFFFF",
        },
    ),
}


def _scheme(base: ft.ColorScheme, overrides: dict[str, str]) -> ft.ColorScheme:
    values = {field.name: getattr(base, field.name) for field in fields(base)}
    values.update(overrides)
    return ft.ColorScheme(**values)


def build_theme(dark: bool = False, preset: str = "indigo") -> ft.Theme:
    chosen = PRESETS.get(preset, PRESETS["indigo"])
    base = DARK_SCHEME if dark else LIGHT_SCHEME
    return ft.Theme(
        color_scheme=_scheme(base, chosen.dark if dark else chosen.light),
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
