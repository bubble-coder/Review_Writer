"""Small, theme-aware pixel icons shared by the Tkinter interface."""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from weakref import WeakKeyDictionary


ICON_SIZE = 16

_PATTERNS: dict[str, tuple[str, ...]] = {
    "app": (
        "...##...", "..####..", ".##..##.", "##.##.##",
        "##.##.##", ".##..##.", "..####..", "...##...",
    ),
    "grid": (
        "........", ".##..##.", ".##..##.", "........",
        ".##..##.", ".##..##.", "........", "........",
    ),
    "plan": (
        "......#.", ".....##.", "....##..", "...##...",
        "..##....", ".##.....", ".#......", "###.....",
    ),
    "review": (
        ".#####..", ".#...#..", ".###.#..", ".#...#..",
        ".###.#..", ".#...#..", ".#####..", "........",
    ),
    "search": (
        "..###...", ".#...#..", ".#...#..", "..###...",
        "....##..", ".....##.", "......#.", "........",
    ),
    "transfer": (
        "........", ".####.#.", ".....##.", ".#####..",
        "..#####.", ".##.....", ".#.####.", "........",
    ),
    "reading": (
        "........", ".###.###", ".#.#.#.#", ".#.#.#.#",
        ".#.#.#.#", ".###.###", "...##...", "........",
    ),
    "report": (
        ".#####..", ".#...#..", ".###.#..", ".#...#..",
        ".##.##..", ".#.#.#..", ".#####..", "........",
    ),
    "tasks": (
        "........", ".##.###.", ".##.....", "....###.",
        ".##.....", ".##.###.", "........", "........",
    ),
    "database": (
        "..####..", ".#....#.", ".#....#.", "..####..",
        ".#....#.", ".#....#.", "..####..", "........",
    ),
    "model": (
        "...#....", "...#....", ".#.#.#..", "..###...",
        "########", "..###...", ".#.#.#..", "...#....",
    ),
    "appearance": (
        "...##...", ".#....#.", "#..##..#", "#.#..#.#",
        "#.#..#.#", "#..##..#", ".#....#.", "...##...",
    ),
    "health": (
        "........", ".#....#.", "###..###", "########",
        ".######.", "..####..", "...##...", "........",
    ),
    "settings": (
        "........", ".#..###.", "###..#..", ".#......",
        "....#...", ".###.###", "..#...#.", "........",
    ),
    "folder": (
        "........", ".###....", ".#.###..", ".#....#.",
        ".#....#.", ".#....#.", ".######.", "........",
    ),
    "library": (
        ".#.#.#..", ".#.#.#..", ".#.#.#..", ".#.#.#..",
        ".#.#.#..", ".#.#.#..", ".#####..", "........",
    ),
    "knowledge": (
        "...#....", "..#.#...", ".#...#..", "#.....#.",
        ".#...#..", "..#.#...", "...#....", "........",
    ),
    "local": (
        "........", "..####..", ".######.", ".######.",
        ".######.", "..####..", "........", "........",
    ),
    "back": (
        "........", "...#....", "..#.....", ".######.",
        "..#.....", "...#....", "........", "........",
    ),
    "forward": (
        "........", "....#...", ".....#..", ".######.",
        ".....#..", "....#...", "........", "........",
    ),
    "chevron_right": (
        "........", "..#.....", "...#....", "....#...",
        "...#....", "..#.....", "........", "........",
    ),
    "chevron_down": (
        "........", "........", ".#....#.", "..#..#..",
        "...##...", "........", "........", "........",
    ),
}


@dataclass
class _IconEntry:
    image: tk.PhotoImage
    name: str
    role: str
    color: str


_REGISTRIES: WeakKeyDictionary[tk.Misc, dict[tuple[str, str], _IconEntry]] = (
    WeakKeyDictionary()
)


def _paint(image: tk.PhotoImage, name: str, color: str) -> None:
    pattern = _PATTERNS[name]
    scale = ICON_SIZE // len(pattern)
    image.blank()
    for row, pixels in enumerate(pattern):
        for column, pixel in enumerate(pixels):
            if pixel == "#":
                image.put(
                    color,
                    to=(
                        column * scale,
                        row * scale,
                        (column + 1) * scale,
                        (row + 1) * scale,
                    ),
                )


def get_icon(
    master: tk.Misc,
    name: str,
    color: str,
    *,
    role: str = "primary",
) -> tk.PhotoImage:
    """Return a cached 16×16 icon tied to the widget's top-level window."""

    if name not in _PATTERNS:
        raise ValueError(f"未知图标：{name}")
    root = master.winfo_toplevel()
    registry = _REGISTRIES.setdefault(root, {})
    key = (name, role)
    entry = registry.get(key)
    if entry is None:
        image = tk.PhotoImage(master=root, width=ICON_SIZE, height=ICON_SIZE)
        entry = _IconEntry(image=image, name=name, role=role, color=color)
        registry[key] = entry
        _paint(image, name, color)
    return entry.image


def recolor_icons(master: tk.Misc, colors: dict[str, str]) -> None:
    """Repaint existing icons in place after a live theme change."""

    registry = _REGISTRIES.get(master.winfo_toplevel(), {})
    for entry in registry.values():
        color = colors.get(entry.role, entry.color)
        if color != entry.color:
            entry.color = color
            _paint(entry.image, entry.name, color)
