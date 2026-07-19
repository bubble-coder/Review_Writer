"""Color palettes and helpers for the desktop interface."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
import re


@dataclass(frozen=True, slots=True)
class ThemePalette:
    """Complete set of colors used by the Tkinter interface."""

    background: str
    surface: str
    surface_muted: str
    border: str
    text: str
    muted: str
    subtle: str
    primary: str
    primary_active: str
    primary_pale: str
    success: str
    success_active: str
    success_pale: str
    warning: str
    warning_pale: str
    sidebar: str
    sidebar_active: str
    sidebar_text: str
    sidebar_muted: str
    secondary_hover: str
    quiet_hover: str
    intro_background: str
    intro_foreground: str
    intro_muted: str
    sidebar_divider: str
    sidebar_heading: str
    sidebar_success: str
    sidebar_footer: str
    text_area: str
    selection: str


@dataclass(frozen=True, slots=True)
class ThemePreset:
    id: str
    name: str
    description: str
    palette: ThemePalette


_BASE = ThemePalette(
    background="#f4f7fb",
    surface="#ffffff",
    surface_muted="#f8fafc",
    border="#dfe6ee",
    text="#172033",
    muted="#667085",
    subtle="#98a2b3",
    primary="#2563eb",
    primary_active="#1d4ed8",
    primary_pale="#eaf1ff",
    success="#16835b",
    success_active="#116a49",
    success_pale="#e8f6f0",
    warning="#b76e00",
    warning_pale="#fff5df",
    sidebar="#122033",
    sidebar_active="#203651",
    sidebar_text="#f8fafc",
    sidebar_muted="#aebdce",
    secondary_hover="#eef3f8",
    quiet_hover="#dce8ff",
    intro_background="#eef4ff",
    intro_foreground="#1e4f9a",
    intro_muted="#53709b",
    sidebar_divider="#263a50",
    sidebar_heading="#73869a",
    sidebar_success="#5bd5a5",
    sidebar_footer="#7f91a5",
    text_area="#fbfcfe",
    selection="#cddfff",
)


THEME_PRESETS: tuple[ThemePreset, ...] = (
    ThemePreset("ocean", "海洋蓝", "清晰、稳重，适合长时间阅读", _BASE),
    ThemePreset(
        "forest",
        "森林绿",
        "柔和自然，降低高亮区域的视觉压力",
        replace(
            _BASE,
            background="#f3f8f5",
            surface_muted="#f6faf8",
            border="#d9e7df",
            primary="#15803d",
            primary_active="#166534",
            primary_pale="#e8f5ec",
            sidebar="#17352a",
            sidebar_active="#24513e",
            secondary_hover="#eaf3ee",
            quiet_hover="#d9eddf",
            intro_background="#eaf6ee",
            intro_foreground="#21613d",
            intro_muted="#557565",
            sidebar_divider="#2c5142",
            selection="#cce8d5",
        ),
    ),
    ThemePreset(
        "violet",
        "紫罗兰",
        "更具创作感，适合写作与方案整理",
        replace(
            _BASE,
            background="#f7f5fb",
            surface_muted="#faf8fc",
            border="#e5deed",
            primary="#7c3aed",
            primary_active="#6d28d9",
            primary_pale="#f1eafe",
            sidebar="#281b3d",
            sidebar_active="#41305d",
            secondary_hover="#f1edf6",
            quiet_hover="#e7dbfb",
            intro_background="#f2ecfd",
            intro_foreground="#5b2aa5",
            intro_muted="#74618f",
            sidebar_divider="#44345c",
            selection="#dfd0fa",
        ),
    ),
    ThemePreset(
        "amber",
        "暖橙",
        "温暖醒目，强调关键操作与进度",
        replace(
            _BASE,
            background="#faf7f3",
            surface_muted="#fcfaf7",
            border="#eadfd3",
            primary="#c25d0b",
            primary_active="#9a4606",
            primary_pale="#fff0e2",
            sidebar="#39261d",
            sidebar_active="#594033",
            secondary_hover="#f5eee7",
            quiet_hover="#f9dfc8",
            intro_background="#fff1e4",
            intro_foreground="#914509",
            intro_muted="#806653",
            sidebar_divider="#584033",
            selection="#f4d5ba",
        ),
    ),
    ThemePreset(
        "rose",
        "玫瑰红",
        "细腻而有辨识度，适合重点标注",
        replace(
            _BASE,
            background="#faf5f7",
            surface_muted="#fcf8fa",
            border="#eadce2",
            primary="#be185d",
            primary_active="#9d174d",
            primary_pale="#fce8f0",
            sidebar="#3b1828",
            sidebar_active="#5b2940",
            secondary_hover="#f5ebef",
            quiet_hover="#f8d9e6",
            intro_background="#fcebf2",
            intro_foreground="#8f1749",
            intro_muted="#846071",
            sidebar_divider="#5a2d41",
            selection="#f3cddd",
        ),
    ),
    ThemePreset(
        "aurora",
        "极光青",
        "清透冷静，适合检索、数据核验与长时间工作",
        replace(
            _BASE,
            background="#f1f8fa",
            surface_muted="#f5fafb",
            border="#d5e7ea",
            primary="#0891b2",
            primary_active="#0e7490",
            primary_pale="#e2f5f8",
            sidebar="#12343d",
            sidebar_active="#1d515d",
            secondary_hover="#e7f2f4",
            quiet_hover="#ccecf1",
            intro_background="#e5f6f8",
            intro_foreground="#176579",
            intro_muted="#52747d",
            sidebar_divider="#2b5660",
            selection="#c5eaf0",
        ),
    ),
    ThemePreset(
        "indigo",
        "靛青蓝",
        "理性专注，强化研究阶段与操作层级",
        replace(
            _BASE,
            background="#f4f5fb",
            surface_muted="#f7f7fc",
            border="#dde0ef",
            primary="#4f46e5",
            primary_active="#4338ca",
            primary_pale="#ecebff",
            sidebar="#202449",
            sidebar_active="#343a68",
            secondary_hover="#eceef6",
            quiet_hover="#dcdbfb",
            intro_background="#edeeff",
            intro_foreground="#4039a5",
            intro_muted="#64658b",
            sidebar_divider="#3c426d",
            selection="#d5d3fa",
        ),
    ),
    ThemePreset(
        "graphite",
        "石墨灰",
        "克制中性，突出正文、表格和证据内容",
        replace(
            _BASE,
            background="#f3f5f7",
            surface_muted="#f6f8f9",
            border="#dce1e6",
            primary="#475569",
            primary_active="#334155",
            primary_pale="#e9edf1",
            sidebar="#202832",
            sidebar_active="#35414e",
            secondary_hover="#e9edf0",
            quiet_hover="#d9e0e6",
            intro_background="#eaf0f3",
            intro_foreground="#34495a",
            intro_muted="#667580",
            sidebar_divider="#3a4652",
            selection="#d4dce3",
        ),
    ),
)

_PRESETS_BY_ID = {preset.id: preset for preset in THEME_PRESETS}
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_hex_color(value: str, fallback: str = _BASE.primary) -> str:
    """Return a normalized ``#rrggbb`` value or the supplied fallback."""

    candidate = value.strip()
    return candidate.lower() if _HEX_COLOR.fullmatch(candidate) else fallback


