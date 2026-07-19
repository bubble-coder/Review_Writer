from types import SimpleNamespace
import tkinter as tk
import unittest

from review_writer.theme import THEME_PRESETS, get_palette, normalize_hex_color
from review_writer.ui_utils import AutoHideScrollbar, MouseWheelRouter


class ThemeTests(unittest.TestCase):
    def test_builtin_presets_have_distinct_primary_colors(self) -> None:
        self.assertEqual(len(THEME_PRESETS), 8)
        self.assertEqual(len({preset.palette.primary for preset in THEME_PRESETS}), 8)
        self.assertEqual(
            {preset.id for preset in THEME_PRESETS},
            {"ocean", "forest", "violet", "amber", "rose", "aurora", "indigo", "graphite"},
        )

    def test_custom_accent_derives_a_complete_palette(self) -> None:
        palette = get_palette("custom", "#12AB67")

        self.assertEqual(palette.primary, "#12ab67")
        self.assertNotEqual(palette.primary_active, palette.primary)
        self.assertTrue(palette.primary_pale.startswith("#"))

    def test_invalid_color_uses_fallback(self) -> None:
        self.assertEqual(normalize_hex_color("blue", "#123456"), "#123456")


class MouseWheelRouterTests(unittest.TestCase):
    def test_normalizes_cross_platform_wheel_events(self) -> None:
        self.assertEqual(MouseWheelRouter.wheel_units(SimpleNamespace(delta=120)), -1)
        self.assertEqual(MouseWheelRouter.wheel_units(SimpleNamespace(delta=-240)), 2)
        self.assertEqual(MouseWheelRouter.wheel_units(SimpleNamespace(num=4, delta=0)), -1)
        self.assertEqual(MouseWheelRouter.wheel_units(SimpleNamespace(num=5, delta=0)), 1)


class AutoHideScrollbarTests(unittest.TestCase):
    def test_hides_when_content_fits_and_returns_when_needed(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tkinter display is unavailable: {error}")
        root.withdraw()
        try:
            scrollbar = AutoHideScrollbar(root, orient="vertical")
            scrollbar.grid(row=0, column=0, sticky="ns")
            scrollbar.set("0.0", "1.0")
            root.update_idletasks()
            self.assertEqual(scrollbar.winfo_manager(), "")

            scrollbar.set("0.0", "0.5")
            root.update_idletasks()
            self.assertEqual(scrollbar.winfo_manager(), "grid")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
