"""Polished Tkinter UI for the local literature-research workbench."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from . import settings_views, workflow_view
from .generators import generate_agent_plan
from .iconography import get_icon, recolor_icons
from .markdown_view import configure_markdown_tags, render_markdown
from .models import ResearchBrief
from .planner import generate_research_plan, mark_plan_confirmed
from .secret_store import SecretStore
from .settings import SettingsStore
from .settings_views import AppearanceSettingsView, DatabaseSettingsView, HealthSettingsView, ModelSettingsView
from .storage import ProjectStatus, load_project, save_project
from .theme import ThemePalette, get_palette, palette_color_map
from .typography import configure_named_fonts, refont_widget_tree, resolve_fonts
from .ui_utils import AutoHideScrollbar, enable_hover_wheel
from .workflow_view import ExecutionWorkspace


FONT = "Microsoft YaHei UI"
MONO_FONT = "Cascadia Mono"

BACKGROUND = "#f4f7fb"
SURFACE = "#ffffff"
SURFACE_MUTED = "#f8fafc"
BORDER = "#dfe6ee"
TEXT = "#172033"
MUTED = "#667085"
SUBTLE = "#98a2b3"
PRIMARY = "#2563eb"
PRIMARY_ACTIVE = "#1d4ed8"
PRIMARY_PALE = "#eaf1ff"
SUCCESS = "#16835b"
SUCCESS_ACTIVE = "#116a49"
SUCCESS_PALE = "#e8f6f0"
WARNING = "#b76e00"
WARNING_PALE = "#fff5df"
SIDEBAR = "#122033"
SIDEBAR_ACTIVE = "#203651"
SIDEBAR_TEXT = "#f8fafc"
SIDEBAR_MUTED = "#aebdce"
SECONDARY_HOVER = "#eef3f8"
QUIET_HOVER = "#dce8ff"
INTRO_BACKGROUND = "#eef4ff"
INTRO_FOREGROUND = "#1e4f9a"
INTRO_MUTED = "#53709b"
SIDEBAR_DIVIDER = "#263a50"
SIDEBAR_HEADING = "#73869a"
SIDEBAR_SUCCESS = "#5bd5a5"
SIDEBAR_FOOTER = "#7f91a5"
TEXT_AREA = "#fbfcfe"
SELECTION = "#cddfff"


def apply_fonts(ui_font: str, mono_font: str) -> None:
    """Update module font families before widgets are created."""

    global FONT, MONO_FONT
    FONT = ui_font
    MONO_FONT = mono_font


def apply_palette(palette: ThemePalette) -> None:
    """Update module color constants for the active theme."""

    global BACKGROUND, SURFACE, SURFACE_MUTED, BORDER, TEXT, MUTED, SUBTLE
    global PRIMARY, PRIMARY_ACTIVE, PRIMARY_PALE, SUCCESS, SUCCESS_ACTIVE, SUCCESS_PALE
    global WARNING, WARNING_PALE, SIDEBAR, SIDEBAR_ACTIVE, SIDEBAR_TEXT, SIDEBAR_MUTED
    global SECONDARY_HOVER, QUIET_HOVER, INTRO_BACKGROUND, INTRO_FOREGROUND, INTRO_MUTED
    global SIDEBAR_DIVIDER, SIDEBAR_HEADING, SIDEBAR_SUCCESS, SIDEBAR_FOOTER, TEXT_AREA, SELECTION
    BACKGROUND = palette.background
    SURFACE = palette.surface
    SURFACE_MUTED = palette.surface_muted
    BORDER = palette.border
    TEXT = palette.text
    MUTED = palette.muted
    SUBTLE = palette.subtle
    PRIMARY = palette.primary
    PRIMARY_ACTIVE = palette.primary_active
    PRIMARY_PALE = palette.primary_pale
    SUCCESS = palette.success
    SUCCESS_ACTIVE = palette.success_active
    SUCCESS_PALE = palette.success_pale
    WARNING = palette.warning
    WARNING_PALE = palette.warning_pale
    SIDEBAR = palette.sidebar
    SIDEBAR_ACTIVE = palette.sidebar_active
    SIDEBAR_TEXT = palette.sidebar_text
    SIDEBAR_MUTED = palette.sidebar_muted
    SECONDARY_HOVER = palette.secondary_hover
    QUIET_HOVER = palette.quiet_hover
    INTRO_BACKGROUND = palette.intro_background
    INTRO_FOREGROUND = palette.intro_foreground
    INTRO_MUTED = palette.intro_muted
    SIDEBAR_DIVIDER = palette.sidebar_divider
    SIDEBAR_HEADING = palette.sidebar_heading
    SIDEBAR_SUCCESS = palette.sidebar_success
    SIDEBAR_FOOTER = palette.sidebar_footer
    TEXT_AREA = palette.text_area
    SELECTION = palette.selection


class ScrollableFrame(tk.Frame):
    """A simple vertical scrolling surface with a public ``body`` frame."""

    def __init__(self, master: tk.Widget, *, background: str = BACKGROUND) -> None:
        super().__init__(master, background=background)
        self.canvas = tk.Canvas(
            self,
            background=background,
            borderwidth=0,
            highlightthickness=0,
        )
        self.scrollbar = AutoHideScrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = tk.Frame(self.canvas, background=background)
        self._body_window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.body.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_body_width)
        enable_hover_wheel(self, self.canvas)

    def _sync_scroll_region(self, _event: tk.Event[tk.Misc]) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_body_width(self, event: tk.Event[tk.Misc]) -> None:
        self.canvas.itemconfigure(self._body_window, width=event.width)


class ResearchPlannerApp(tk.Frame):
    """Local workbench with a home page, navigation, and a two-step planner."""

    WORKFLOW_NAVIGATION_ITEMS = (
        ("plan", "01", "需求与计划"),
        ("review", "02", "综述协议"),
        ("search", "03", "关键词与检索式"),
        ("transfer", "04", "文献检索与全文"),
        ("reading", "05", "结构化精读"),
        ("report", "06", "报告与核验"),
        ("tasks", "07", "任务与日志"),
        ("database", "08", "跨项目证据"),
    )

    def __init__(self, master: tk.Tk) -> None:
        settings_store = SettingsStore()
        settings = settings_store.load()
        palette = get_palette(settings.appearance.theme_id, settings.appearance.custom_accent)
        ui_font, mono_font = resolve_fonts(
            master,
            settings.appearance.ui_font,
            settings.appearance.mono_font,
        )
        configure_named_fonts(master, ui_font, mono_font)
        apply_palette(palette)
        settings_views.apply_palette(palette)
        workflow_view.apply_palette(palette)
        apply_fonts(ui_font, mono_font)
        settings_views.apply_fonts(ui_font, mono_font)
        workflow_view.apply_fonts(ui_font, mono_font)
        super().__init__(master, background=BACKGROUND)
        self.master = master
        self.palette = palette
        self.ui_font = ui_font
        self.mono_font = mono_font
        self.current_brief: ResearchBrief | None = None
        self.project_directory: Path | None = None
        self.plan_dirty = False
        self.plan_generation_metadata: dict[str, Any] = {}
        self._suspend_plan_events = False
        self.settings_store = settings_store
        self.settings = settings
        self.secret_store = SecretStore()

        self.active_section = tk.StringVar(value="home")
        self.research_stage = tk.StringVar(value="form")
        self.research_cta_var = tk.StringVar(value="开始制定")
        self.plan_status_var = tk.StringVar(value="待生成")
        self.plan_summary_var = tk.StringVar(value="尚未生成调研计划")
        self.plan_view_mode = tk.StringVar(value="preview")
        self._plan_preview_job: str | None = None

        self.sections: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.workflow_nav_buttons: dict[int, tk.Button] = {}
        self.workflow_nav_icon_names: dict[int, str] = {}
        self.workflow_nav_expanded = False
        self.home_actions: dict[str, ttk.Button] = {}

        self._configure_window()
        self._configure_styles()
        self.pack(fill="both", expand=True)
        self._build_shell()
        self._build_pages()
        self._bind_shortcuts()
        self.master.protocol("WM_DELETE_WINDOW", self._request_close)
        self.show_section("home")

    def _icon(self, name: str, role: str = "primary") -> tk.PhotoImage:
        colors = {
            "primary": self.palette.primary,
            "text": self.palette.text,
            "muted": self.palette.muted,
            "sidebar_text": self.palette.sidebar_text,
            "sidebar_muted": self.palette.sidebar_muted,
            "success": self.palette.sidebar_success,
            "white": "#ffffff",
        }
        return get_icon(self.master, name, colors[role], role=role)

    def _configure_window(self) -> None:
        self.master.title("文献调研与报告助手")
        self.master.geometry("1240x840")
        self.master.minsize(980, 700)
        self.master.configure(background=BACKGROUND)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.master)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=(FONT, 10), foreground=TEXT)

        style.configure(
            "Primary.TButton",
            font=(FONT, 10, "bold"),
            foreground="white",
            background=PRIMARY,
            bordercolor=PRIMARY,
            padding=(18, 10),
        )
        style.map(
            "Primary.TButton",
            background=[("active", PRIMARY_ACTIVE), ("pressed", PRIMARY_ACTIVE)],
            bordercolor=[("active", PRIMARY_ACTIVE)],
        )
        style.configure(
            "Success.TButton",
            font=(FONT, 10, "bold"),
            foreground="white",
            background=SUCCESS,
            bordercolor=SUCCESS,
            padding=(18, 10),
        )
        style.map(
            "Success.TButton",
            background=[("active", SUCCESS_ACTIVE), ("pressed", SUCCESS_ACTIVE)],
        )
        style.configure(
            "Secondary.TButton",
            font=(FONT, 10),
            foreground=TEXT,
            background=SURFACE,
            bordercolor=BORDER,
            padding=(14, 9),
        )
        style.map("Secondary.TButton", background=[("active", SECONDARY_HOVER)])
        style.configure(
            "Quiet.TButton",
            font=(FONT, 10),
            foreground=PRIMARY,
            background=PRIMARY_PALE,
            bordercolor=PRIMARY_PALE,
            padding=(14, 9),
        )
        style.map("Quiet.TButton", background=[("active", QUIET_HOVER)])
        style.configure(
            "App.TEntry",
            font=(FONT, 10),
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=(9, 8),
        )
        style.configure(
            "App.TCombobox",
            font=(FONT, 10),
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            padding=(8, 7),
        )
        style.layout(
            "Modern.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [
                            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                        ],
                    },
                )
            ],
        )
        style.layout(
            "Modern.Horizontal.TScrollbar",
            [
                (
                    "Horizontal.Scrollbar.trough",
                    {
                        "sticky": "ew",
                        "children": [
                            ("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                        ],
                    },
                )
            ],
        )
        style.configure(
            "Modern.Vertical.TScrollbar",
            width=9,
            gripcount=0,
            background=SUBTLE,
            troughcolor=BACKGROUND,
            bordercolor=BACKGROUND,
            lightcolor=SUBTLE,
            darkcolor=SUBTLE,
            relief="flat",
        )
        style.map(
            "Modern.Vertical.TScrollbar",
            background=[("pressed", PRIMARY), ("active", MUTED)],
            lightcolor=[("pressed", PRIMARY), ("active", MUTED)],
            darkcolor=[("pressed", PRIMARY), ("active", MUTED)],
        )
        style.configure(
            "Modern.Horizontal.TScrollbar",
            width=9,
            gripcount=0,
            background=SUBTLE,
            troughcolor=BACKGROUND,
            bordercolor=BACKGROUND,
            lightcolor=SUBTLE,
            darkcolor=SUBTLE,
            relief="flat",
        )
        style.map(
            "Modern.Horizontal.TScrollbar",
            background=[("pressed", PRIMARY), ("active", MUTED)],
            lightcolor=[("pressed", PRIMARY), ("active", MUTED)],
            darkcolor=[("pressed", PRIMARY), ("active", MUTED)],
        )

        for name, tab_font, font_size, tab_padding, raised_padding, margins in (
            ("Bookmark", MONO_FONT, 10, (22, 10, 22, 8), (22, 16, 22, 9), (0, 9, 0, 0)),
            ("SubBookmark", FONT, 9, (16, 8, 16, 7), (16, 13, 16, 8), (0, 7, 0, 0)),
        ):
            style.configure(
                f"{name}.TNotebook",
                background=BACKGROUND,
                bordercolor=BORDER,
                lightcolor=BORDER,
                darkcolor=BORDER,
                borderwidth=0,
                tabmargins=margins,
            )
            style.configure(
                f"{name}.TNotebook.Tab",
                font=(tab_font, font_size, "bold"),
                foreground=MUTED,
                background=SURFACE_MUTED,
                bordercolor=BORDER,
                lightcolor=BORDER,
                darkcolor=BORDER,
                relief="flat",
                padding=tab_padding,
            )
            style.map(
                f"{name}.TNotebook.Tab",
                foreground=[("selected", PRIMARY), ("active", TEXT)],
                background=[("selected", SURFACE), ("active", SECONDARY_HOVER)],
                bordercolor=[("selected", PRIMARY), ("active", BORDER)],
                lightcolor=[("selected", PRIMARY)],
                darkcolor=[("selected", PRIMARY)],
                relief=[("selected", "raised")],
                padding=[("selected", raised_padding), ("!selected", tab_padding)],
                borderwidth=[("selected", 2), ("!selected", 1)],
            )
        style.configure(
            "Workspace.TNotebook",
            background=BACKGROUND,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            borderwidth=0,
            tabmargins=0,
        )
        style.layout("Workspace.TNotebook.Tab", [])
        style.configure("Treeview", font=(FONT, 9), rowheight=28)
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"), padding=(8, 7))
        style.configure(
            "Settings.Treeview",
            font=(MONO_FONT, 9),
            rowheight=30,
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=PRIMARY,
        )
        style.configure(
            "Settings.Treeview.Heading",
            font=(MONO_FONT, 9, "bold"),
            background=SIDEBAR,
            foreground=SIDEBAR_TEXT,
            bordercolor=PRIMARY,
            padding=(9, 8),
        )
        style.map(
            "Settings.Treeview",
            background=[("selected", PRIMARY)],
            foreground=[("selected", "white")],
        )
        style.configure(
            "Ops.Treeview",
            font=(MONO_FONT, 9),
            rowheight=28,
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=PRIMARY,
        )
        style.configure(
            "Ops.Treeview.Heading",
            font=(MONO_FONT, 9, "bold"),
            background=SIDEBAR,
            foreground=SIDEBAR_TEXT,
            bordercolor=PRIMARY,
            padding=(8, 8),
        )
        style.map(
            "Ops.Treeview",
            background=[("selected", PRIMARY)],
            foreground=[("selected", "white")],
        )
        style.configure(
            "Ops.TButton",
            font=(MONO_FONT, 9, "bold"),
            foreground=SIDEBAR_TEXT,
            background=SIDEBAR_ACTIVE,
            bordercolor=PRIMARY,
            padding=(11, 8),
        )
        style.map(
            "Ops.TButton",
            background=[("pressed", PRIMARY_ACTIVE), ("active", SIDEBAR_DIVIDER)],
            foreground=[("disabled", SIDEBAR_MUTED)],
        )
        style.configure(
            "OpsAccent.TButton",
            font=(MONO_FONT, 9, "bold"),
            foreground="white",
            background=PRIMARY,
            bordercolor=PRIMARY,
            padding=(11, 8),
        )
        style.map(
            "OpsAccent.TButton",
            background=[("pressed", PRIMARY_ACTIVE), ("active", PRIMARY_ACTIVE)],
        )

    def _build_shell(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.sidebar = tk.Frame(self, background=SIDEBAR, width=238)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        self.sidebar.rowconfigure(4, weight=1)

        brand = tk.Button(
            self.sidebar,
            text="REVIEW WRITER\n文献调研工作台",
            image=self._icon("app", "sidebar_text"),
            compound="left",
            command=lambda: self.show_section("home"),
            background=SIDEBAR,
            activebackground=SIDEBAR_ACTIVE,
            foreground=SIDEBAR_TEXT,
            activeforeground=SIDEBAR_TEXT,
            font=(FONT, 12, "bold"),
            justify="left",
            anchor="w",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=24,
            pady=24,
        )
        brand.grid(row=0, column=0, sticky="ew")
        self.home_button = brand

        tk.Label(
            self.sidebar,
            text="功能",
            image=self._icon("grid", "sidebar_muted"),
            compound="left",
            background=SIDEBAR,
            foreground=SIDEBAR_HEADING,
            font=(FONT, 9, "bold"),
            anchor="w",
            padx=24,
        ).grid(row=1, column=0, sticky="ew", pady=(10, 8))

        nav_container = tk.Frame(self.sidebar, background=SIDEBAR)
        nav_container.grid(row=2, column=0, sticky="new")
        nav_container.columnconfigure(0, weight=1)
        self.research_nav_button = tk.Button(
            nav_container,
            text="调研计划",
            image=self._icon("chevron_right", "sidebar_muted"),
            compound="left",
            command=self.toggle_workflow_navigation,
            background=SIDEBAR,
            activebackground=SIDEBAR_ACTIVE,
            foreground=SIDEBAR_MUTED,
            activeforeground=SIDEBAR_TEXT,
            font=(FONT, 11, "bold"),
            justify="left",
            anchor="w",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=22,
            pady=12,
        )
        self.research_nav_button.grid(row=0, column=0, sticky="ew", padx=10, pady=2)
        self.nav_buttons["research"] = self.research_nav_button

        self.workflow_nav_frame = tk.Frame(nav_container, background=SIDEBAR)
        self.workflow_nav_frame.grid(row=1, column=0, sticky="ew", padx=(18, 10), pady=(0, 5))
        self.workflow_nav_frame.columnconfigure(0, weight=1)
        for index, (icon, number, label) in enumerate(self.WORKFLOW_NAVIGATION_ITEMS):
            button = tk.Button(
                self.workflow_nav_frame,
                text=f"{number}  {label}",
                image=self._icon(icon, "sidebar_muted"),
                compound="left",
                command=lambda stage=index: self.open_workflow_stage(stage),
                background=SIDEBAR,
                activebackground=SIDEBAR_ACTIVE,
                foreground=SIDEBAR_MUTED,
                activeforeground=SIDEBAR_TEXT,
                font=(FONT, 9),
                justify="left",
                anchor="w",
                relief="flat",
                borderwidth=0,
                cursor="hand2",
                padx=12,
                pady=7,
            )
            button.grid(row=index, column=0, sticky="ew", pady=1)
            self.workflow_nav_buttons[index] = button
            self.workflow_nav_icon_names[index] = icon
        self.workflow_nav_frame.grid_remove()

        separator = tk.Frame(self.sidebar, background=SIDEBAR_DIVIDER, height=1)
        separator.grid(row=3, column=0, sticky="ew", padx=22, pady=(18, 0))

        self.settings_button = tk.Button(
            self.sidebar,
            text="设置",
            image=self._icon("settings", "sidebar_muted"),
            compound="left",
            command=self.show_settings,
            background=SIDEBAR,
            activebackground=SIDEBAR_ACTIVE,
            foreground=SIDEBAR_MUTED,
            activeforeground=SIDEBAR_TEXT,
            font=(FONT, 11, "bold"),
            anchor="w",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=22,
            pady=12,
        )
        self.settings_button.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 18))

        sidebar_footer = tk.Frame(self.sidebar, background=SIDEBAR)
        sidebar_footer.grid(row=5, column=0, sticky="sew", padx=24, pady=(8, 10))
        tk.Label(
            sidebar_footer,
            text="本地运行",
            image=self._icon("local", "success"),
            compound="left",
            background=SIDEBAR,
            foreground=SIDEBAR_SUCCESS,
            font=(FONT, 9, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            sidebar_footer,
            text="数据仅保存在本机\nv0.5 · 可恢复任务与系统综述",
            background=SIDEBAR,
            foreground=SIDEBAR_FOOTER,
            font=(FONT, 8),
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(6, 0))

        self.content_host = tk.Frame(self, background=BACKGROUND)
        self.content_host.grid(row=0, column=1, sticky="nsew")
        self.content_host.columnconfigure(0, weight=1)
        self.content_host.rowconfigure(0, weight=1)

    def _build_pages(self) -> None:
        for key in ("home", "research", "settings"):
            page = tk.Frame(self.content_host, background=BACKGROUND)
            page.grid(row=0, column=0, sticky="nsew")
            self.sections[key] = page

        self._build_home_page(self.sections["home"])
        self._build_research_section(self.sections["research"])
        settings_page = self.sections["settings"]
        settings_page.columnconfigure(0, weight=1)
        settings_page.rowconfigure(0, weight=1)
        self.settings_notebook = ttk.Notebook(settings_page, style="Bookmark.TNotebook")
        self.settings_notebook.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        self.settings_tabs: dict[str, tk.Frame] = {}
        for key, icon, label in (
            ("database", "database", "数据库设置"),
            ("model", "model", "大模型设置"),
            ("appearance", "appearance", "界面设置"),
            ("health", "health", "健康检查"),
        ):
            tab = tk.Frame(self.settings_notebook, background=BACKGROUND)
            self.settings_notebook.add(
                tab,
                text=label,
                image=self._icon(icon),
                compound="left",
            )
            self.settings_tabs[key] = tab
        self.database_settings_view = DatabaseSettingsView(
            self.settings_tabs["database"],
            settings_store=self.settings_store,
            settings=self.settings,
            secret_store=self.secret_store,
            on_back=lambda: self.show_section("home"),
        )
        self.database_settings_view.pack(fill="both", expand=True)
        self.model_settings_view = ModelSettingsView(
            self.settings_tabs["model"],
            settings_store=self.settings_store,
            settings=self.settings,
            secret_store=self.secret_store,
            on_back=lambda: self.show_section("home"),
            on_saved=self._refresh_agent_mode_hint,
        )
        self.model_settings_view.pack(fill="both", expand=True)
        self.appearance_settings_view = AppearanceSettingsView(
            self.settings_tabs["appearance"],
            settings_store=self.settings_store,
            settings=self.settings,
            on_back=lambda: self.show_section("home"),
            on_applied=self.apply_theme,
        )
        self.appearance_settings_view.pack(fill="both", expand=True)
        self.health_settings_view = HealthSettingsView(
            self.settings_tabs["health"],
            settings_store=self.settings_store,
            settings=self.settings,
            secret_store=self.secret_store,
            on_back=lambda: self.show_section("home"),
        )
        self.health_settings_view.pack(fill="both", expand=True)

    def _page_header(
        self,
        parent: tk.Widget,
        *,
        eyebrow: str,
        title: str,
        description: str,
        badge: str | None = None,
    ) -> tk.Frame:
        header = tk.Frame(parent, background=BACKGROUND)
        header.columnconfigure(0, weight=1)
        text_frame = tk.Frame(header, background=BACKGROUND)
        text_frame.grid(row=0, column=0, sticky="w")
        tk.Label(
            text_frame,
            text=eyebrow,
            background=BACKGROUND,
            foreground=PRIMARY,
            font=(FONT, 9, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            text_frame,
            text=title,
            background=BACKGROUND,
            foreground=TEXT,
            font=(FONT, 21, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(5, 3))
        tk.Label(
            text_frame,
            text=description,
            background=BACKGROUND,
            foreground=MUTED,
            font=(FONT, 10),
            anchor="w",
        ).pack(anchor="w")
        if badge:
            tk.Label(
                header,
                text=badge,
                background=SUCCESS_PALE,
                foreground=SUCCESS,
                font=(FONT, 9, "bold"),
                padx=12,
                pady=7,
            ).grid(row=0, column=1, sticky="ne", padx=(20, 0))
        return header

    @staticmethod
    def _card(parent: tk.Widget, *, background: str = SURFACE) -> tuple[tk.Frame, tk.Frame]:
        outer = tk.Frame(
            parent,
            background=background,
            highlightbackground=BORDER,
            highlightthickness=1,
            borderwidth=0,
        )
        inner = tk.Frame(outer, background=background)
        inner.pack(fill="both", expand=True, padx=24, pady=22)
        return outer, inner

    def _build_home_page(self, page: tk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        header = self._page_header(
            page,
            eyebrow="工作台首页",
            title="从一份清晰的调研计划开始",
            description="先定义问题和交付要求，再逐步接入检索、精读、写作与引用核验。",
            badge="本地模式",
        )
        header.grid(row=0, column=0, sticky="ew", padx=40, pady=(34, 22))

        body = tk.Frame(page, background=BACKGROUND)
        body.grid(row=1, column=0, sticky="nsew", padx=40, pady=(0, 34))
        body.columnconfigure((0, 1), weight=1, uniform="home-card")
        body.rowconfigure(1, weight=1)

        intro_outer, intro = self._card(body, background=INTRO_BACKGROUND)
        intro_outer.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        intro.columnconfigure(0, weight=1)
        tk.Label(
            intro,
            text="当前版本已可完成：需求收集 → 计划生成 → 人工修订 → 本地确认",
            background=INTRO_BACKGROUND,
            foreground=INTRO_FOREGROUND,
            font=(FONT, 11, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            intro,
            text="数据库支持只读探测与预览；大模型可用于 Agent 生成调研计划。",
            background=INTRO_BACKGROUND,
            foreground=INTRO_MUTED,
            font=(FONT, 9),
            anchor="e",
        ).grid(row=0, column=1, sticky="e", padx=(20, 0))

        cards = (
            (
                "research",
                "plan",
                "PLAN",
                "调研计划",
                "填写调研主题、目标与核心问题，生成一份可编辑并可确认的 Markdown 计划。",
                "可用",
                ("需求表单", "计划修订", "本地保存"),
                self.open_research,
                "Primary.TButton",
            ),
            (
                "settings",
                "settings",
                "SET",
                "统一设置",
                "在同一个设置工作区中配置数据库、大模型与界面配色，各项设置按标签页清晰组织。",
                "可配置",
                ("数据库设置", "大模型设置", "界面设置"),
                self.show_settings,
                "Secondary.TButton",
            ),
        )
        for column, card_data in enumerate(cards):
            self._build_home_feature_card(
                body,
                column=column,
                last_column=len(cards) - 1,
                data=card_data,
            )

    def _build_home_feature_card(
        self,
        parent: tk.Frame,
        *,
        column: int,
        last_column: int,
        data: tuple[
            str,
            str,
            str,
            str,
            str,
            str,
            tuple[str, str, str],
            Callable[[], None],
            str,
        ],
    ) -> None:
        key, icon, code, title, description, status, features, command, button_style = data
        outer, card = self._card(parent)
        outer.grid(
            row=1,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 8, 0 if column == last_column else 8),
        )
        card.columnconfigure(0, weight=1)
        card.rowconfigure(5, weight=1)

        code_background = PRIMARY_PALE if key == "research" else SURFACE_MUTED
        code_foreground = PRIMARY if key == "research" else MUTED
        tk.Label(
            card,
            text=code,
            image=self._icon(icon, "primary" if key == "research" else "muted"),
            compound="left",
            background=code_background,
            foreground=code_foreground,
            font=(FONT, 10, "bold"),
            padx=10,
            pady=7,
        ).grid(row=0, column=0, sticky="w")
        badge_background = SUCCESS_PALE if status == "可用" else WARNING_PALE
        badge_foreground = SUCCESS if status == "可用" else WARNING
        tk.Label(
            card,
            text=status,
            background=badge_background,
            foreground=badge_foreground,
            font=(FONT, 8, "bold"),
            padx=9,
            pady=5,
        ).grid(row=0, column=1, sticky="e")
        tk.Label(
            card,
            text=title,
            background=SURFACE,
            foreground=TEXT,
            font=(FONT, 14, "bold"),
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(18, 8))
        tk.Label(
            card,
            text=description,
            background=SURFACE,
            foreground=MUTED,
            font=(FONT, 9),
            justify="left",
            anchor="nw",
            wraplength=240,
        ).grid(row=2, column=0, columnspan=2, sticky="new")
        feature_frame = tk.Frame(card, background=SURFACE)
        feature_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(17, 0))
        for feature in features:
            tk.Label(
                feature_frame,
                text=f"—  {feature}",
                background=SURFACE,
                foreground=TEXT,
                font=(FONT, 9),
                anchor="w",
            ).pack(fill="x", pady=2)
        button_text = self.research_cta_var if key == "research" else None
        action = ttk.Button(
            card,
            text="打开设置" if key == "settings" else None,
            textvariable=button_text,
            image=self._icon("settings" if key == "settings" else "forward", "primary" if key == "settings" else "white"),
            compound="right",
            command=command,
            style=button_style,
        )
        action.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(24, 0))
        self.home_actions[key] = action

    def _build_research_section(self, section: tk.Frame) -> None:
        section.columnconfigure(0, weight=1)
        section.rowconfigure(0, weight=1)
        self.research_form_page = tk.Frame(section, background=BACKGROUND)
        self.research_plan_page = tk.Frame(section, background=BACKGROUND)
        self.research_execution_page = tk.Frame(section, background=BACKGROUND)
        for page in (
            self.research_form_page,
            self.research_plan_page,
            self.research_execution_page,
        ):
            page.grid(row=0, column=0, sticky="nsew")
        self._build_form_page(self.research_form_page)
        self._build_plan_page(self.research_plan_page)
        self.execution_workspace = ExecutionWorkspace(
            self.research_execution_page,
            settings_store=self.settings_store,
            settings=self.settings,
            secret_store=self.secret_store,
            on_back=self.show_plan,
            on_database_settings=lambda: self.show_settings("database"),
            on_model_settings=lambda: self.show_settings("model"),
        )
        self.execution_workspace.pack(fill="both", expand=True)
        self.execution_workspace.notebook.bind(
            "<<NotebookTabChanged>>",
            self._on_execution_tab_changed,
            add="+",
        )
        self.research_form_page.tkraise()

    def _build_progress(self, parent: tk.Widget, *, active_step: int) -> tk.Frame:
        outer, content = self._card(parent)
        content.columnconfigure(2, weight=1)
        steps = (
            ("01.1", "填写调研需求", "明确问题、范围和交付要求"),
            ("01.2", "审阅并确认计划", "人工修改后保存最终版本"),
        )
        for index, (number, title, helper) in enumerate(steps):
            active = index < active_step
            base_column = 0 if index == 0 else 3
            circle = tk.Label(
                content,
                text=str(number),
                background=PRIMARY if active else BORDER,
                foreground="white" if active else MUTED,
                font=(FONT, 9, "bold"),
                width=5,
                height=1,
                padx=1,
                pady=5,
            )
            circle.grid(row=0, column=base_column, rowspan=2, sticky="w")
            text_frame = tk.Frame(content, background=SURFACE)
            text_frame.grid(
                row=0,
                column=base_column + 1,
                rowspan=2,
                sticky="w",
                padx=(10, 20 if index == 0 else 0),
            )
            tk.Label(
                text_frame,
                text=title,
                background=SURFACE,
                foreground=TEXT if active else MUTED,
                font=(FONT, 10, "bold"),
                anchor="w",
            ).pack(anchor="w")
            tk.Label(
                text_frame,
                text=helper,
                background=SURFACE,
                foreground=SUBTLE,
                font=(FONT, 8),
                anchor="w",
            ).pack(anchor="w", pady=(2, 0))
            if index == 0:
                line = tk.Frame(
                    content,
                    background=PRIMARY if active_step >= 2 else BORDER,
                    height=2,
                )
                line.grid(row=0, column=2, sticky="ew", padx=(10, 16))
        return outer

    def _build_form_page(self, page: tk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)

        header = self._page_header(
            page,
            eyebrow="调研计划 / 需求表单",
            title="定义这次调研要解决的问题",
            description="所有字段仅在本机处理；带 * 的内容用于生成第一版调研计划。",
            badge="本地模板生成",
        )
        header.grid(row=0, column=0, sticky="ew", padx=38, pady=(28, 16))
        progress = self._build_progress(page, active_step=1)
        progress.grid(row=1, column=0, sticky="ew", padx=38, pady=(0, 14))

        scroll = ScrollableFrame(page)
        scroll.grid(row=2, column=0, sticky="nsew", padx=38)
        self.form_scroll = scroll
        body = scroll.body
        body.columnconfigure(0, weight=1)

        task_outer, task = self._card(body)
        task_outer.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        task.columnconfigure(0, weight=1)
        task.columnconfigure(1, weight=1)
        self._section_heading(
            task,
            row=0,
            title="研究任务",
            description="先说明主题和目标，再把核心问题按优先顺序逐行列出。",
        )

        self._field_label(task, row=1, text="调研主题 *")
        self.topic_var = tk.StringVar()
        self.topic_entry = ttk.Entry(task, textvariable=self.topic_var, style="App.TEntry")
        self.topic_entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 17))

        self._field_label(
            task,
            row=3,
            text="调研目标 *",
            hint="说明这次调研要支持什么判断、决策或写作任务。",
        )
        self.objectives_text = self._make_text(task, height=4)
        self.objectives_text.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 17))

        self._field_label(
            task,
            row=6,
            text="核心问题 *",
            hint="每行一个问题；它们将成为筛选文献和组织报告的主线。",
        )
        self.questions_text = self._make_text(task, height=5)
        self.questions_text.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        delivery_outer, delivery = self._card(body)
        delivery_outer.grid(row=1, column=0, sticky="ew", pady=(0, 20))
        delivery.columnconfigure(0, weight=1)
        delivery.columnconfigure(1, weight=1)
        self._section_heading(
            delivery,
            row=0,
            title="时间、交付与范围",
            description="限定检索年份，并说明最终报告应采用的形式。",
        )

        self._field_label(
            delivery,
            row=1,
            text="文献起始年份 *",
            column=0,
            columnspan=1,
        )
        self._field_label(
            delivery,
            row=1,
            text="文献结束年份 *",
            column=1,
            columnspan=1,
        )
        current_year = date.today().year
        self.start_year_var = tk.StringVar(value=str(current_year - 10))
        self.end_year_var = tk.StringVar(value=str(current_year))
        ttk.Entry(delivery, textvariable=self.start_year_var, style="App.TEntry").grid(
            row=2, column=0, sticky="ew", padx=(0, 8), pady=(6, 17)
        )
        ttk.Entry(delivery, textvariable=self.end_year_var, style="App.TEntry").grid(
            row=2, column=1, sticky="ew", padx=(8, 0), pady=(6, 17)
        )

        self._field_label(delivery, row=3, text="报告交付形式 *")
        self.delivery_format_var = tk.StringVar(value="Markdown 综合报告")
        ttk.Combobox(
            delivery,
            textvariable=self.delivery_format_var,
            values=(
                "Markdown 综合报告",
                "Markdown 决策简报",
                "系统综述框架",
                "研究现状与趋势报告",
                "自定义交付形式",
            ),
            state="normal",
            style="App.TCombobox",
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 17))

        self._field_label(
            delivery,
            row=5,
            text="报告交付要求 *",
            hint="例如语言、篇幅、结构、目标读者、截止时间和必须包含的表格。",
        )
        self.delivery_requirements_text = self._make_text(delivery, height=4)
        self.delivery_requirements_text.grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(6, 17)
        )

        self._field_label(
            delivery,
            row=8,
            text="范围或排除条件（可选）",
            hint="例如仅纳入某类人群、地区、研究设计或应用场景。",
        )
        self.scope_text = self._make_text(delivery, height=3)
        self.scope_text.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(6, 17))

        separator = tk.Frame(delivery, background=BORDER, height=1)
        separator.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(2, 17))
        self._field_label(
            delivery,
            row=12,
            text="计划生成方式 *",
            hint="本地模板不联网；大模型 Agent 使用“大模型设置”中的默认模型。",
        )
        self.generation_mode_var = tk.StringVar(value="local")
        generation = tk.Frame(delivery, background=SURFACE)
        generation.grid(row=14, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        generation.columnconfigure((0, 1), weight=1, uniform="generation-mode")
        local_option = tk.Radiobutton(
            generation,
            text="本地规则模板\n离线、快速、结果可复现",
            value="local",
            variable=self.generation_mode_var,
            background=SURFACE_MUTED,
            activebackground=SURFACE_MUTED,
            foreground=TEXT,
            font=(FONT, 9, "bold"),
            justify="left",
            anchor="w",
            padx=12,
            pady=10,
            indicatoron=True,
            relief="flat",
        )
        local_option.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        agent_option = tk.Radiobutton(
            generation,
            text="大模型 Agent\n结构化生成，调用可能产生费用",
            value="agent",
            variable=self.generation_mode_var,
            background=PRIMARY_PALE,
            activebackground=PRIMARY_PALE,
            foreground=TEXT,
            font=(FONT, 9, "bold"),
            justify="left",
            anchor="w",
            padx=12,
            pady=10,
            indicatoron=True,
            relief="flat",
        )
        agent_option.grid(row=0, column=1, sticky="ew", padx=(7, 0))
        self.agent_mode_hint_var = tk.StringVar()
        hint_row = tk.Frame(delivery, background=SURFACE)
        hint_row.grid(row=15, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        tk.Label(
            hint_row,
            textvariable=self.agent_mode_hint_var,
            background=SURFACE,
            foreground=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(side="left")
        ttk.Button(
            hint_row,
            text="打开大模型设置",
            image=self._icon("model"),
            compound="left",
            style="Secondary.TButton",
            command=lambda: self.show_settings("model"),
        ).pack(side="right")
        self._refresh_agent_mode_hint()

        footer = tk.Frame(page, background=BACKGROUND)
        footer.grid(row=3, column=0, sticky="ew", padx=38, pady=(14, 24))
        tk.Label(
            footer,
            text="快捷键：Ctrl + Enter 生成计划",
            background=BACKGROUND,
            foreground=SUBTLE,
            font=(FONT, 8),
        ).pack(side="left")
        ttk.Button(
            footer,
            text="打开已有项目",
            image=self._icon("folder"),
            compound="left",
            style="Secondary.TButton",
            command=self.open_existing_project,
        ).pack(side="left", padx=(12, 0))
        self.generate_button = ttk.Button(
            footer,
            text="生成调研计划",
            image=self._icon("forward", "white"),
            compound="right",
            style="Primary.TButton",
            command=self.generate_plan,
        )
        self.generate_button.pack(side="right")

    def _build_plan_page(self, page: tk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)

        header = self._page_header(
            page,
            eyebrow="调研计划 / 审阅确认",
            title="检查、修改并确认调研计划",
            description="编辑区保存的是 Markdown；确认时以当前编辑内容为准。",
            badge="可人工修改",
        )
        header.grid(row=0, column=0, sticky="ew", padx=38, pady=(28, 16))
        progress = self._build_progress(page, active_step=2)
        progress.grid(row=1, column=0, sticky="ew", padx=38, pady=(0, 14))

        editor_outer, editor = self._card(page)
        editor_outer.grid(row=2, column=0, sticky="nsew", padx=38)
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(2, weight=1)

        summary = tk.Frame(editor, background=SURFACE_MUTED)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        summary.columnconfigure(0, weight=1)
        tk.Label(
            summary,
            textvariable=self.plan_summary_var,
            background=SURFACE_MUTED,
            foreground=MUTED,
            font=(FONT, 9),
            anchor="w",
            padx=12,
            pady=10,
        ).grid(row=0, column=0, sticky="ew")

        toolbar = tk.Frame(editor, background=SURFACE)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(0, weight=1)
        tk.Label(
            toolbar,
            text="research_plan.md",
            background=SURFACE,
            foreground=TEXT,
            font=(MONO_FONT, 9, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        view_switcher = tk.Frame(toolbar, background=SURFACE_MUTED, padx=2, pady=2)
        view_switcher.grid(row=0, column=1, sticky="e", padx=(12, 12))
        self.plan_preview_button = tk.Button(
            view_switcher,
            text="阅读预览",
            command=lambda: self._show_plan_view("preview"),
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=12,
            pady=5,
            font=(FONT, 8, "bold"),
        )
        self.plan_preview_button.pack(side="left")
        self.plan_edit_button = tk.Button(
            view_switcher,
            text="编辑原文",
            command=lambda: self._show_plan_view("edit"),
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=12,
            pady=5,
            font=(FONT, 8, "bold"),
        )
        self.plan_edit_button.pack(side="left")
        tk.Label(
            toolbar,
            textvariable=self.plan_status_var,
            background=WARNING_PALE,
            foreground=WARNING,
            font=(FONT, 8, "bold"),
            padx=10,
            pady=5,
        ).grid(row=0, column=2, sticky="e")

        text_frame = tk.Frame(editor, background=SURFACE)
        text_frame.grid(row=2, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.plan_editor_frame = tk.Frame(text_frame, background=SURFACE)
        self.plan_editor_frame.grid(row=0, column=0, sticky="nsew")
        self.plan_editor_frame.columnconfigure(0, weight=1)
        self.plan_editor_frame.rowconfigure(0, weight=1)
        self.plan_text = tk.Text(
            self.plan_editor_frame,
            wrap="word",
            undo=True,
            font=(MONO_FONT, 10),
            foreground=TEXT,
            background=TEXT_AREA,
            insertbackground=TEXT,
            selectbackground=SELECTION,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=PRIMARY,
            padx=15,
            pady=13,
        )
        scrollbar = AutoHideScrollbar(self.plan_editor_frame, orient="vertical", command=self.plan_text.yview)
        self.plan_text.configure(yscrollcommand=scrollbar.set)
        self.plan_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.plan_text.bind("<<Modified>>", self._on_plan_modified)

        self.plan_preview_frame = tk.Frame(text_frame, background=SURFACE)
        self.plan_preview_frame.grid(row=0, column=0, sticky="nsew")
        self.plan_preview_frame.columnconfigure(0, weight=1)
        self.plan_preview_frame.rowconfigure(0, weight=1)
        self.plan_preview = tk.Text(self.plan_preview_frame, wrap="word", state="disabled", cursor="arrow")
        configure_markdown_tags(
            self.plan_preview,
            palette=self.palette,
            ui_font=FONT,
            mono_font=MONO_FONT,
        )
        preview_scrollbar = AutoHideScrollbar(
            self.plan_preview_frame,
            orient="vertical",
            command=self.plan_preview.yview,
        )
        self.plan_preview.configure(yscrollcommand=preview_scrollbar.set)
        self.plan_preview.grid(row=0, column=0, sticky="nsew")
        preview_scrollbar.grid(row=0, column=1, sticky="ns")
        self._show_plan_view("preview")

        footer = tk.Frame(page, background=BACKGROUND)
        footer.grid(row=3, column=0, sticky="ew", padx=38, pady=(14, 24))
        ttk.Button(
            footer,
            text="返回修改需求",
            image=self._icon("back"),
            compound="left",
            style="Secondary.TButton",
            command=self.show_form,
        ).pack(side="left")
        ttk.Button(
            footer,
            text="重新生成",
            style="Secondary.TButton",
            command=self.regenerate_plan,
        ).pack(side="left", padx=(9, 0))
        ttk.Button(
            footer,
            text="确认并进入执行工作台",
            image=self._icon("forward", "white"),
            compound="right",
            style="Success.TButton",
            command=self.confirm_plan,
        ).pack(side="right")
        ttk.Button(
            footer,
            text="保存草稿",
            style="Quiet.TButton",
            command=self.save_draft,
        ).pack(side="right", padx=(0, 9))

    @staticmethod
    def _section_heading(
        parent: tk.Widget,
        *,
        row: int,
        title: str,
        description: str,
    ) -> None:
        heading = tk.Frame(parent, background=SURFACE)
        heading.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        tk.Label(
            heading,
            text=title,
            background=SURFACE,
            foreground=TEXT,
            font=(FONT, 12, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            heading,
            text=description,
            background=SURFACE,
            foreground=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

    @staticmethod
    def _field_label(
        parent: tk.Widget,
        *,
        row: int,
        text: str,
        hint: str | None = None,
        column: int = 0,
        columnspan: int = 2,
    ) -> None:
        tk.Label(
            parent,
            text=text,
            background=SURFACE,
            foreground=TEXT,
            font=(FONT, 9, "bold"),
            anchor="w",
        ).grid(row=row, column=column, columnspan=columnspan, sticky="w")
        if hint:
            tk.Label(
                parent,
                text=hint,
                background=SURFACE,
                foreground=SUBTLE,
                font=(FONT, 8),
                anchor="w",
            ).grid(row=row + 1, column=column, columnspan=columnspan, sticky="w", pady=(3, 0))

    @staticmethod
    def _make_text(parent: tk.Widget, *, height: int) -> tk.Text:
        return tk.Text(
            parent,
            height=height,
            wrap="word",
            font=(FONT, 10),
            foreground=TEXT,
            background=SURFACE,
            insertbackground=TEXT,
            selectbackground=SELECTION,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=PRIMARY,
            padx=10,
            pady=8,
            undo=True,
        )

    @staticmethod
    def _text_value(widget: tk.Text) -> str:
        return widget.get("1.0", "end-1c")

    def _refresh_agent_mode_hint(self) -> None:
        model = self.settings.model
        api_key = self.secret_store.get("model.api_key")
        if model.is_configured(api_key):
            self.agent_mode_hint_var.set(
                f"Agent 已配置：{model.provider_name} / {model.model}"
            )
        else:
            self.agent_mode_hint_var.set("Agent 尚未配置完整，选择后会引导到大模型设置。")

    def _brief_from_form(self) -> ResearchBrief | None:
        try:
            return ResearchBrief.from_form(
                topic=self.topic_var.get(),
                objectives=self._text_value(self.objectives_text),
                core_questions=self._text_value(self.questions_text),
                start_year=self.start_year_var.get(),
                end_year=self.end_year_var.get(),
                delivery_format=self.delivery_format_var.get(),
                delivery_requirements=self._text_value(self.delivery_requirements_text),
                scope_notes=self._text_value(self.scope_text),
                generation_mode=self.generation_mode_var.get(),
            )
        except ValueError as error:
            messagebox.showwarning("请完善调研需求", str(error), parent=self.master)
            return None

    def generate_plan(self) -> None:
        brief = self._brief_from_form()
        if brief is None:
            return
        self.project_directory = None
        self._start_plan_generation(brief, regenerated=False)

    def regenerate_plan(self) -> None:
        brief = self._brief_from_form()
        if brief is None:
            self.show_form()
            return
        if self._text_value(self.plan_text).strip():
            should_replace = messagebox.askyesno(
                "重新生成计划",
                "重新生成会替换编辑区中的当前内容，是否继续？",
                parent=self.master,
            )
            if not should_replace:
                return
        self._start_plan_generation(brief, regenerated=True)

    def _start_plan_generation(self, brief: ResearchBrief, *, regenerated: bool) -> None:
        if brief.generation_mode == "local":
            from .provenance import content_hash, now_iso

            self.plan_generation_metadata = {
                "mode": "local",
                "prompt_version": "review-writer:local-plan:v1",
                "input_hash": content_hash(brief.to_dict()),
                "generated_at": now_iso(),
            }
            self._finish_plan_generation(
                brief,
                generate_research_plan(brief),
                regenerated=regenerated,
            )
            return

        model = self.settings.model
        api_key = self.secret_store.get("model.api_key")
        if not model.is_configured(api_key):
            messagebox.showwarning(
                "大模型 Agent 尚未配置",
                "请先在“大模型设置”中填写 API 地址、模型名称和 API Key，并测试连接。",
                parent=self.master,
            )
            self.show_settings("model")
            return

        from .llm_policy import DataClass, MaterialDescriptor, ModelCallPolicy, preflight_model_call

        class_map = {
            "public_metadata": DataClass.PUBLIC_METADATA, "abstract": DataClass.ABSTRACT,
            "open_fulltext": DataClass.OPEN_FULLTEXT, "licensed_fulltext": DataClass.LICENSED_FULLTEXT,
            "private_notes": DataClass.PRIVATE_NOTES, "sensitive": DataClass.SENSITIVE,
        }
        policy = ModelCallPolicy(
            maximum_data_class=class_map.get(model.maximum_data_class, DataClass.ABSTRACT),
            require_confirmation=model.require_external_confirmation,
            context_window_tokens=model.context_window_tokens,
            input_price_per_million=model.input_price_per_million,
            output_price_per_million=model.output_price_per_million,
            cached_input_price_per_million=model.cached_input_price_per_million,
            price_tiers=list(model.price_tiers),
            currency=model.price_currency,
        )
        materials = [MaterialDescriptor("research_brief", DataClass.PUBLIC_METADATA, len(str(brief.to_dict())), "调研需求")]
        preflight = preflight_model_call(model, policy, materials, purpose="planning")
        if not preflight.allowed:
            messagebox.showwarning("模型调用策略已阻断", "；".join(preflight.warnings) or "调研需求超出当前允许外发等级。", parent=self.master)
            return
        if preflight.requires_confirmation:
            cost = f"，预计费用上限约 {preflight.estimated_cost:.4f} {preflight.currency}" if preflight.estimated_cost is not None else "，当前价格待核验"
            price_source = model.pricing_source or ("用户手工配置" if model.pricing_mode == "manual" else "未记录")
            if not messagebox.askyesno(
                "确认发送调研需求",
                f"将向 {model.provider_name} / {model.model} 发送主题、目标、核心问题和范围要求。预计输入约 {preflight.estimated_input_tokens:,} Token{cost}。\n价格来源：{price_source}；核验/更新时间：{model.pricing_updated_at or '未记录'}。\n\n是否继续？",
                parent=self.master,
            ):
                return

        self.generate_button.state(["disabled"])
        self.agent_mode_hint_var.set(f"正在调用 {model.provider_name} / {model.model}…")

        def worker() -> None:
            audit_event: dict[str, Any] = {}

            def audit_callback(event: dict[str, Any]) -> None:
                audit_event.update(event)

            try:
                text = generate_agent_plan(brief, settings=model, api_key=api_key, audit_callback=audit_callback)
                from .llm_policy import estimate_tokens
                from .provenance import content_hash, now_iso

                metadata = {
                    "mode": "agent",
                    "provider": model.provider_name,
                    "model": model.model,
                    "protocol": model.protocol,
                    "prompt_version": "review-writer:research-plan:v1",
                    "system_prompt_hash": content_hash(audit_event.get("system_prompt", "")),
                    "user_payload_hash": content_hash(audit_event.get("user_prompt", "")),
                    "response_hash": content_hash(audit_event.get("response", "")),
                    "input_tokens_estimated": estimate_tokens(str(audit_event.get("system_prompt", "")) + str(audit_event.get("user_prompt", ""))),
                    "pricing_source": model.pricing_source,
                    "pricing_updated_at": model.pricing_updated_at,
                    "input_price_per_million": model.input_price_per_million,
                    "output_price_per_million": model.output_price_per_million,
                    "price_currency": model.price_currency,
                    "generated_at": now_iso(),
                }
                error: Exception | None = None
            except Exception as caught:
                text = ""
                metadata = {}
                error = caught

            def complete() -> None:
                self.generate_button.state(["!disabled"])
                self._refresh_agent_mode_hint()
                if error is not None:
                    messagebox.showerror(
                        "Agent 生成失败",
                        str(error),
                        parent=self.master,
                    )
                    return
                self.plan_generation_metadata = metadata
                self._finish_plan_generation(brief, text, regenerated=regenerated)

            try:
                self.after(0, complete)
            except tk.TclError:
                return

        threading.Thread(target=worker, daemon=True).start()

    def _finish_plan_generation(
        self,
        brief: ResearchBrief,
        plan_text: str,
        *,
        regenerated: bool,
    ) -> None:
        self.current_brief = brief
        self._set_plan_text(plan_text, mark_dirty=True)
        self._refresh_plan_summary()
        prefix = "Agent 计划" if brief.generation_mode == "agent" else "本地模板计划"
        if regenerated:
            prefix = f"{prefix} · 已重新生成"
        self.plan_status_var.set(f"{prefix} · 尚未保存")
        self.research_cta_var.set("继续编辑")
        self.show_plan()

    def _refresh_plan_summary(self) -> None:
        if self.current_brief is None:
            self.plan_summary_var.set("尚未生成调研计划")
            return
        brief = self.current_brief
        generation = "大模型 Agent" if brief.generation_mode == "agent" else "本地模板"
        self.plan_summary_var.set(
            f"{brief.topic}  ·  {brief.start_year}—{brief.end_year}  ·  "
            f"{len(brief.core_questions)} 个核心问题  ·  {brief.delivery_format}  ·  {generation}"
        )

    def _set_plan_text(self, value: str, *, mark_dirty: bool) -> None:
        self._suspend_plan_events = True
        self.plan_text.delete("1.0", "end")
        self.plan_text.insert("1.0", value)
        self.plan_text.edit_reset()
        self.plan_text.edit_modified(False)
        self._suspend_plan_events = False
        self.plan_dirty = mark_dirty
        self.plan_text.yview_moveto(0)
        self._refresh_plan_preview()

    def _on_plan_modified(self, _event: tk.Event[tk.Misc]) -> None:
        if self._suspend_plan_events:
            self.plan_text.edit_modified(False)
            return
        if self.plan_text.edit_modified():
            self.plan_dirty = True
            self.plan_status_var.set("有未保存修改")
            self.plan_text.edit_modified(False)
            self._schedule_plan_preview()

    def _show_plan_view(self, mode: str) -> None:
        """Switch between the readable rendering and editable Markdown source."""

        if mode not in {"preview", "edit"}:
            raise ValueError(f"未知 Markdown 显示模式：{mode}")
        self.plan_view_mode.set(mode)
        if mode == "preview":
            self._refresh_plan_preview()
            self.plan_preview_frame.tkraise()
            self.plan_preview.focus_set()
        else:
            self.plan_editor_frame.tkraise()
            self.plan_text.focus_set()
        selected = (PRIMARY, "white")
        idle = (SURFACE_MUTED, MUTED)
        self.plan_preview_button.configure(
            background=selected[0] if mode == "preview" else idle[0],
            foreground=selected[1] if mode == "preview" else idle[1],
            activebackground=PRIMARY_ACTIVE if mode == "preview" else SECONDARY_HOVER,
            activeforeground="white" if mode == "preview" else TEXT,
        )
        self.plan_edit_button.configure(
            background=selected[0] if mode == "edit" else idle[0],
            foreground=selected[1] if mode == "edit" else idle[1],
            activebackground=PRIMARY_ACTIVE if mode == "edit" else SECONDARY_HOVER,
            activeforeground="white" if mode == "edit" else TEXT,
        )

    def _schedule_plan_preview(self) -> None:
        if self._plan_preview_job is not None:
            try:
                self.after_cancel(self._plan_preview_job)
            except tk.TclError:
                pass
        self._plan_preview_job = self.after(180, self._refresh_plan_preview)

    def _refresh_plan_preview(self) -> None:
        self._plan_preview_job = None
        render_markdown(self.plan_preview, self._text_value(self.plan_text))

    def _save(self, status: ProjectStatus) -> Path | None:
        if self.current_brief is None:
            messagebox.showerror("无法保存", "请先填写需求并生成调研计划。", parent=self.master)
            return None
        plan = self._text_value(self.plan_text).strip()
        if not plan:
            messagebox.showwarning("计划为空", "调研计划不能为空。", parent=self.master)
            return None
        try:
            self.project_directory = save_project(
                brief=self.current_brief,
                plan_text=plan,
                status=status,
                project_directory=self.project_directory,
                generation_metadata=self.plan_generation_metadata,
            )
        except (OSError, ValueError) as error:
            messagebox.showerror(
                "保存失败",
                f"无法写入本地文件：\n{error}",
                parent=self.master,
            )
            return None
        self.plan_dirty = False
        return self.project_directory

    def save_draft(self) -> None:
        directory = self._save("draft")
        if directory is not None:
            self.plan_status_var.set(f"草稿已保存 · {directory.name}")

    def confirm_plan(self) -> None:
        plan = self._text_value(self.plan_text).strip()
        if not plan:
            messagebox.showwarning("计划为空", "调研计划不能为空。", parent=self.master)
            return
        self._set_plan_text(mark_plan_confirmed(plan), mark_dirty=True)
        directory = self._save("confirmed")
        if directory is None:
            return
        self.plan_status_var.set(f"已确认 · {directory.name}")
        self.research_cta_var.set("继续执行调研")
        self.execution_workspace.set_project(
            brief=self.current_brief,
            confirmed_plan=self._text_value(self.plan_text),
            project_directory=directory,
        )
        messagebox.showinfo(
            "调研计划已确认",
            "结构化需求和确认后的调研计划已保存。接下来可生成关键词树与宽/精检索式。\n\n"
            f"项目目录：{directory}",
            parent=self.master,
        )
        self.show_execution()

    def open_existing_project(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.master,
            title="选择 Review Writer 项目文件夹",
        )
        if not selected:
            return
        try:
            brief, plan, status, _manifest = load_project(Path(selected))
        except ValueError as error:
            messagebox.showerror("无法打开项目", str(error), parent=self.master)
            return
        self.current_brief = brief
        self.plan_generation_metadata = dict(_manifest.get("plan_generation") or {})
        self.project_directory = Path(selected).resolve()
        self._set_plan_text(plan, mark_dirty=False)
        self._refresh_plan_summary()
        if status != "confirmed":
            self.plan_status_var.set(f"草稿已载入 · {self.project_directory.name}")
            messagebox.showinfo(
                "项目仍是草稿",
                "已载入调研计划草稿。请检查并确认计划后再进入检索阶段。",
                parent=self.master,
            )
            self.show_plan()
            return
        self.plan_status_var.set(f"已确认 · {self.project_directory.name}")
        self.research_cta_var.set("继续执行调研")
        self.execution_workspace.set_project(
            brief=brief,
            confirmed_plan=plan,
            project_directory=self.project_directory,
        )
        self.show_execution()

    def apply_theme(
        self,
        theme_id: str,
        custom_accent: str,
        ui_font_preference: str | None = None,
        mono_font_preference: str | None = None,
    ) -> None:
        """Apply and persist colors and fonts without rebuilding the workspace."""

        old_palette = self.palette
        new_palette = get_palette(theme_id, custom_accent)
        old_ui_font = self.ui_font
        old_mono_font = self.mono_font
        ui_preference = (
            self.settings.appearance.ui_font
            if ui_font_preference is None
            else ui_font_preference
        )
        mono_preference = (
            self.settings.appearance.mono_font
            if mono_font_preference is None
            else mono_font_preference
        )
        new_ui_font, new_mono_font = resolve_fonts(
            self.master,
            ui_preference,
            mono_preference,
        )
        self.settings.appearance.theme_id = theme_id
        self.settings.appearance.custom_accent = custom_accent
        self.settings.appearance.ui_font = ui_preference
        self.settings.appearance.mono_font = mono_preference
        try:
            self.settings_store.save(self.settings)
        except OSError:
            pass

        apply_palette(new_palette)
        settings_views.apply_palette(new_palette)
        workflow_view.apply_palette(new_palette)
        apply_fonts(new_ui_font, new_mono_font)
        settings_views.apply_fonts(new_ui_font, new_mono_font)
        workflow_view.apply_fonts(new_ui_font, new_mono_font)
        configure_named_fonts(self.master, new_ui_font, new_mono_font)
        self.palette = new_palette
        self.ui_font = new_ui_font
        self.mono_font = new_mono_font
        self.master.configure(background=new_palette.background)
        self._configure_styles()
        self._recolor_widget_tree(self.master, palette_color_map(old_palette, new_palette))
        recolor_icons(
            self.master,
            {
                "primary": new_palette.primary,
                "text": new_palette.text,
                "muted": new_palette.muted,
                "sidebar_text": new_palette.sidebar_text,
                "sidebar_muted": new_palette.sidebar_muted,
                "success": new_palette.sidebar_success,
                "white": "#ffffff",
            },
        )
        refont_widget_tree(
            self.master,
            old_ui_font=old_ui_font,
            new_ui_font=new_ui_font,
            old_mono_font=old_mono_font,
            new_mono_font=new_mono_font,
        )
        configure_markdown_tags(
            self.plan_preview,
            palette=new_palette,
            ui_font=FONT,
            mono_font=MONO_FONT,
        )
        self._refresh_plan_preview()
        self._show_plan_view(self.plan_view_mode.get())
        self.execution_workspace.apply_visual_preferences(
            new_palette,
            new_ui_font,
            new_mono_font,
        )
        self._update_navigation_state()

    @classmethod
    def _recolor_widget_tree(cls, widget: tk.Misc, colors: dict[str, str]) -> None:
        color_options = (
            "background",
            "foreground",
            "activebackground",
            "activeforeground",
            "disabledforeground",
            "highlightbackground",
            "highlightcolor",
            "insertbackground",
            "readonlybackground",
            "selectbackground",
            "selectcolor",
            "troughcolor",
        )
        try:
            configuration = widget.configure()
        except tk.TclError:
            configuration = {}
        updates: dict[str, str] = {}
        for option in color_options:
            if option not in configuration:
                continue
            try:
                current = str(widget.cget(option)).lower()
            except tk.TclError:
                continue
            if current in colors:
                updates[option] = colors[current]
        if updates:
            try:
                widget.configure(**updates)
            except tk.TclError:
                pass
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            cls._recolor_widget_tree(child, colors)

    def show_section(self, key: str) -> None:
        if key in getattr(self, "settings_tabs", {}):
            self.show_settings(key)
            return
        if key not in self.sections:
            raise ValueError(f"未知页面：{key}")
        self.sections[key].tkraise()
        self.active_section.set(key)
        self._update_navigation_state()
        if key == "research":
            if self.research_stage.get() == "execution":
                self.research_execution_page.tkraise()
            elif self.research_stage.get() == "plan":
                self.research_plan_page.tkraise()
            else:
                self.research_form_page.tkraise()

    def show_settings(self, tab: str | None = None) -> None:
        """Open the unified settings workspace and optionally select one category."""

        if tab is not None:
            if tab not in self.settings_tabs:
                raise ValueError(f"未知设置页面：{tab}")
            self.settings_notebook.select(self.settings_tabs[tab])
        self.show_section("settings")

    def _set_workflow_navigation_expanded(self, expanded: bool) -> None:
        self.workflow_nav_expanded = expanded
        if expanded:
            self.workflow_nav_frame.grid()
        else:
            self.workflow_nav_frame.grid_remove()
        self.research_nav_button.configure(
            text="调研计划",
            image=self._icon(
                "chevron_down" if expanded else "chevron_right",
                "sidebar_text" if self.active_section.get() == "research" else "sidebar_muted",
            ),
        )

    def toggle_workflow_navigation(self) -> None:
        """Open the planner and toggle its stage list in the sidebar."""

        self._set_workflow_navigation_expanded(not self.workflow_nav_expanded)
        self.show_section("research")
        self._focus_research_stage()

    def open_workflow_stage(self, stage_index: int) -> None:
        """Open one of the numbered planner or execution stages."""

        if stage_index not in self.workflow_nav_buttons:
            raise ValueError(f"未知工作台阶段：{stage_index}")
        self._set_workflow_navigation_expanded(True)
        if stage_index == 0:
            if self.current_brief is None:
                self.show_form()
            else:
                self.show_plan()
            return
        if not self.show_execution():
            return
        self.execution_workspace.notebook.select(stage_index - 1)
        self._update_navigation_state()

    def _on_execution_tab_changed(self, _event: tk.Event[tk.Misc]) -> None:
        if self.research_stage.get() == "execution":
            self._update_navigation_state()

    def _current_workflow_stage_index(self) -> int:
        if self.research_stage.get() != "execution":
            return 0
        try:
            return int(self.execution_workspace.notebook.index("current")) + 1
        except tk.TclError:
            return 1

    def _focus_research_stage(self) -> None:
        if self.research_stage.get() == "form":
            self.topic_entry.focus_set()
        elif self.research_stage.get() == "plan":
            target = self.plan_preview if self.plan_view_mode.get() == "preview" else self.plan_text
            target.focus_set()

    def _update_navigation_state(self) -> None:
        active = self.active_section.get()
        self.home_button.configure(
            background=SIDEBAR_ACTIVE if active == "home" else SIDEBAR,
            foreground=SIDEBAR_TEXT,
        )
        for key, button in self.nav_buttons.items():
            selected = key == active
            button.configure(
                background=SIDEBAR_ACTIVE if selected else SIDEBAR,
                foreground=SIDEBAR_TEXT if selected else SIDEBAR_MUTED,
            )
        self.research_nav_button.configure(
            image=self._icon(
                "chevron_down" if self.workflow_nav_expanded else "chevron_right",
                "sidebar_text" if active == "research" else "sidebar_muted",
            )
        )
        workflow_stage = self._current_workflow_stage_index()
        for index, button in self.workflow_nav_buttons.items():
            selected = active == "research" and index == workflow_stage
            button.configure(
                background=PRIMARY if selected else SIDEBAR,
                foreground="white" if selected else SIDEBAR_MUTED,
                image=self._icon(
                    self.workflow_nav_icon_names[index],
                    "white" if selected else "sidebar_muted",
                ),
            )
        settings_active = active == "settings"
        self.settings_button.configure(
            background=SIDEBAR_ACTIVE if settings_active else SIDEBAR,
            foreground=SIDEBAR_TEXT if settings_active else SIDEBAR_MUTED,
        )

    def open_research(self) -> None:
        self._set_workflow_navigation_expanded(True)
        self.show_section("research")
        self._focus_research_stage()

    def show_form(self) -> None:
        self.research_stage.set("form")
        self.research_form_page.tkraise()
        self.show_section("research")
        self.topic_entry.focus_set()

    def show_plan(self) -> None:
        self.research_stage.set("plan")
        self.research_plan_page.tkraise()
        self.show_section("research")
        (self.plan_preview if self.plan_view_mode.get() == "preview" else self.plan_text).focus_set()

    def show_execution(self) -> bool:
        if self.current_brief is None or self.project_directory is None:
            messagebox.showwarning(
                "尚无已确认项目",
                "请先确认调研计划，或打开一个已确认的项目。",
                parent=self.master,
            )
            return False
        self.research_stage.set("execution")
        self.research_execution_page.tkraise()
        self.show_section("research")
        return True

    def _bind_shortcuts(self) -> None:
        self.master.bind("<Control-Key-1>", lambda _event: self.open_research())
        self.master.bind("<Control-Key-2>", lambda _event: self.show_settings("database"))
        self.master.bind("<Control-Key-3>", lambda _event: self.show_settings("model"))
        self.master.bind("<Control-Key-4>", lambda _event: self.show_settings("appearance"))
        self.master.bind("<Control-Home>", lambda _event: self.show_section("home"))
        self.master.bind("<Control-Return>", self._shortcut_generate)
        self.master.bind("<Control-s>", self._shortcut_save)

    def _shortcut_generate(self, _event: tk.Event[tk.Misc]) -> str:
        if self.active_section.get() == "research" and self.research_stage.get() == "form":
            self.generate_plan()
        return "break"

    def _shortcut_save(self, _event: tk.Event[tk.Misc]) -> str:
        if self.active_section.get() == "research" and self.research_stage.get() == "plan":
            self.save_draft()
        return "break"

    def _request_close(self) -> None:
        if self.plan_dirty and self._text_value(self.plan_text).strip():
            should_close = messagebox.askyesno(
                "存在未保存修改",
                "调研计划还有未保存的修改。确定要关闭应用吗？",
                parent=self.master,
            )
            if not should_close:
                return
        self.master.destroy()


def launch() -> None:
    """Create and run the Tk application."""

    root = tk.Tk()
    ResearchPlannerApp(root)
    root.mainloop()