def _mix(color: str, other: str, amount: float) -> str:
    first = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    second = tuple(int(other[index : index + 2], 16) for index in (1, 3, 5))
    values = tuple(round(left * (1 - amount) + right * amount) for left, right in zip(first, second))
    return "#" + "".join(f"{value:02x}" for value in values)


def get_palette(theme_id: str, custom_accent: str = _BASE.primary) -> ThemePalette:
    """Resolve a built-in palette or create one from a custom accent color."""

    if theme_id != "custom":
        return _PRESETS_BY_ID.get(theme_id, _PRESETS_BY_ID["ocean"]).palette
    accent = normalize_hex_color(custom_accent)
    return replace(
        _BASE,
        primary=accent,
        primary_active=_mix(accent, "#000000", 0.18),
        primary_pale=_mix(accent, "#ffffff", 0.89),
        sidebar=_mix(accent, "#000000", 0.72),
        sidebar_active=_mix(accent, "#000000", 0.58),
        quiet_hover=_mix(accent, "#ffffff", 0.80),
        intro_background=_mix(accent, "#ffffff", 0.91),
        intro_foreground=_mix(accent, "#000000", 0.24),
        intro_muted=_mix(accent, "#667085", 0.58),
        sidebar_divider=_mix(accent, "#000000", 0.52),
        selection=_mix(accent, "#ffffff", 0.72),
    )


def palette_color_map(old: ThemePalette, new: ThemePalette) -> dict[str, str]:
    """Map the colors of one palette to the corresponding colors of another."""

    mapping: dict[str, str] = {}
    for item in fields(ThemePalette):
        mapping.setdefault(getattr(old, item.name).lower(), getattr(new, item.name))
    return mapping
