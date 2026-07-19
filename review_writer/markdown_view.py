"""Lightweight, dependency-free Markdown rendering for Tkinter text widgets.

The renderer intentionally covers the document structures used by Review Writer
plans.  It is a reading view, not an HTML converter: the Markdown source remains
in the editor and this module creates a selectable, read-only presentation of it.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import tkinter as tk
from typing import Any

from .theme import ThemePalette
from .ui_utils import AutoHideScrollbar


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    """A parsed display block used by the Tk renderer and offline tests."""

    kind: str
    text: str = ""
    level: int = 0


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_UNORDERED = re.compile(r"^\s*[-*+]\s+(.+)$")
_ORDERED = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
_TASK = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.+)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RULE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def parse_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    """Parse the small, predictable Markdown subset used in research plans."""

    blocks: list[MarkdownBlock] = []
    code_lines: list[str] = []
    in_code = False
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("```"):
            if in_code:
                blocks.append(MarkdownBlock("code", "\n".join(code_lines)))
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(raw)
            continue
        if not stripped:
            if not blocks or blocks[-1].kind != "blank":
                blocks.append(MarkdownBlock("blank"))
            continue
        match = _HEADING.match(raw)
        if match:
            blocks.append(MarkdownBlock("heading", match.group(2), len(match.group(1))))
            continue
        if _RULE.match(raw):
            blocks.append(MarkdownBlock("rule"))
            continue
        match = _TASK.match(raw)
        if match:
            marker = "☑" if match.group(1).lower() == "x" else "☐"
            blocks.append(MarkdownBlock("list", f"{marker} {match.group(2)}"))
            continue
        match = _UNORDERED.match(raw)
        if match:
            blocks.append(MarkdownBlock("list", f"• {match.group(1)}"))
            continue
        match = _ORDERED.match(raw)
        if match:
            blocks.append(MarkdownBlock("list", f"{match.group(1)}. {match.group(2)}"))
            continue
        match = _QUOTE.match(raw)
        if match:
            blocks.append(MarkdownBlock("quote", match.group(1)))
            continue
        if "|" in raw and index + 1 < len(lines) and _TABLE_DIVIDER.match(lines[index + 1]):
            blocks.append(MarkdownBlock("table_header", _clean_table_row(raw)))
            continue
        if _TABLE_DIVIDER.match(raw):
            continue
        if "|" in raw and blocks and blocks[-1].kind in {"table_header", "table_row"}:
            blocks.append(MarkdownBlock("table_row", _clean_table_row(raw)))
            continue
        blocks.append(MarkdownBlock("paragraph", raw.strip()))
    if in_code:
        blocks.append(MarkdownBlock("code", "\n".join(code_lines)))
    while blocks and blocks[-1].kind == "blank":
        blocks.pop()
    return blocks


def _clean_table_row(value: str) -> str:
    cells = [cell.strip() for cell in value.strip().strip("|").split("|")]
    return "   │   ".join(cells)


def configure_markdown_tags(
    widget: tk.Text,
    *,
    palette: ThemePalette,
    ui_font: str,
    mono_font: str,
) -> None:
    """Install a calm document hierarchy that follows the active application theme."""

    widget.configure(
        font=(ui_font, 11),
        foreground=palette.text,
        background=palette.surface,
        selectbackground=palette.selection,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        padx=28,
        pady=22,
        spacing1=2,
        spacing3=7,
        tabs=(32,),
    )
    widget.tag_configure("body", font=(ui_font, 11), foreground=palette.text, spacing3=8)
    widget.tag_configure("h1", font=(ui_font, 19, "bold"), foreground=palette.text, spacing1=5, spacing3=16)
    widget.tag_configure("h2", font=(ui_font, 15, "bold"), foreground=palette.primary, spacing1=14, spacing3=10)
    widget.tag_configure("h3", font=(ui_font, 12, "bold"), foreground=palette.text, spacing1=10, spacing3=7)
    widget.tag_configure("h4", font=(ui_font, 11, "bold"), foreground=palette.muted, spacing1=8, spacing3=6)
    widget.tag_configure("list", lmargin1=14, lmargin2=34, spacing1=2, spacing3=5)
    widget.tag_configure(
        "quote",
        foreground=palette.intro_foreground,
        background=palette.intro_background,
        lmargin1=14,
        lmargin2=14,
        rmargin=14,
        spacing1=7,
        spacing3=7,
    )
    widget.tag_configure(
        "code_block",
        font=(mono_font, 9),
        foreground=palette.text,
        background=palette.surface_muted,
        lmargin1=14,
        lmargin2=14,
        rmargin=14,
        spacing1=8,
        spacing3=8,
    )
    widget.tag_configure("table_header", font=(ui_font, 10, "bold"), background=palette.surface_muted, spacing1=6, spacing3=6)
    widget.tag_configure("table_row", font=(ui_font, 10), spacing1=4, spacing3=4)
    widget.tag_configure("rule", foreground=palette.border, justify="center", spacing1=6, spacing3=10)
    widget.tag_configure("strong", font=(ui_font, 11, "bold"))
    widget.tag_configure("emphasis", font=(ui_font, 11, "italic"))
    widget.tag_configure("inline_code", font=(mono_font, 9), background=palette.surface_muted, foreground=palette.intro_foreground)
    widget.tag_configure("link", foreground=palette.primary, underline=True)


_INLINE = re.compile(
    r"(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|(?<!\*)\*[^*\n]+\*(?!\*)|(?<!_)_[^_\n]+_(?!_)|\[[^]\n]+\]\([^)\n]+\))"
)


def _insert_inline(widget: tk.Text, text: str, base_tag: str) -> None:
    cursor = 0
    for match in _INLINE.finditer(text):
        if match.start() > cursor:
            widget.insert("end", text[cursor : match.start()], (base_tag,))
        token = match.group(0)
        if token.startswith("`"):
            widget.insert("end", token[1:-1], (base_tag, "inline_code"))
        elif token.startswith(("**", "__")):
            widget.insert("end", token[2:-2], (base_tag, "strong"))
        elif token.startswith(("*", "_")):
            widget.insert("end", token[1:-1], (base_tag, "emphasis"))
        else:
            label, _, target = token[1:].partition("](")
            widget.insert("end", f"{label}  ↗", (base_tag, "link"))
            # The URL is kept as a tooltip-like adjacent run so copying retains it.
            widget.insert("end", f"  {target[:-1]}", (base_tag, "inline_code"))
        cursor = match.end()
    if cursor < len(text):
        widget.insert("end", text[cursor:], (base_tag,))


def render_markdown(widget: tk.Text, markdown: str) -> None:
    """Render Markdown into an already configured Text widget as read-only content."""

    previous_state = str(widget.cget("state"))
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    for block in parse_markdown_blocks(markdown):
        if block.kind == "blank":
            widget.insert("end", "\n")
        elif block.kind == "heading":
            _insert_inline(widget, block.text, f"h{min(block.level, 4)}")
            widget.insert("end", "\n")
        elif block.kind == "list":
            _insert_inline(widget, block.text, "list")
            widget.insert("end", "\n")
        elif block.kind == "quote":
            widget.insert("end", "  │  ", ("quote",))
            _insert_inline(widget, block.text, "quote")
            widget.insert("end", "  \n", ("quote",))
        elif block.kind == "code":
            widget.insert("end", f"  {block.text}\n", ("code_block",))
        elif block.kind == "rule":
            widget.insert("end", "────────────────────────────────────────\n", ("rule",))
        elif block.kind in {"table_header", "table_row"}:
            _insert_inline(widget, block.text, block.kind)
            widget.insert("end", "\n", (block.kind,))
        else:
            _insert_inline(widget, block.text, "body")
            widget.insert("end", "\n")
    widget.configure(state="disabled" if previous_state == "disabled" else previous_state)
    widget.yview_moveto(0)


class MarkdownPreviewToggle:
    """Pair an existing Markdown source widget with a rendered reading view."""

    def __init__(
        self,
        source: tk.Text,
        container: tk.Widget,
        *,
        palette: ThemePalette,
        ui_font: str,
        mono_font: str,
        grid_options: dict[str, Any],
        editable: bool = True,
        default_mode: str = "preview",
    ) -> None:
        self.source = source
        self.container = container
        self.palette = palette
        self.ui_font = ui_font
        self.mono_font = mono_font
        self.editable = editable
        self.mode = tk.StringVar(master=source, value=default_mode)
        self._refresh_job: str | None = None

        self.preview_frame = tk.Frame(container, background=palette.surface)
        self.preview_frame.grid(**grid_options)
        self.preview_frame.columnconfigure(0, weight=1)
        self.preview_frame.rowconfigure(0, weight=1)
        self.preview = tk.Text(self.preview_frame, wrap="word", state="disabled", cursor="arrow")
        configure_markdown_tags(
            self.preview,
            palette=palette,
            ui_font=ui_font,
            mono_font=mono_font,
        )
        scrollbar = AutoHideScrollbar(
            self.preview_frame,
            orient="vertical",
            command=self.preview.yview,
        )
        self.preview.configure(yscrollcommand=scrollbar.set)
        self.preview.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.source.bind("<<Modified>>", self._on_source_modified, add="+")
        setattr(self.source, "_markdown_preview_controller", self)
        try:
            self.source.edit_modified(False)
        except tk.TclError:
            pass
        self.preview_button: tk.Button | None = None
        self.source_button: tk.Button | None = None
        self.show(default_mode)

    def mount_switcher(self, parent: tk.Widget) -> tk.Frame:
        """Create compact preview/source controls inside a heading row."""

        switcher = tk.Frame(parent, background=self.palette.surface_muted, padx=2, pady=2)
        self.preview_button = tk.Button(
            switcher,
            text="预览",
            command=lambda: self.show("preview"),
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=10,
            pady=4,
            font=(self.mono_font, 8, "bold"),
        )
        self.preview_button.pack(side="left")
        self.source_button = tk.Button(
            switcher,
            text="编辑" if self.editable else "原文",
            command=lambda: self.show("source"),
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=10,
            pady=4,
            font=(self.mono_font, 8, "bold"),
        )
        self.source_button.pack(side="left")
        self._refresh_button_style()
        return switcher

    def show(self, mode: str) -> None:
        if mode not in {"preview", "source"}:
            raise ValueError(f"未知 Markdown 视图：{mode}")
        self.mode.set(mode)
        if mode == "preview":
            self.refresh()
            self.preview_frame.tkraise()
            self.preview.focus_set()
        else:
            self.source.tkraise()
            self.source.focus_set()
        self._refresh_button_style()

    def refresh(self) -> None:
        try:
            value = self.source.get("1.0", "end-1c")
        except tk.TclError:
            return
        render_markdown(self.preview, value)

    def configure_visuals(
        self,
        *,
        palette: ThemePalette,
        ui_font: str,
        mono_font: str,
    ) -> None:
        self.palette = palette
        self.ui_font = ui_font
        self.mono_font = mono_font
        configure_markdown_tags(
            self.preview,
            palette=palette,
            ui_font=ui_font,
            mono_font=mono_font,
        )
        for button in (self.preview_button, self.source_button):
            if button is not None:
                button.configure(font=(mono_font, 8, "bold"))
        self.refresh()
        self._refresh_button_style()

    def _on_source_modified(self, _event: tk.Event[tk.Misc]) -> None:
        try:
            modified = self.source.edit_modified()
            self.source.edit_modified(False)
        except tk.TclError:
            return
        if not modified or self.mode.get() != "preview":
            return
        if self._refresh_job is not None:
            try:
                self.source.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
        self._refresh_job = self.source.after(160, self._refresh_after_idle)

    def _refresh_after_idle(self) -> None:
        self._refresh_job = None
        self.refresh()

    def _refresh_button_style(self) -> None:
        selected = (self.palette.primary, "white")
        idle = (self.palette.surface_muted, self.palette.muted)
        for button, mode in (
            (self.preview_button, "preview"),
            (self.source_button, "source"),
        ):
            if button is None:
                continue
            active = self.mode.get() == mode
            button.configure(
                background=selected[0] if active else idle[0],
                foreground=selected[1] if active else idle[1],
                activebackground=self.palette.primary_active if active else self.palette.secondary_hover,
                activeforeground="white" if active else self.palette.text,
            )
