"""Shared Tkinter interaction helpers."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any
import weakref


class AutoHideScrollbar(ttk.Scrollbar):
    """A grid-managed scrollbar that disappears when all content is visible."""

    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:
        orientation = str(kwargs.get("orient", "vertical")).lower()
        style_name = (
            "Modern.Horizontal.TScrollbar"
            if orientation.startswith("h")
            else "Modern.Vertical.TScrollbar"
        )
        kwargs.setdefault("style", style_name)
        super().__init__(master, **kwargs)
        self._needed = True
        self._uses_grid = False

    def grid(self, *args: Any, **kwargs: Any) -> None:
        self._uses_grid = True
        super().grid(*args, **kwargs)
        self.after_idle(self._sync_visibility)

    def set(self, first: str, last: str) -> None:
        self._needed = float(first) > 0.0 or float(last) < 1.0
        super().set(first, last)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        if not self._uses_grid:
            return
        try:
            if self._needed:
                tk.Grid.grid(self)
            else:
                self.grid_remove()
        except tk.TclError:
            return


class MouseWheelRouter:
    """Route wheel input to the scroll surface currently under the pointer."""

    _ATTRIBUTE = "_review_writer_mousewheel_router"

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self._regions: list[tuple[weakref.ReferenceType[tk.Widget], weakref.ReferenceType[tk.Canvas]]] = []
        root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        root.bind_all("<Button-4>", self._on_mousewheel, add="+")
        root.bind_all("<Button-5>", self._on_mousewheel, add="+")

    @classmethod
    def for_widget(cls, widget: tk.Widget) -> "MouseWheelRouter":
        root = widget.winfo_toplevel()
        router = getattr(root, cls._ATTRIBUTE, None)
        if router is None:
            router = cls(root)
            setattr(root, cls._ATTRIBUTE, router)
        return router

    def register(self, region: tk.Widget, canvas: tk.Canvas) -> None:
        self._regions.append((weakref.ref(region), weakref.ref(canvas)))

    @staticmethod
    def wheel_units(event: Any) -> int:
        """Convert Windows/macOS/Linux wheel events into Tk scroll units."""

        number = getattr(event, "num", None)
        if number == 4:
            return -1
        if number == 5:
            return 1
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return 0
        magnitude = max(1, abs(delta) // 120)
        return -magnitude if delta > 0 else magnitude

    @staticmethod
    def _is_descendant(widget: tk.Widget, ancestor: tk.Widget) -> bool:
        current: tk.Misc | None = widget
        while current is not None:
            if current == ancestor:
                return True
            current = getattr(current, "master", None)
        return False

    @staticmethod
    def _has_native_vertical_scroll(widget: tk.Widget) -> bool:
        return isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview))

    @staticmethod
    def _native_can_scroll(widget: tk.Widget, units: int) -> bool:
        try:
            first, last = widget.yview()
        except (AttributeError, tk.TclError):
            return False
        return (units < 0 and first > 0.0) or (units > 0 and last < 1.0)

    def _on_mousewheel(self, event: tk.Event[tk.Misc]) -> str | None:
        widget = event.widget
        units = self.wheel_units(event)
        if not units:
            return None
        if self._has_native_vertical_scroll(widget) and self._native_can_scroll(widget, units):
            return None

        live_regions: list[tuple[weakref.ReferenceType[tk.Widget], weakref.ReferenceType[tk.Canvas]]] = []
        matches: list[tuple[tk.Widget, tk.Canvas]] = []
        for region_ref, canvas_ref in self._regions:
            region = region_ref()
            canvas = canvas_ref()
            if region is None or canvas is None:
                continue
            try:
                if not region.winfo_exists() or not canvas.winfo_exists():
                    continue
            except tk.TclError:
                continue
            live_regions.append((region_ref, canvas_ref))
            if self._is_descendant(widget, region):
                matches.append((region, canvas))
        self._regions = live_regions
        for _region, canvas in reversed(matches):
            try:
                first, last = canvas.yview()
                can_scroll = (units < 0 and first > 0.0) or (units > 0 and last < 1.0)
                if not can_scroll:
                    continue
                canvas.yview_scroll(units, "units")
            except tk.TclError:
                continue
            return "break"
        return None


def enable_hover_wheel(region: tk.Widget, canvas: tk.Canvas) -> None:
    """Enable wheel scrolling anywhere inside ``region`` for ``canvas``."""

    MouseWheelRouter.for_widget(region).register(region, canvas)
