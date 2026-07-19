from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from review_writer.settings import SettingsStore
from review_writer.ui import ResearchPlannerApp


class WorkbenchNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tkinter display is unavailable: {error}")
        self.root.withdraw()
        self.temporary = TemporaryDirectory()
        self.settings_store = SettingsStore(Path(self.temporary.name) / "settings.json")
        self.settings_patch = patch("review_writer.ui.SettingsStore", return_value=self.settings_store)
        self.settings_patch.start()
        self.app = ResearchPlannerApp(self.root)
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            self.root.destroy()
        if hasattr(self, "settings_patch"):
            self.settings_patch.stop()
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def _fill_valid_form(self) -> None:
        self.app.topic_var.set("生成式人工智能教育应用")
        self.app.objectives_text.insert("1.0", "梳理有效应用场景")
        self.app.questions_text.insert("1.0", "学习效果如何？\n有哪些风险？")
        self.app.start_year_var.set("2020")
        self.app.end_year_var.set(str(date.today().year))
        self.app.delivery_requirements_text.insert("1.0", "中文，约 5000 字")

    def test_home_opens_with_requested_navigation_options(self) -> None:
        self.assertEqual(self.app.active_section.get(), "home")
        self.assertEqual(set(self.app.nav_buttons), {"research"})
        self.assertEqual(len(self.app.workflow_nav_buttons), 8)
        self.assertFalse(self.app.workflow_nav_expanded)
        self.assertEqual(self.app.workflow_nav_frame.winfo_manager(), "")
        self.assertEqual(set(self.app.home_actions), {"research", "settings"})
        self.assertTrue(self.app.settings_button.winfo_exists())

    def test_each_navigation_button_selects_its_section(self) -> None:
        for key, button in self.app.nav_buttons.items():
            with self.subTest(section=key):
                button.invoke()
                self.root.update_idletasks()
                self.assertEqual(self.app.active_section.get(), key)

    def test_settings_pages_are_real_views(self) -> None:
        self.assertTrue(self.app.database_settings_view.winfo_exists())
        self.assertTrue(self.app.model_settings_view.winfo_exists())
        self.assertTrue(self.app.appearance_settings_view.winfo_exists())
        self.assertEqual(self.app.model_settings_view.model_var.get(), "gpt-5.6")
        self.assertEqual(
            self.app.database_settings_view.zotero_status_var.get(),
            "尚未测试",
        )
        self.assertTrue(self.app.database_settings_view.zotero_test_button.winfo_exists())
        self.assertEqual(len(self.app.settings_notebook.tabs()), 4)
        self.assertEqual(len(self.app.database_settings_view.notebook.tabs()), 4)
        self.assertEqual(len(self.app.model_settings_view.notebook.tabs()), 2)
        self.assertEqual(self.app.settings_notebook.cget("style"), "Bookmark.TNotebook")
        self.assertEqual(
            self.app.database_settings_view.notebook.cget("style"),
            "SubBookmark.TNotebook",
        )
        style = ttk.Style(self.root)
        for style_name in ("Bookmark.TNotebook.Tab", "SubBookmark.TNotebook.Tab"):
            padding_map = style.map(style_name, "padding")
            self.assertEqual(str(padding_map[0][0]), "selected")
            self.assertNotEqual(padding_map[0][1], padding_map[1][1])
        subtab_font = tkfont.Font(
            root=self.root,
            font=style.lookup("SubBookmark.TNotebook.Tab", "font"),
        )
        self.assertEqual(subtab_font.actual("family"), self.app.ui_font)
        self.assertEqual(abs(int(subtab_font.actual("size"))), 9)
        database_header = self.app.database_settings_view.grid_slaves(row=0, column=0)[0]
        self.assertEqual(database_header.cget("background"), self.app.palette.sidebar)
        self.assertEqual(int(database_header.cget("highlightthickness")), 2)
        selected_theme = self.app.appearance_settings_view.theme_var.get()
        self.assertEqual(
            int(
                self.app.appearance_settings_view.theme_cards[selected_theme].cget(
                    "highlightthickness"
                )
            ),
            3,
        )
        self.assertEqual(
            self.app.health_settings_view.tree.cget("style"),
            "Settings.Treeview",
        )
        database_content = self.app.database_settings_view.grid_slaves(row=1, column=0)[0]
        model_content = self.app.model_settings_view.grid_slaves(row=1, column=0)[0]
        self.assertFalse(hasattr(database_content, "canvas"))
        self.assertFalse(hasattr(model_content, "canvas"))

    def test_model_catalog_selection_applies_structured_pricing(self) -> None:
        view = self.app.model_settings_view
        qwen_iid = next(
            iid for iid, entry in view.catalog_by_iid.items() if entry.provider_id == "qwen"
        )

        view.tree.selection_set(qwen_iid)
        view.tree.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()

        self.assertEqual(view.model_var.get(), "qwen3.7-plus")
        self.assertEqual(view.input_price_var.get(), "2")
        self.assertEqual(view.output_price_var.get(), "8")
        self.assertEqual(view.price_currency_var.get(), "CNY")
        self.assertEqual(view.pricing_mode_var.get(), "catalog")

    def test_model_catalog_can_filter_unknown_prices(self) -> None:
        view = self.app.model_settings_view
        view.catalog_price_filter_var.set("价格待核验")
        self.root.update_idletasks()

        visible = [view.catalog_by_iid[iid].provider_id for iid in view.tree.get_children()]
        self.assertEqual(set(visible), {"kimi", "zhipu"})

    def test_research_navigation_expands_workbench_stages_in_sidebar(self) -> None:
        style = ttk.Style(self.root)

        self.app.research_nav_button.invoke()
        self.root.update_idletasks()

        self.assertTrue(self.app.workflow_nav_expanded)
        self.assertEqual(self.app.workflow_nav_frame.winfo_manager(), "grid")
        self.assertEqual(
            [button.cget("text") for button in self.app.workflow_nav_buttons.values()],
            [
                "01  需求与计划",
                "02  综述协议",
                "03  关键词与检索式",
                "04  文献检索与全文",
                "05  结构化精读",
                "06  报告与核验",
                "07  任务与日志",
                "08  跨项目证据",
            ],
        )
        icon_sizes = {
            (
                int(self.root.tk.call("image", "width", button.cget("image"))),
                int(self.root.tk.call("image", "height", button.cget("image"))),
            )
            for button in self.app.workflow_nav_buttons.values()
        }
        self.assertEqual(icon_sizes, {(16, 16)})
        self.assertEqual(
            self.app.execution_workspace.notebook.cget("style"),
            "Workspace.TNotebook",
        )
        self.assertEqual(style.layout("Workspace.TNotebook.Tab")[0][0], "null")

    def test_sidebar_stage_selects_matching_workbench_page(self) -> None:
        self.app.current_brief = SimpleNamespace(topic="测试项目")
        self.app.project_directory = Path(self.temporary.name)

        self.app.workflow_nav_buttons[7].invoke()
        self.root.update_idletasks()

        self.assertEqual(self.app.active_section.get(), "research")
        self.assertEqual(self.app.research_stage.get(), "execution")
        self.assertEqual(self.app.execution_workspace.notebook.index("current"), 6)

    def test_runtime_selects_an_installed_modern_font(self) -> None:
        self.assertIn(
            self.app.ui_font,
            {"Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI"},
        )
        self.assertIn(self.app.mono_font, {"Cascadia Code", "Cascadia Mono", "Consolas"})
        self.assertFalse(any(name.startswith("@") for name in self.app.appearance_settings_view.font_families))

    def test_gear_opens_unified_settings_workspace(self) -> None:
        self.app.settings_button.invoke()
        self.root.update_idletasks()

        self.assertEqual(self.app.active_section.get(), "settings")
        self.app.show_settings("appearance")
        selected = self.app.settings_notebook.select()
        self.assertEqual(selected, str(self.app.settings_tabs["appearance"]))

    def test_theme_switch_is_live_and_persistent(self) -> None:
        topic_widget = self.app.topic_entry
        original_id = id(topic_widget)

        self.app.apply_theme("forest", "#2563eb")
        self.root.update_idletasks()

        self.assertEqual(id(self.app.topic_entry), original_id)
        self.assertEqual(self.app.palette.primary, "#15803d")
        self.assertEqual(self.app.sidebar.cget("background"), "#17352a")
        self.assertEqual(self.settings_store.load().appearance.theme_id, "forest")

    def test_appearance_settings_persist_font_choices(self) -> None:
        view = self.app.appearance_settings_view
        view.ui_font_var.set(self.app.ui_font)
        view.mono_font_var.set(self.app.mono_font)

        view.apply_theme()
        self.root.update_idletasks()

        loaded = self.settings_store.load().appearance
        self.assertEqual(loaded.ui_font, self.app.ui_font)
        self.assertEqual(loaded.mono_font, self.app.mono_font)
        self.assertEqual(self.app.ui_font, view.ui_font_var.get())

    def test_wheel_over_form_content_scrolls_its_page(self) -> None:
        self.app.open_research()
        self.root.update_idletasks()
        canvas = self.app.form_scroll.canvas
        canvas.yview_moveto(0.5)
        before = canvas.yview()[0]
        router = getattr(self.root, "_review_writer_mousewheel_router")

        result = router._on_mousewheel(
            SimpleNamespace(widget=self.app.form_scroll.body, delta=120)
        )

        self.assertEqual(result, "break")
        self.assertLess(canvas.yview()[0], before)

    def test_local_generation_mode_is_default_and_preserved(self) -> None:
        self.app.open_research()
        self._fill_valid_form()

        self.assertEqual(self.app.generation_mode_var.get(), "local")
        brief = self.app._brief_from_form()

        self.assertEqual(brief.generation_mode, "local")

    def test_form_values_survive_navigation_round_trip(self) -> None:
        self.app.open_research()
        self._fill_valid_form()

        self.app.show_section("database")
        self.app.show_section("model")
        self.app.show_section("research")

        self.assertEqual(self.app.topic_var.get(), "生成式人工智能教育应用")
        self.assertIn("有效应用场景", self.app.objectives_text.get("1.0", "end-1c"))
        self.assertIn("学习效果如何", self.app.questions_text.get("1.0", "end-1c"))

    def test_plan_and_stage_survive_navigation_round_trip(self) -> None:
        self.app.open_research()
        self._fill_valid_form()
        self.app.generate_plan()
        self.app.plan_text.insert("end", "\n用户手工补充")
        expected = self.app.plan_text.get("1.0", "end-1c")
        brief = self.app.current_brief

        self.app.show_section("database")
        self.app.show_section("model")
        self.app.show_section("research")

        self.assertEqual(self.app.research_stage.get(), "plan")
        self.assertEqual(self.app.plan_text.get("1.0", "end-1c"), expected)
        self.assertIs(self.app.current_brief, brief)

    def test_plan_has_readable_markdown_preview_and_edit_toggle(self) -> None:
        self.app.open_research()
        self._fill_valid_form()
        self.app.generate_plan()
        self.root.update_idletasks()

        self.assertEqual(self.app.plan_view_mode.get(), "preview")
        self.assertEqual(str(self.app.plan_preview.cget("state")), "disabled")
        self.assertNotIn("# ", self.app.plan_preview.get("1.0", "2.0"))
        self.assertTrue(self.app.plan_preview.tag_ranges("h1"))

        self.app.plan_edit_button.invoke()
        self.assertEqual(self.app.plan_view_mode.get(), "edit")
        self.app.plan_text.insert("end", "\n## 用户补充\n**重要证据**")
        self.app.plan_preview_button.invoke()

        self.assertIn("用户补充", self.app.plan_preview.get("1.0", "end-1c"))
        self.assertTrue(self.app.plan_preview.tag_ranges("h2"))
        self.assertTrue(self.app.plan_preview.tag_ranges("strong"))

    def test_navigation_widgets_are_not_recreated(self) -> None:
        original_ids = {key: id(button) for key, button in self.app.nav_buttons.items()}

        for _ in range(5):
            for key in ("research", "database", "model", "home"):
                self.app.show_section(key)

        self.assertEqual(
            {key: id(button) for key, button in self.app.nav_buttons.items()},
            original_ids,
        )

    def test_unknown_section_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知页面"):
            self.app.show_section("not-a-page")


if __name__ == "__main__":
    unittest.main()
