"""Runtime font selection for a polished and portable Windows UI."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont


UI_FONT_CANDIDATES = (
    "Segoe UI Variable Text",
    "Segoe UI Variable",
    "Segoe UI",
    "Microsoft YaHei UI",
)
MONO_FONT_CANDIDATES = (
    "Cascadia Code",
    "Cascadia Mono",
    "Consolas",
)


def installed_font_families(root: tk.Misc) -> tuple[str, ...]:
    """Return installed font families in a stable, case-insensitive order."""

    try:
        # Windows exposes vertical-writing aliases with an ``@`` prefix.  They
        # rotate CJK glyphs in ordinary horizontal Tk widgets, so never offer
        # them as application display fonts.
        families = {
            name.strip()
            for name in tkfont.families(root)
            if name.strip() and not name.lstrip().startswith("@")
        }
        return tuple(sorted(families, key=str.casefold))
    except tk.TclError:
        return ()


def resolve_fonts(
    root: tk.Misc,
    preferred_ui: str = "",
    preferred_mono: str = "",
) -> tuple[str, str]:
    """Choose the best installed UI and monospace families."""

    try:
        installed = {name.casefold(): name for name in installed_font_families(root)}
    except tk.TclError:
        return "Segoe UI", "Consolas"

    def choose(candidates: tuple[str, ...], fallback: str) -> str:
        for candidate in candidates:
            match = installed.get(candidate.casefold())
            if match:
                return match
        return fallback

    preferred_ui_match = installed.get(preferred_ui.casefold()) if preferred_ui else None
    preferred_mono_match = installed.get(preferred_mono.casefold()) if preferred_mono else None
    return (
        preferred_ui_match or choose(UI_FONT_CANDIDATES, "Microsoft YaHei UI"),
        preferred_mono_match or choose(MONO_FONT_CANDIDATES, "Consolas"),
    )


def configure_named_fonts(root: tk.Misc, ui_font: str, mono_font: str) -> None:
    """Apply the selected families to Tk widgets that use named defaults."""

    for name, family, size in (
        ("TkDefaultFont", ui_font, 10),
        ("TkTextFont", ui_font, 10),
        ("TkMenuFont", ui_font, 10),
        ("TkCaptionFont", ui_font, 10),
        ("TkSmallCaptionFont", ui_font, 9),
        ("TkFixedFont", mono_font, 10),
    ):
        try:
            tkfont.nametofont(name, root=root).configure(family=family, size=size)
        except tk.TclError:
            continue


def refont_widget_tree(
    widget: tk.Misc,
    *,
    old_ui_font: str,
    new_ui_font: str,
    old_mono_font: str,
    new_mono_font: str,
) -> None:
    """Replace explicit font families while preserving size and emphasis."""

    try:
        configuration = widget.configure()
    except tk.TclError:
        configuration = {}
    if "font" in configuration:
        try:
            actual = tkfont.Font(root=widget.winfo_toplevel(), font=widget.cget("font")).actual()
            family = str(actual.get("family", ""))
            replacement = ""
            if family.casefold() == old_ui_font.casefold():
                replacement = new_ui_font
            elif family.casefold() == old_mono_font.casefold():
                replacement = new_mono_font
            if replacement:
                styles: list[str] = []
                weight = str(actual.get("weight", "normal"))
                slant = str(actual.get("slant", "roman"))
                if weight != "normal":
                    styles.append(weight)
                if slant != "roman":
                    styles.append(slant)
                if actual.get("underline"):
                    styles.append("underline")
                if actual.get("overstrike"):
                    styles.append("overstrike")
                widget.configure(font=(replacement, int(actual.get("size", 10)), *styles))
        except (tk.TclError, ValueError, TypeError):
            pass
    try:
        children = widget.winfo_children()
    except tk.TclError:
        return
    for child in children:
        refont_widget_tree(
            child,
            old_ui_font=old_ui_font,
            new_ui_font=new_ui_font,
            old_mono_font=old_mono_font,
            new_mono_font=new_mono_font,
        )
