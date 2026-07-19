"""Tkinter pages for real model and data-source settings."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Any, Callable
from urllib.parse import urlparse
import webbrowser

from .generators import LLMClient, LLMRequestError
from .iconography import get_icon
from .integrations import ImaConnector, IntegrationError, LibraryConnector, ZoteroConnector
from .model_catalog import (
    CatalogUpdateResult,
    ModelCatalogEntry,
    ModelCatalogService,
)
from .secret_store import SecretStore, SecretStoreError
from .settings import (
    AppSettings,
    AppearanceSettings,
    DiscoverySettings,
    ImaSettings,
    LibrarySettings,
    ModelSettings,
    SettingsStore,
    ZoteroSettings,
)
from .theme import THEME_PRESETS, ThemePalette, normalize_hex_color
from .typography import installed_font_families, resolve_fonts
from .ui_utils import AutoHideScrollbar, enable_hover_wheel


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
PRIMARY_PALE = "#eaf1ff"
SUCCESS = "#16835b"
SUCCESS_PALE = "#e8f6f0"
WARNING = "#b76e00"
WARNING_PALE = "#fff5df"
TEXT_AREA = "#fbfcfe"
PIXEL_BACKGROUND = "#122033"
PIXEL_PANEL = "#203651"
PIXEL_TEXT = "#f8fafc"
PIXEL_MUTED = "#aebdce"
PIXEL_GRID = "#263a50"


def apply_fonts(ui_font: str, mono_font: str) -> None:
    """Update module font families before widgets are created."""

    global FONT, MONO_FONT
    FONT = ui_font
    MONO_FONT = mono_font


def apply_palette(palette: ThemePalette) -> None:
    """Update module colors before new views are built or recolored."""

    global BACKGROUND, SURFACE, SURFACE_MUTED, BORDER, TEXT, MUTED, SUBTLE
    global PRIMARY, PRIMARY_PALE, SUCCESS, SUCCESS_PALE, WARNING, WARNING_PALE, TEXT_AREA
    global PIXEL_BACKGROUND, PIXEL_PANEL, PIXEL_TEXT, PIXEL_MUTED, PIXEL_GRID
    BACKGROUND = palette.background
    SURFACE = palette.surface
    SURFACE_MUTED = palette.surface_muted
    BORDER = palette.border
    TEXT = palette.text
    MUTED = palette.muted
    SUBTLE = palette.subtle
    PRIMARY = palette.primary
    PRIMARY_PALE = palette.primary_pale
    SUCCESS = palette.success
    SUCCESS_PALE = palette.success_pale
    WARNING = palette.warning
    WARNING_PALE = palette.warning_pale
    TEXT_AREA = palette.text_area
    PIXEL_BACKGROUND = palette.sidebar
    PIXEL_PANEL = palette.sidebar_active
    PIXEL_TEXT = palette.sidebar_text
    PIXEL_MUTED = palette.sidebar_muted
    PIXEL_GRID = palette.sidebar_divider


class _Scroll(tk.Frame):
    def __init__(self, master: tk.Widget) -> None:
        super().__init__(master, background=BACKGROUND)
        self.canvas = tk.Canvas(self, background=BACKGROUND, highlightthickness=0, borderwidth=0)
        self.scrollbar = AutoHideScrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = tk.Frame(self.canvas, background=BACKGROUND)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.body.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>", lambda event: self.canvas.itemconfigure(self.window, width=event.width)
        )
        enable_hover_wheel(self, self.canvas)


def _card(parent: tk.Widget) -> tuple[tk.Frame, tk.Frame]:
    outer = tk.Frame(
        parent,
        background=SURFACE,
        highlightbackground=PRIMARY,
        highlightthickness=2,
        borderwidth=0,
    )
    inner = tk.Frame(outer, background=SURFACE)
    inner.pack(fill="both", expand=True, padx=24, pady=22)
    return outer, inner


def _label(parent: tk.Widget, text: str, *, row: int, column: int = 0, span: int = 1) -> None:
    tk.Label(
        parent,
        text=text,
        background=SURFACE,
        foreground=TEXT,
        font=(MONO_FONT, 9, "bold"),
        anchor="w",
    ).grid(row=row, column=column, columnspan=span, sticky="w", pady=(0, 5))


def _entry(parent: tk.Widget, variable: tk.StringVar, *, row: int, column: int = 0, span: int = 1, show: str = "") -> ttk.Entry:
    widget = ttk.Entry(parent, textvariable=variable, style="App.TEntry", show=show)
    widget.grid(row=row, column=column, columnspan=span, sticky="ew", pady=(0, 13))
    return widget


def _section_title(parent: tk.Widget, title: str, description: str) -> None:
    tk.Label(
        parent,
        text=f"// {title}",
        background=SURFACE,
        foreground=TEXT,
        font=(MONO_FONT, 12, "bold"),
        anchor="w",
    ).pack(anchor="w")
    tk.Label(
        parent,
        text=description,
        background=SURFACE,
        foreground=MUTED,
        font=(FONT, 9),
        justify="left",
        anchor="w",
        wraplength=820,
    ).pack(anchor="w", pady=(5, 0))


def _icon(master: tk.Misc, name: str, role: str = "primary") -> tk.PhotoImage:
    colors = {
        "primary": PRIMARY,
        "text": TEXT,
        "muted": MUTED,
        "white": "#ffffff",
    }
    return get_icon(master, name, colors[role], role=role)


class _BaseSettingsView(tk.Frame):
    def __init__(self, master: tk.Widget, *, on_back: Callable[[], None]) -> None:
        super().__init__(master, background=BACKGROUND)
        self.on_back = on_back
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

    def build_header(
        self,
        title: str,
        description: str,
        *,
        badge: str = "凭证不写入调研项目",
        scrollable: bool = True,
    ) -> _Scroll | tk.Frame:
        header = tk.Frame(
            self,
            background=PIXEL_BACKGROUND,
            highlightbackground=PRIMARY,
            highlightthickness=2,
        )
        header.grid(row=0, column=0, sticky="ew", padx=38, pady=(28, 18))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="[ SYSTEM_SETTINGS // CONTROL_PANEL ]",
            background=PIXEL_BACKGROUND,
            foreground=PRIMARY,
            font=(MONO_FONT, 9, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(12, 0))
        tk.Label(
            header,
            text=title,
            background=PIXEL_BACKGROUND,
            foreground=PIXEL_TEXT,
            font=(MONO_FONT, 19, "bold"),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(5, 3))
        tk.Label(
            header,
            text=description,
            background=PIXEL_BACKGROUND,
            foreground=PIXEL_MUTED,
            font=(FONT, 10),
            anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 12))
        tk.Label(
            header,
            text=f"[ {badge} ]",
            background=PIXEL_PANEL,
            foreground=PIXEL_TEXT,
            font=(MONO_FONT, 8, "bold"),
            padx=11,
            pady=6,
        ).grid(row=0, column=1, rowspan=2, sticky="ne", padx=14, pady=12)
        if scrollable:
            content: _Scroll | tk.Frame = _Scroll(self)
            content.grid(row=1, column=0, sticky="nsew", padx=38)
            content.body.columnconfigure(0, weight=1)
        else:
            content = tk.Frame(self, background=BACKGROUND)
            content.grid(row=1, column=0, sticky="nsew", padx=38)
            content.columnconfigure(0, weight=1)
            content.rowconfigure(0, weight=1)
        footer = tk.Frame(self, background=BACKGROUND)
        footer.grid(row=2, column=0, sticky="ew", padx=38, pady=(12, 22))
        ttk.Button(
            footer,
            text="返回工作台",
            image=_icon(self, "back"),
            compound="left",
            style="Secondary.TButton",
            command=self.on_back,
        ).pack(side="left")
        return content

    def run_async(
        self,
        button: ttk.Button,
        status_var: tk.StringVar,
        working_message: str,
        worker: Callable[[], Any],
        complete: Callable[[Any], None],
    ) -> None:
        button.state(["disabled"])
        status_var.set(working_message)

        def target() -> None:
            try:
                result: Any = worker()
                error: Exception | None = None
            except Exception as caught:  # UI boundary: normalize connector errors.
                result = None
                error = caught

            def finish() -> None:
                button.state(["!disabled"])
                if error is not None:
                    status_var.set(f"失败：{error}")
                    return
                complete(result)

            try:
                self.after(0, finish)
            except tk.TclError:
                return

        threading.Thread(target=target, daemon=True).start()


class AppearanceSettingsView(_BaseSettingsView):
    """Theme preset and custom accent settings."""

    def __init__(
        self,
        master: tk.Widget,
        *,
        settings_store: SettingsStore,
        settings: AppSettings,
        on_back: Callable[[], None],
        on_applied: Callable[[str, str, str, str], None],
    ) -> None:
        super().__init__(master, on_back=on_back)
        self.settings_store = settings_store
        self.settings = settings
        self.on_applied = on_applied
        self.theme_var = tk.StringVar(value=settings.appearance.theme_id)
        self.custom_accent_var = tk.StringVar(value=settings.appearance.custom_accent)
        self.auto_font_label = "自动选择（推荐）"
        self.font_families = installed_font_families(self)
        available_fonts = set(self.font_families)
        saved_ui_font = settings.appearance.ui_font
        saved_mono_font = settings.appearance.mono_font
        self.ui_font_var = tk.StringVar(
            value=saved_ui_font if saved_ui_font in available_fonts else self.auto_font_label
        )
        self.mono_font_var = tk.StringVar(
            value=saved_mono_font if saved_mono_font in available_fonts else self.auto_font_label
        )
        self.status_var = tk.StringVar(value="选择配色和字体后点击“应用界面设置”")

        scroll = self.build_header(
            "界面外观",
            "选择内置主题，或指定自己的强调色；设置会保存在本机。",
            badge="切换后立即生效",
        )
        self._build_content(scroll.body)

    def _build_content(self, body: tk.Frame) -> None:
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        tk.Label(
            body,
            text="[ THEME_PRESETS ]  内置配色方案",
            background=BACKGROUND,
            foreground=TEXT,
            font=(MONO_FONT, 11, "bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.theme_cards: dict[str, tk.Frame] = {}
        for index, preset in enumerate(THEME_PRESETS):
            row = 1 + index // 2
            column = index % 2
            card = tk.Frame(
                body,
                background=SURFACE,
                highlightbackground=PIXEL_GRID,
                highlightthickness=2,
                cursor="hand2",
            )
            card.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 7, 7 if column == 0 else 0),
                pady=(0, 12),
            )
            card.columnconfigure(1, weight=1)
            radio = tk.Radiobutton(
                card,
                variable=self.theme_var,
                value=preset.id,
                command=lambda value=preset.id: self._select_theme(value),
                background=SURFACE,
                activebackground=SURFACE,
                selectcolor=SURFACE,
                highlightthickness=0,
                cursor="hand2",
            )
            radio.grid(row=0, column=0, rowspan=2, padx=(14, 5), pady=14)
            tk.Label(
                card,
                text=preset.name,
                background=SURFACE,
                foreground=TEXT,
                font=(MONO_FONT, 10, "bold"),
                anchor="w",
                cursor="hand2",
            ).grid(row=0, column=1, sticky="sw", pady=(12, 2))
            tk.Label(
                card,
                text=preset.description,
                background=SURFACE,
                foreground=MUTED,
                font=(FONT, 8),
                anchor="w",
                cursor="hand2",
            ).grid(row=1, column=1, sticky="nw", pady=(0, 12))
            swatches = tk.Frame(card, background=SURFACE, cursor="hand2")
            swatches.grid(row=0, column=2, rowspan=2, padx=(10, 14))
            for color in (
                preset.palette.primary,
                preset.palette.primary_pale,
                preset.palette.sidebar,
            ):
                swatch = tk.Frame(swatches, background=color, width=18, height=18)
                swatch.pack(side="left", padx=2)
                swatch.pack_propagate(False)
            self.theme_cards[preset.id] = card
            for widget in (card, *card.winfo_children()):
                if widget is not radio:
                    widget.bind("<Button-1>", lambda _event, value=preset.id: self._select_theme(value))

        custom_row = 1 + (len(THEME_PRESETS) + 1) // 2
        outer, custom = _card(body)
        self.theme_cards["custom"] = outer
        outer.grid(row=custom_row, column=0, columnspan=2, sticky="ew", pady=(4, 18))
        custom.columnconfigure(1, weight=1)
        tk.Radiobutton(
            custom,
            text="自定义强调色",
            variable=self.theme_var,
            value="custom",
            command=lambda: self._select_theme("custom"),
            background=SURFACE,
            activebackground=SURFACE,
            selectcolor=SURFACE,
            foreground=TEXT,
            font=(FONT, 10, "bold"),
            highlightthickness=0,
        ).grid(row=0, column=0, sticky="w")
        self.custom_preview = tk.Label(
            custom,
            background=normalize_hex_color(self.custom_accent_var.get()),
            width=4,
            height=1,
        )
        self.custom_preview.grid(row=0, column=1, sticky="e", padx=(12, 8))
        ttk.Entry(custom, textvariable=self.custom_accent_var, style="App.TEntry", width=12).grid(
            row=0, column=2, sticky="e", padx=(0, 8)
        )
        ttk.Button(
            custom,
            text="选择颜色…",
            style="Secondary.TButton",
            command=self._choose_color,
        ).grid(row=0, column=3, sticky="e")
        tk.Label(
            custom,
            text="请输入 #RRGGBB，系统会自动生成按钮悬停色、浅色背景和侧边栏颜色。",
            background=SURFACE,
            foreground=MUTED,
            font=(MONO_FONT, 8),
            anchor="w",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(10, 0))

        font_outer, font_card = _card(body)
        font_outer.grid(row=custom_row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        font_card.columnconfigure(0, weight=1)
        font_card.columnconfigure(1, weight=1)
        font_heading = tk.Frame(font_card, background=SURFACE)
        font_heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        _section_title(
            font_heading,
            "显示字体",
            "分别设置界面文字与 Markdown 原文/代码字体；选择后会立即应用并保存在本机。",
        )
        _label(font_card, "界面字体", row=1, column=0)
        _label(font_card, "等宽字体", row=1, column=1)
        values = (self.auto_font_label, *self.font_families)
        self.ui_font_box = ttk.Combobox(
            font_card,
            textvariable=self.ui_font_var,
            values=values,
            state="readonly",
            style="App.TCombobox",
        )
        self.ui_font_box.grid(row=2, column=0, sticky="ew", pady=(0, 13), padx=(0, 6))
        self.mono_font_box = ttk.Combobox(
            font_card,
            textvariable=self.mono_font_var,
            values=values,
            state="readonly",
            style="App.TCombobox",
        )
        self.mono_font_box.grid(row=2, column=1, sticky="ew", pady=(0, 13), padx=(6, 0))
        self.ui_font_box.bind("<<ComboboxSelected>>", self._update_font_preview)
        self.mono_font_box.bind("<<ComboboxSelected>>", self._update_font_preview)
        self.font_preview = tk.Label(
            font_card,
            text="文献调研 Research  /  Markdown `CODE`  /  0123456789",
            background=SURFACE_MUTED,
            foreground=TEXT,
            anchor="w",
            padx=14,
            pady=12,
        )
        self.font_preview.grid(row=3, column=0, columnspan=2, sticky="ew")
        self._update_font_preview()
        self._refresh_theme_cards()

        actions = tk.Frame(body, background=BACKGROUND)
        actions.grid(row=custom_row + 2, column=0, columnspan=2, sticky="ew", pady=(0, 20))
        tk.Label(
            actions,
            textvariable=self.status_var,
            background=BACKGROUND,
            foreground=MUTED,
            font=(MONO_FONT, 8),
        ).pack(side="left")
        ttk.Button(
            actions,
            text="恢复海洋蓝",
            style="Secondary.TButton",
            command=lambda: self._select_theme("ocean"),
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            actions,
            text="应用界面设置",
            style="Primary.TButton",
            command=self.apply_theme,
        ).pack(side="right")

    def _select_theme(self, theme_id: str) -> None:
        self.theme_var.set(theme_id)
        self._refresh_theme_cards()
        self.status_var.set("已选择，点击“应用界面设置”后生效")

    def _refresh_theme_cards(self) -> None:
        selected = self.theme_var.get()
        for theme_id, card in self.theme_cards.items():
            card.configure(
                highlightbackground=PRIMARY if theme_id == selected else PIXEL_GRID,
                highlightthickness=3 if theme_id == selected else 2,
            )

    def _choose_color(self) -> None:
        initial = normalize_hex_color(self.custom_accent_var.get())
        _rgb, selected = colorchooser.askcolor(color=initial, parent=self)
        if not selected:
            return
        color = normalize_hex_color(selected)
        self.custom_accent_var.set(color)
        self.custom_preview.configure(background=color)
        self._select_theme("custom")

    def _font_preferences(self) -> tuple[str, str]:
        ui_font = "" if self.ui_font_var.get() == self.auto_font_label else self.ui_font_var.get()
        mono_font = "" if self.mono_font_var.get() == self.auto_font_label else self.mono_font_var.get()
        return ui_font, mono_font

    def _update_font_preview(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        ui_preference, mono_preference = self._font_preferences()
        ui_font, mono_font = resolve_fonts(self, ui_preference, mono_preference)
        self.font_preview.configure(font=(ui_font, 11))
        # Keep the selected code family visible even though the sentence uses the UI font.
        self.font_preview.configure(text=f"文献调研 Research  /  {mono_font}: Markdown CODE 0123456789")

    def apply_theme(self) -> None:
        theme_id = self.theme_var.get()
        accent = self.custom_accent_var.get().strip()
        normalized = normalize_hex_color(accent, fallback="")
        if theme_id == "custom" and not normalized:
            self.status_var.set("自定义颜色格式应为 #RRGGBB")
            return
        if theme_id != "custom":
            normalized = self.settings.appearance.custom_accent
        ui_font, mono_font = self._font_preferences()
        self.settings.appearance = AppearanceSettings(
            theme_id=theme_id,
            custom_accent=normalized,
            ui_font=ui_font,
            mono_font=mono_font,
        )
        try:
            self.settings_store.save(self.settings)
        except OSError as error:
            self.status_var.set(f"保存失败：{error}")
            return
        self.on_applied(theme_id, normalized, ui_font, mono_font)
        self.status_var.set("界面配色与字体已应用并保存")


class ModelSettingsView(_BaseSettingsView):
    def __init__(
        self,
        master: tk.Widget,
        *,
        settings_store: SettingsStore,
        settings: AppSettings,
        secret_store: SecretStore,
        on_back: Callable[[], None],
        on_saved: Callable[[], None],
    ) -> None:
        super().__init__(master, on_back=on_back)
        self.settings_store = settings_store
        self.settings = settings
        self.secret_store = secret_store
        self.on_saved = on_saved
        self.catalog_service = ModelCatalogService(settings_store.path.parent)
        self.catalog_document = self.catalog_service.load_effective()
        self.catalog = list(self.catalog_document.models)
        self.catalog_by_iid: dict[str, ModelCatalogEntry] = {}

        model = settings.model
        self.provider_id_var = tk.StringVar(value=model.provider_id)
        self.provider_name_var = tk.StringVar(value=model.provider_name)
        self.api_base_var = tk.StringVar(value=model.api_base)
        self.model_var = tk.StringVar(value=model.model)
        self.protocol_var = tk.StringVar(value=model.protocol)
        self.api_key_var = tk.StringVar()
        self.persist_var = tk.BooleanVar(value=model.persist_api_key)
        self.timeout_var = tk.StringVar(value=str(model.timeout_seconds))
        self.temperature_var = tk.StringVar(value=str(model.temperature))
        self.max_tokens_var = tk.StringVar(value=str(model.max_output_tokens))
        self.context_tokens_var = tk.StringVar(value=str(model.context_window_tokens))
        self.data_class_var = tk.StringVar(value=model.maximum_data_class)
        self.require_confirmation_var = tk.BooleanVar(value=model.require_external_confirmation)
        self.input_price_var = tk.StringVar(value="" if model.input_price_per_million is None else str(model.input_price_per_million))
        self.output_price_var = tk.StringVar(value="" if model.output_price_per_million is None else str(model.output_price_per_million))
        self.cached_input_price_var = tk.StringVar(value="" if model.cached_input_price_per_million is None else str(model.cached_input_price_per_million))
        self.price_currency_var = tk.StringVar(value=model.price_currency)
        self.pricing_mode_var = tk.StringVar(value=model.pricing_mode or "catalog")
        self.pricing_source_var = tk.StringVar(value=model.pricing_source or self.catalog_document.source)
        self.catalog_url_var = tk.StringVar(value=settings.model_catalog.update_url)
        self.catalog_auto_var = tk.BooleanVar(value=settings.model_catalog.auto_check)
        self.catalog_interval_var = tk.StringVar(value=str(settings.model_catalog.update_interval_days))
        self.catalog_filter_var = tk.StringVar()
        self.catalog_price_filter_var = tk.StringVar(value="全部价格")
        self.catalog_status_var = tk.StringVar(value=self._catalog_status_text())
        self.status_var = tk.StringVar(
            value="已保存 API Key" if secret_store.has("model.api_key") else "尚未保存 API Key"
        )
        self.detail_var = tk.StringVar(value="从下方推荐目录选择模型，或直接编辑为自定义接口。")

        content = self.build_header(
            "大模型设置",
            "配置调研计划 Agent 使用的模型接口，并查看带官方链接和核验日期的推荐目录。",
            scrollable=False,
        )
        assert isinstance(content, tk.Frame)
        self._build(content)
        if self.pricing_mode_var.get() == "catalog":
            entry = self.catalog_document.find(model.provider_id, model.model)
            if entry is not None:
                self._apply_entry_to_variables(entry, include_identity=False)
                self.settings.model = self._current_model_settings()
        self._catalog_update_job: str | None = self.after(250, self._auto_update_if_due)
        self.bind("<Destroy>", self._cancel_catalog_update_job, add="+")

    def _build(self, body: tk.Frame) -> None:
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(body, style="SubBookmark.TNotebook")
        notebook.grid(row=0, column=0, sticky="nsew", pady=(0, 18))
        self.notebook = notebook
        config_tab = tk.Frame(notebook, background=BACKGROUND)
        catalog_tab = tk.Frame(notebook, background=BACKGROUND)
        notebook.add(
            config_tab,
            text="模型配置",
            image=_icon(self, "settings"),
            compound="left",
        )
        notebook.add(
            catalog_tab,
            text="模型推荐",
            image=_icon(self, "model"),
            compound="left",
        )
        config_scroll = _Scroll(config_tab)
        config_scroll.pack(fill="both", expand=True, padx=12, pady=12)
        config_scroll.body.columnconfigure(0, weight=1)
        catalog_scroll = _Scroll(catalog_tab)
        catalog_scroll.pack(fill="both", expand=True, padx=12, pady=12)
        catalog_scroll.body.columnconfigure(0, weight=1)

        config_outer, config = _card(config_scroll.body)
        config_outer.grid(row=0, column=0, sticky="ew")
        config.columnconfigure(0, weight=1)
        config.columnconfigure(1, weight=1)
        heading = tk.Frame(config, background=SURFACE)
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        _section_title(heading, "默认模型配置", "API Key 默认通过 Windows DPAPI 加密；取消勾选后只在本次运行中保留。")

        _label(config, "服务商名称", row=1, column=0)
        _label(config, "接口协议", row=1, column=1)
        _entry(config, self.provider_name_var, row=2, column=0)
        ttk.Combobox(
            config,
            textvariable=self.protocol_var,
            values=("openai_responses", "openai_compatible", "anthropic", "gemini", "ollama"),
            state="readonly",
            style="App.TCombobox",
        ).grid(row=2, column=1, sticky="ew", pady=(0, 13), padx=(10, 0))

        _label(config, "API Base URL", row=3, span=2)
        _entry(config, self.api_base_var, row=4, span=2)
        _label(config, "模型名称", row=5, span=2)
        _entry(config, self.model_var, row=6, span=2)
        _label(config, "API Key（留空表示继续使用已保存密钥）", row=7, span=2)
        _entry(config, self.api_key_var, row=8, span=2, show="•")

        options = tk.Frame(config, background=SURFACE)
        options.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ttk.Checkbutton(options, text="使用 DPAPI 持久保存密钥", variable=self.persist_var).pack(side="left")
        tk.Label(
            options,
            text="密钥不会进入 settings.json、project.json 或 Markdown",
            background=SURFACE,
            foreground=SUBTLE,
            font=(MONO_FONT, 8),
        ).pack(side="right")

        advanced = tk.Frame(config, background=SURFACE)
        advanced.grid(row=10, column=0, columnspan=2, sticky="ew")
        advanced.columnconfigure((0, 1, 2), weight=1, uniform="model-options")
        for column, (label, variable) in enumerate(
            (("超时（秒）", self.timeout_var), ("温度", self.temperature_var), ("最大输出 Token", self.max_tokens_var))
        ):
            tk.Label(advanced, text=label, background=SURFACE, foreground=TEXT, font=(FONT, 8, "bold")).grid(
                row=0, column=column, sticky="w", padx=(0 if column == 0 else 8, 0)
            )
            ttk.Entry(advanced, textvariable=variable, style="App.TEntry").grid(
                row=1, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0), pady=(5, 0)
            )

        policy = tk.Frame(config, background=SURFACE_MUTED)
        policy.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        policy.columnconfigure((0, 1, 2), weight=1)
        tk.Label(policy, text="允许发送的最高材料等级", background=SURFACE_MUTED, foreground=TEXT, font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        tk.Label(policy, text="上下文窗口 Token", background=SURFACE_MUTED, foreground=TEXT, font=(FONT, 8, "bold")).grid(row=0, column=1, sticky="w", padx=10, pady=(10, 4))
        tk.Label(policy, text="输入/输出/缓存输入价格（每百万 Token）", background=SURFACE_MUTED, foreground=TEXT, font=(FONT, 8, "bold")).grid(row=0, column=2, sticky="w", padx=10, pady=(10, 4))
        ttk.Combobox(policy, textvariable=self.data_class_var, values=("public_metadata", "abstract", "open_fulltext", "licensed_fulltext", "private_notes", "sensitive"), state="readonly").grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        ttk.Entry(policy, textvariable=self.context_tokens_var, style="App.TEntry").grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 8))
        prices = tk.Frame(policy, background=SURFACE_MUTED)
        prices.grid(row=1, column=2, sticky="ew", padx=10, pady=(0, 8))
        ttk.Entry(prices, textvariable=self.input_price_var, width=10).pack(side="left", fill="x", expand=True)
        ttk.Entry(prices, textvariable=self.output_price_var, width=10).pack(side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Entry(prices, textvariable=self.cached_input_price_var, width=10).pack(side="left", fill="x", expand=True, padx=(6, 0))
        price_options = tk.Frame(policy, background=SURFACE_MUTED)
        price_options.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 4))
        ttk.Radiobutton(price_options, text="价格自动跟随推荐目录", variable=self.pricing_mode_var, value="catalog").pack(side="left")
        ttk.Radiobutton(price_options, text="手工锁定价格", variable=self.pricing_mode_var, value="manual").pack(side="left", padx=(12, 0))
        ttk.Combobox(price_options, textvariable=self.price_currency_var, values=("USD", "CNY", "EUR", "LOCAL"), width=8).pack(side="left", padx=(12, 0))
        tk.Label(price_options, textvariable=self.pricing_source_var, background=SURFACE_MUTED, foreground=SUBTLE, font=(FONT, 8)).pack(side="right")
        ttk.Checkbutton(policy, text="每次数据外发前要求确认", variable=self.require_confirmation_var).grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 10))

        actions = tk.Frame(config, background=SURFACE)
        actions.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        tk.Label(actions, textvariable=self.status_var, background=SURFACE, foreground=MUTED, font=(MONO_FONT, 8)).pack(side="left")
        self.test_button = ttk.Button(actions, text="测试连接", style="Secondary.TButton", command=self.test_connection)
        self.test_button.pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="保存设置", style="Primary.TButton", command=self.save).pack(side="right")

        catalog_outer, catalog = _card(catalog_scroll.body)
        catalog_outer.grid(row=0, column=0, sticky="ew")
        heading = tk.Frame(catalog, background=SURFACE)
        heading.pack(fill="x", pady=(0, 14))
        _section_title(heading, "主流模型推荐", "价格为官方公开页快照，地区、缓存、批处理和促销会影响实际账单。")
        update_box = tk.Frame(catalog, background=SURFACE_MUTED)
        update_box.pack(fill="x", pady=(0, 12))
        update_box.columnconfigure(0, weight=1)
        tk.Label(update_box, text="远程目录地址（可留空）", background=SURFACE_MUTED, foreground=TEXT, font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(9, 3))
        ttk.Entry(update_box, textvariable=self.catalog_url_var, style="App.TEntry").grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        ttk.Checkbutton(update_box, text="自动检查", variable=self.catalog_auto_var).grid(row=0, column=1, sticky="e", padx=(8, 2), pady=(9, 3))
        ttk.Combobox(update_box, textvariable=self.catalog_interval_var, values=("1", "3", "7", "14", "30", "90"), width=5).grid(row=0, column=2, sticky="e", padx=2, pady=(9, 3))
        tk.Label(update_box, text="天", background=SURFACE_MUTED, foreground=MUTED, font=(FONT, 8)).grid(row=0, column=3, sticky="w", padx=(0, 10), pady=(9, 3))
        tk.Label(update_box, textvariable=self.catalog_status_var, background=SURFACE_MUTED, foreground=MUTED, font=(MONO_FONT, 8), anchor="w").grid(row=2, column=0, sticky="w", padx=10, pady=(0, 9))
        ttk.Button(update_box, text="从本地 JSON 导入", style="Secondary.TButton", command=self.import_catalog).grid(row=1, column=1, columnspan=2, sticky="e", padx=4, pady=(0, 8))
        self.catalog_refresh_button = ttk.Button(update_box, text="检查更新", style="Primary.TButton", command=self.refresh_catalog)
        self.catalog_refresh_button.grid(row=1, column=3, sticky="e", padx=(4, 10), pady=(0, 8))

        filters = tk.Frame(catalog, background=SURFACE)
        filters.pack(fill="x", pady=(0, 8))
        tk.Label(filters, text="筛选", background=SURFACE, foreground=TEXT, font=(FONT, 8, "bold")).pack(side="left")
        filter_entry = ttk.Entry(filters, textvariable=self.catalog_filter_var, style="App.TEntry", width=30)
        filter_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Combobox(
            filters,
            textvariable=self.catalog_price_filter_var,
            values=("全部价格", "可计算费用", "价格待核验", "本地免费"),
            state="readonly",
            width=13,
        ).pack(side="right")
        self.catalog_filter_var.trace_add("write", lambda *_args: self._refresh_catalog_tree())
        self.catalog_price_filter_var.trace_add("write", lambda *_args: self._refresh_catalog_tree())

        columns = ("provider", "model", "price", "freshness", "use")
        self.tree = ttk.Treeview(
            catalog,
            columns=columns,
            show="headings",
            height=8,
            style="Settings.Treeview",
        )
        for key, text, width in (
            ("provider", "服务商", 115),
            ("model", "模型名称", 165),
            ("price", "收费情况", 285),
            ("freshness", "价格状态", 100),
            ("use", "推荐场景", 225),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="x")
        self._refresh_catalog_tree()
        self.tree.bind("<<TreeviewSelect>>", self._select_catalog_entry)
        details = tk.Frame(catalog, background=SURFACE_MUTED)
        details.pack(fill="x", pady=(12, 0))
        tk.Label(
            details,
            textvariable=self.detail_var,
            background=SURFACE_MUTED,
            foreground=MUTED,
            font=(MONO_FONT, 8),
            justify="left",
            anchor="w",
            padx=12,
            pady=10,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(details, text="模型说明", style="Secondary.TButton", command=self.open_model_url).pack(side="right", padx=6, pady=6)
        ttk.Button(details, text="官方价格", style="Secondary.TButton", command=self.open_pricing_url).pack(side="right", pady=6)

    def _catalog_status_text(self) -> str:
        state = self.catalog_service.state()
        failure = str(state.get("last_error") or "").strip()
        suffix = f"；最近失败：{failure}" if failure else ""
        return (
            f"{self.catalog_document.source} · 版本 {self.catalog_document.catalog_version} · "
            f"更新 {self.catalog_document.updated_at}{suffix}"
        )

    def _freshness_label(self, entry: ModelCatalogEntry) -> str:
        try:
            verified = date.fromisoformat(entry.last_verified[:10])
            age = max(0, (date.today() - verified).days)
        except ValueError:
            return "日期异常"
        try:
            interval = max(1, int(self.catalog_interval_var.get()))
        except ValueError:
            interval = 7
        if age == 0:
            return "今日核验"
        if age <= interval:
            return f"{age} 天前"
        return f"已过期 {age} 天"

    def _refresh_catalog_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.catalog_by_iid.clear()
        query = self.catalog_filter_var.get().strip().lower()
        price_filter = self.catalog_price_filter_var.get()
        visible = []
        for entry in self.catalog:
            haystack = " ".join((entry.provider_name, entry.model, entry.recommendation, *entry.capability_tags)).lower()
            calculable = entry.input_price_per_million is not None and entry.output_price_per_million is not None
            if query and query not in haystack:
                continue
            if price_filter == "可计算费用" and not calculable:
                continue
            if price_filter == "价格待核验" and calculable:
                continue
            if price_filter == "本地免费" and not (calculable and entry.input_price_per_million == 0 and entry.output_price_per_million == 0):
                continue
            visible.append(entry)
        for index, entry in enumerate(visible):
            iid = f"model-{index}"
            self.catalog_by_iid[iid] = entry
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    entry.provider_name,
                    entry.model,
                    entry.pricing_summary,
                    self._freshness_label(entry),
                    entry.recommendation,
                ),
            )

    def _catalog_preferences(self) -> tuple[str, int]:
        url = self.catalog_url_var.get().strip()
        if url:
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("远程模型目录必须使用有效的 HTTPS 地址。")
        try:
            interval = int(self.catalog_interval_var.get())
        except ValueError as error:
            raise ValueError("目录更新周期必须是 1–365 天的整数。") from error
        if not 1 <= interval <= 365:
            raise ValueError("目录更新周期必须是 1–365 天的整数。")
        return url, interval

    def _save_catalog_preferences(self) -> tuple[str, int]:
        url, interval = self._catalog_preferences()
        self.settings.model_catalog = replace(
            self.settings.model_catalog,
            update_url=url,
            auto_check=self.catalog_auto_var.get(),
            update_interval_days=interval,
        )
        return url, interval

    @staticmethod
    def _price_text(value: float | None) -> str:
        return "" if value is None else f"{value:g}"

    def _apply_entry_to_variables(self, entry: ModelCatalogEntry, *, include_identity: bool = True) -> None:
        if include_identity:
            self.provider_id_var.set(entry.provider_id)
            self.provider_name_var.set(entry.provider_name)
            self.api_base_var.set(entry.api_base)
            self.model_var.set(entry.model)
            self.protocol_var.set(entry.protocol)
        self.input_price_var.set(self._price_text(entry.input_price_per_million))
        self.output_price_var.set(self._price_text(entry.output_price_per_million))
        self.cached_input_price_var.set(self._price_text(entry.cached_input_price_per_million))
        self.price_currency_var.set(entry.price_currency)
        self.pricing_mode_var.set("catalog")
        self.pricing_source_var.set(f"{self.catalog_document.source} · {entry.last_verified}")
        if entry.context_window_tokens is not None:
            self.context_tokens_var.set(str(entry.context_window_tokens))
        if entry.max_output_tokens is not None:
            self.max_tokens_var.set(str(entry.max_output_tokens))

    def _complete_catalog_update(self, result: CatalogUpdateResult) -> None:
        self.catalog_document = result.document
        self.catalog = list(result.document.models)
        self._refresh_catalog_tree()
        active = self.catalog_document.find(self.provider_id_var.get(), self.model_var.get())
        fallback_warning = ""
        if active is not None and self.pricing_mode_var.get() == "catalog":
            self._apply_entry_to_variables(active, include_identity=False)
            self.settings.model = self._current_model_settings()
        elif active is None and self.pricing_mode_var.get() == "catalog":
            self.pricing_mode_var.set("manual")
            self.pricing_source_var.set("新目录未包含当前模型，旧价格已手工锁定")
            self.settings.model = self._current_model_settings()
            fallback_warning = " 当前模型不在新目录中，已保留旧价并切换为手工锁定。"
        try:
            self._save_catalog_preferences()
            self.settings_store.save(self.settings)
        except (OSError, ValueError) as error:
            self.catalog_status_var.set(f"目录已载入，但设置保存失败：{error}")
            return
        self.catalog_status_var.set(result.message + f" 当前来源：{self.catalog_document.source}。" + fallback_warning)

    def import_catalog(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="导入模型推荐目录",
            filetypes=(("JSON 目录", "*.json"), ("所有文件", "*.*")),
        )
        if not selected:
            return
        try:
            result = self.catalog_service.import_file(Path(selected))
        except (OSError, ValueError) as error:
            self.catalog_status_var.set(f"导入失败，继续使用原目录：{error}")
            return
        self._complete_catalog_update(result)

    def refresh_catalog(self) -> None:
        try:
            url, _interval = self._save_catalog_preferences()
            self.settings_store.save(self.settings)
        except (OSError, ValueError) as error:
            self.catalog_status_var.set(str(error))
            return
        if not url:
            self.catalog_status_var.set("尚未配置远程 HTTPS 地址；可先使用“从本地 JSON 导入”。")
            return
        self.run_async(
            self.catalog_refresh_button,
            self.catalog_status_var,
            "正在后台检查模型价格目录…",
            lambda: self.catalog_service.update_from_url(url, timeout=min(30, max(5, int(self.timeout_var.get())))),
            self._complete_catalog_update,
        )

    def _auto_update_if_due(self) -> None:
        self._catalog_update_job = None
        try:
            url, interval = self._catalog_preferences()
        except ValueError:
            return
        if self.catalog_auto_var.get() and url and self.catalog_service.should_check(interval):
            self.refresh_catalog()

    def _cancel_catalog_update_job(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self or self._catalog_update_job is None:
            return
        try:
            self.after_cancel(self._catalog_update_job)
        except tk.TclError:
            pass
        self._catalog_update_job = None

    def _selected_catalog_entry(self) -> ModelCatalogEntry | None:
        selection = self.tree.selection()
        return self.catalog_by_iid.get(selection[0]) if selection else None

    def _select_catalog_entry(self, _event: tk.Event[tk.Misc]) -> None:
        entry = self._selected_catalog_entry()
        if not entry:
            return
        self._apply_entry_to_variables(entry)
        tags = "、".join(entry.capability_tags) or "未标注"
        notes = f"；{entry.pricing_notes}" if entry.pricing_notes else ""
        self.detail_var.set(
            f"{entry.provider_name} · {entry.model} · 能力：{tags}\n"
            f"{entry.recommendation} · 价格核验：{entry.last_verified}{notes}"
        )

    def open_model_url(self) -> None:
        entry = self._selected_catalog_entry()
        if entry:
            webbrowser.open(entry.model_url)

    def open_pricing_url(self) -> None:
        entry = self._selected_catalog_entry()
        if entry:
            webbrowser.open(entry.pricing_url)

    def _current_model_settings(self) -> ModelSettings:
        try:
            timeout = max(5, int(self.timeout_var.get()))
            temperature = min(2.0, max(0.0, float(self.temperature_var.get())))
            maximum = max(256, int(self.max_tokens_var.get()))
            context_window = max(1024, int(self.context_tokens_var.get()))
            input_price = float(self.input_price_var.get()) if self.input_price_var.get().strip() else None
            output_price = float(self.output_price_var.get()) if self.output_price_var.get().strip() else None
            cached_input_price = float(self.cached_input_price_var.get()) if self.cached_input_price_var.get().strip() else None
        except ValueError as error:
            raise ValueError("超时、温度、Token 限额和价格必须是有效数字。") from error
        if any(value is not None and value < 0 for value in (input_price, output_price, cached_input_price)):
            raise ValueError("模型价格不能为负数。")
        pricing_mode = self.pricing_mode_var.get() if self.pricing_mode_var.get() in {"catalog", "manual"} else "manual"
        catalog_entry = self.catalog_document.find(self.provider_id_var.get(), self.model_var.get())
        if pricing_mode == "catalog" and catalog_entry is None:
            raise ValueError("当前自定义模型不在推荐目录中；请将价格模式切换为“手工锁定价格”。")
        if pricing_mode == "catalog" and catalog_entry is not None:
            input_price = catalog_entry.input_price_per_million
            output_price = catalog_entry.output_price_per_million
            cached_input_price = catalog_entry.cached_input_price_per_million
            self.input_price_var.set(self._price_text(input_price))
            self.output_price_var.set(self._price_text(output_price))
            self.cached_input_price_var.set(self._price_text(cached_input_price))
            self.price_currency_var.set(catalog_entry.price_currency)
        price_tiers = list(catalog_entry.price_tiers) if pricing_mode == "catalog" and catalog_entry else []
        pricing_key = catalog_entry.catalog_key if pricing_mode == "catalog" and catalog_entry else ""
        pricing_updated_at = catalog_entry.last_verified if pricing_mode == "catalog" and catalog_entry else datetime.now().astimezone().replace(microsecond=0).isoformat()
        if pricing_mode == "catalog" and catalog_entry:
            pricing_source = self.catalog_document.source
        elif self.pricing_source_var.get().startswith("新目录未包含"):
            pricing_source = self.pricing_source_var.get()
        else:
            pricing_source = "用户手工锁定"
        return ModelSettings(
            provider_id=self.provider_id_var.get().strip() or "custom",
            provider_name=self.provider_name_var.get().strip() or "自定义服务商",
            api_base=self.api_base_var.get().strip(),
            model=self.model_var.get().strip(),
            protocol=self.protocol_var.get().strip(),
            timeout_seconds=timeout,
            temperature=temperature,
            max_output_tokens=maximum,
            persist_api_key=self.persist_var.get(),
            context_window_tokens=context_window,
            maximum_data_class=self.data_class_var.get(),
            require_external_confirmation=self.require_confirmation_var.get(),
            input_price_per_million=input_price,
            output_price_per_million=output_price,
            cached_input_price_per_million=cached_input_price,
            price_currency=self.price_currency_var.get().strip().upper() or "USD",
            price_tiers=price_tiers,
            pricing_mode=pricing_mode,
            pricing_catalog_key=pricing_key,
            pricing_updated_at=pricing_updated_at,
            pricing_source=pricing_source,
        )

    def _api_key(self) -> str | None:
        return self.api_key_var.get().strip() or self.secret_store.get("model.api_key")

    def save(self) -> bool:
        try:
            model = self._current_model_settings()
            self._save_catalog_preferences()
            key = self.api_key_var.get().strip()
            if key:
                self.secret_store.set("model.api_key", key, persist=self.persist_var.get())
            self.settings.model = model
            self.settings_store.save(self.settings)
        except (OSError, ValueError, SecretStoreError) as error:
            messagebox.showerror("无法保存大模型设置", str(error), parent=self)
            return False
        self.api_key_var.set("")
        self.pricing_source_var.set(model.pricing_source)
        self.status_var.set("设置已保存" + ("，API Key 已加密" if self.secret_store.has("model.api_key") else ""))
        self.on_saved()
        return True

    def test_connection(self) -> None:
        try:
            model = self._current_model_settings()
            client = LLMClient(model, self._api_key())
        except (ValueError, LLMRequestError) as error:
            self.status_var.set(str(error))
            return
        self.run_async(
            self.test_button,
            self.status_var,
            "正在发送最小测试请求…",
            client.test_connection,
            lambda message: self.status_var.set(str(message)),
        )


class DatabaseSettingsView(_BaseSettingsView):
    def __init__(
        self,
        master: tk.Widget,
        *,
        settings_store: SettingsStore,
        settings: AppSettings,
        secret_store: SecretStore,
        on_back: Callable[[], None],
    ) -> None:
        super().__init__(master, on_back=on_back)
        self.settings_store = settings_store
        self.settings = settings
        self.secret_store = secret_store
        self._ima_kb_map: dict[str, str] = {}
        content = self.build_header(
            "数据库设置",
            "配置公开检索、机构资源、Zotero 本地文献库与 IMA 共享知识库。",
            scrollable=False,
        )
        assert isinstance(content, tk.Frame)
        body = content
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(body, style="SubBookmark.TNotebook")
        self.notebook.grid(row=0, column=0, sticky="nsew", pady=(0, 18))
        builders = (
            ("search", "公开检索", self._build_discovery),
            ("library", "机构资源", self._build_library),
            ("database", "Zotero", self._build_zotero),
            ("knowledge", "IMA 知识库", self._build_ima),
        )
        self.tab_frames: dict[str, tk.Frame] = {}
        for icon, label, builder in builders:
            tab = tk.Frame(self.notebook, background=BACKGROUND)
            self.notebook.add(
                tab,
                text=label,
                image=_icon(self, icon),
                compound="left",
            )
            inner_scroll = _Scroll(tab)
            inner_scroll.pack(fill="both", expand=True, padx=12, pady=12)
            inner_scroll.body.columnconfigure(0, weight=1)
            builder(inner_scroll.body)
            self.tab_frames[label] = tab

    def _build_discovery(self, body: tk.Frame) -> None:
        config = self.settings.discovery
        self.openalex_enabled_var = tk.BooleanVar(value=config.openalex_enabled)
        self.crossref_enabled_var = tk.BooleanVar(value=config.crossref_enabled)
        self.discovery_email_var = tk.StringVar(value=config.polite_email)
        self.discovery_limit_var = tk.StringVar(value=str(config.default_limit))
        self.discovery_timeout_var = tk.StringVar(value=str(config.timeout_seconds))
        self.discovery_status_var = tk.StringVar(value="公开元数据源不会在应用启动时联网")

        outer, card = _card(body)
        outer.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        heading = tk.Frame(card, background=SURFACE)
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        _section_title(
            heading,
            "公开学术元数据检索",
            "OpenAlex 用于跨学科发现，Crossref 用于 DOI 与出版元数据交叉核验；仅在执行检索时联网。",
        )
        _label(card, "礼貌池联系邮箱（建议填写）", row=1, column=0)
        _label(card, "每个来源默认返回上限（1—100）", row=1, column=1)
        _entry(card, self.discovery_email_var, row=2, column=0)
        limit_entry = _entry(card, self.discovery_limit_var, row=2, column=1)
        limit_entry.grid_configure(padx=(10, 0))
        _label(card, "请求超时（秒，5—120）", row=3, span=2)
        _entry(card, self.discovery_timeout_var, row=4, span=2)
        options = tk.Frame(card, background=SURFACE)
        options.grid(row=5, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(options, text="启用 OpenAlex", variable=self.openalex_enabled_var).pack(side="left")
        ttk.Checkbutton(options, text="启用 Crossref", variable=self.crossref_enabled_var).pack(side="left", padx=(16, 0))
        actions = tk.Frame(card, background=SURFACE)
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(17, 0))
        tk.Label(
            actions,
            textvariable=self.discovery_status_var,
            background=SURFACE,
            foreground=MUTED,
            font=(MONO_FONT, 8),
        ).pack(side="left")
        self.discovery_save_button = ttk.Button(
            actions,
            text="保存公开检索设置",
            style="Primary.TButton",
            command=self.save_discovery,
        )
        self.discovery_save_button.pack(side="right")

    def _discovery_settings(self) -> DiscoverySettings:
        try:
            limit = int(self.discovery_limit_var.get())
            timeout = int(self.discovery_timeout_var.get())
        except ValueError as error:
            raise ValueError("返回上限和请求超时必须是整数。") from error
        if not 1 <= limit <= 100:
            raise ValueError("每个来源返回上限应在 1—100 之间。")
        if not 5 <= timeout <= 120:
            raise ValueError("请求超时应在 5—120 秒之间。")
        return DiscoverySettings(
            openalex_enabled=self.openalex_enabled_var.get(),
            crossref_enabled=self.crossref_enabled_var.get(),
            polite_email=self.discovery_email_var.get().strip(),
            default_limit=limit,
            timeout_seconds=timeout,
        )

    def save_discovery(self) -> None:
        try:
            self.settings.discovery = self._discovery_settings()
            self.settings_store.save(self.settings)
        except (ValueError, OSError) as error:
            self.discovery_status_var.set(str(error))
            return
        self.discovery_status_var.set("已保存；实际检索将在执行工作台中由用户触发")

    def _build_library(self, body: tk.Frame) -> None:
        config = self.settings.library
        self.library_enabled_var = tk.BooleanVar(value=config.enabled)
        self.portal_var = tk.StringVar(value=config.portal_url)
        self.wos_var = tk.StringVar(value=config.web_of_science_url)
        self.cnki_var = tk.StringVar(value=config.cnki_url)
        self.cdp_var = tk.StringVar(value=config.cdp_proxy_url)
        self.download_var = tk.StringVar(value=config.download_directory)
        self.batch_var = tk.StringVar(value=str(config.max_batch_size))
        self.pdf_only_var = tk.BooleanVar(value=config.pdf_only)
        self.si_var = tk.BooleanVar(value=config.include_supporting_information)
        self.library_status_var = tk.StringVar(value="尚未测试")

        outer, card = _card(body)
        outer.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        heading = tk.Frame(card, background=SURFACE)
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        _section_title(heading, "机构图书馆与学术数据库", "从实际电子资源入口识别 CAS、CARSI、EZproxy 或 WebVPN；不保存密码、Cookie 或验证码。")
        _label(card, "实际使用的图书馆电子资源入口 URL", row=1, span=2)
        _entry(card, self.portal_var, row=2, span=2)
        _label(card, "Web of Science 入口（可选）", row=3, column=0)
        _label(card, "CNKI 入口（可选）", row=3, column=1)
        _entry(card, self.wos_var, row=4, column=0)
        entry = _entry(card, self.cnki_var, row=4, column=1)
        entry.grid_configure(padx=(10, 0))
        _label(card, "浏览器控制地址", row=5, column=0)
        _label(card, "普通批次上限", row=5, column=1)
        _entry(card, self.cdp_var, row=6, column=0)
        batch_entry = _entry(card, self.batch_var, row=6, column=1)
        batch_entry.grid_configure(padx=(10, 0))
        _label(card, "后续下载目录（可选）", row=7, span=2)
        directory = tk.Frame(card, background=SURFACE)
        directory.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(0, 13))
        directory.columnconfigure(0, weight=1)
        ttk.Entry(directory, textvariable=self.download_var, style="App.TEntry").grid(row=0, column=0, sticky="ew")
        ttk.Button(directory, text="选择…", style="Secondary.TButton", command=self.choose_download_directory).grid(row=0, column=1, padx=(8, 0))
        options = tk.Frame(card, background=SURFACE)
        options.grid(row=9, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(options, text="启用机构资源", variable=self.library_enabled_var).pack(side="left")
        ttk.Checkbutton(options, text="仅接受真实 PDF", variable=self.pdf_only_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(options, text="下载补充材料（默认关闭）", variable=self.si_var).pack(side="left", padx=(16, 0))
        actions = tk.Frame(card, background=SURFACE)
        actions.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(17, 0))
        tk.Label(actions, textvariable=self.library_status_var, background=SURFACE, foreground=MUTED, font=(MONO_FONT, 8), wraplength=620, justify="left").pack(side="left")
        self.library_test_button = ttk.Button(actions, text="保存并测试", style="Primary.TButton", command=self.test_library)
        self.library_test_button.pack(side="right")

    def choose_download_directory(self) -> None:
        selected = filedialog.askdirectory(parent=self)
        if selected:
            self.download_var.set(selected)

    def _library_settings(self) -> LibrarySettings:
        try:
            batch = int(self.batch_var.get())
        except ValueError as error:
            raise ValueError("普通批次上限必须是整数。") from error
        if not 1 <= batch <= 20:
            raise ValueError("普通批次上限应在 1—20 之间。")
        return LibrarySettings(
            enabled=self.library_enabled_var.get(),
            portal_url=self.portal_var.get().strip(),
            web_of_science_url=self.wos_var.get().strip(),
            cnki_url=self.cnki_var.get().strip(),
            cdp_proxy_url=self.cdp_var.get().strip(),
            download_directory=self.download_var.get().strip(),
            max_batch_size=batch,
            pdf_only=self.pdf_only_var.get(),
            include_supporting_information=self.si_var.get(),
        )

    def test_library(self) -> None:
        try:
            config = self._library_settings()
            self.settings.library = config
            self.settings_store.save(self.settings)
        except (ValueError, OSError) as error:
            self.library_status_var.set(str(error))
            return
        connector = LibraryConnector(config)
        self.run_async(
            self.library_test_button,
            self.library_status_var,
            "正在检查入口、Node.js 和浏览器控制…",
            connector.check,
            lambda result: self.library_status_var.set(result.message),
        )

    def _build_zotero(self, body: tk.Frame) -> None:
        config = self.settings.zotero
        self.zotero_enabled_var = tk.BooleanVar(value=config.enabled)
        self.zotero_base_var = tk.StringVar(value=config.base_url)
        self.zotero_collection_var = tk.StringVar(value=config.collection_filter)
        self.zotero_tag_var = tk.StringVar(value=config.tag_filter)
        self.zotero_attachments_var = tk.BooleanVar(value=config.inspect_attachments)
        self.zotero_write_var = tk.BooleanVar(value=config.allow_confirmed_writes)
        self.zotero_query_var = tk.StringVar()
        self.zotero_status_var = tk.StringVar(value="尚未测试")

        outer, card = _card(body)
        outer.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        heading = tk.Frame(card, background=SURFACE)
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        _section_title(heading, "Zotero 本地文献库", "默认只读。优先复用已收藏元数据和附件，不执行导入、修改或删除。")
        _label(card, "本地 API 地址", row=1, span=2)
        _entry(card, self.zotero_base_var, row=2, span=2)
        _label(card, "Collection 名称筛选（可选）", row=3, column=0)
        _label(card, "Tag 精确筛选（可选）", row=3, column=1)
        _entry(card, self.zotero_collection_var, row=4, column=0)
        tag_entry = _entry(card, self.zotero_tag_var, row=4, column=1)
        tag_entry.grid_configure(padx=(10, 0))
        options = tk.Frame(card, background=SURFACE)
        options.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Checkbutton(options, text="启用 Zotero", variable=self.zotero_enabled_var).pack(side="left")
        ttk.Checkbutton(options, text="检查附件数量", variable=self.zotero_attachments_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(options, text="允许预览后确认写入标签/笔记", variable=self.zotero_write_var).pack(side="left", padx=(16, 0))
        search = tk.Frame(card, background=SURFACE_MUTED)
        search.grid(row=6, column=0, columnspan=2, sticky="ew")
        search.columnconfigure(0, weight=1)
        ttk.Entry(search, textvariable=self.zotero_query_var, style="App.TEntry").grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        self.zotero_preview_button = ttk.Button(search, text="搜索预览", style="Secondary.TButton", command=self.preview_zotero)
        self.zotero_preview_button.grid(row=0, column=1, padx=(0, 10), pady=10)
        self.zotero_results = tk.Text(search, height=8, wrap="word", state="disabled", font=(FONT, 9), background=TEXT_AREA, relief="flat", padx=10, pady=8)
        self.zotero_results.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        actions = tk.Frame(card, background=SURFACE)
        actions.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        tk.Label(actions, textvariable=self.zotero_status_var, background=SURFACE, foreground=MUTED, font=(MONO_FONT, 8)).pack(side="left")
        self.zotero_test_button = ttk.Button(actions, text="保存并测试", style="Primary.TButton", command=self.test_zotero)
        self.zotero_test_button.pack(side="right")

    def _zotero_settings(self) -> ZoteroSettings:
        return ZoteroSettings(
            enabled=self.zotero_enabled_var.get(),
            base_url=self.zotero_base_var.get().strip(),
            collection_filter=self.zotero_collection_var.get().strip(),
            tag_filter=self.zotero_tag_var.get().strip(),
            inspect_attachments=self.zotero_attachments_var.get(),
            allow_confirmed_writes=self.zotero_write_var.get(),
        )

    def test_zotero(self) -> None:
        config = self._zotero_settings()
        self.settings.zotero = config
        try:
            self.settings_store.save(self.settings)
        except OSError as error:
            self.zotero_status_var.set(str(error))
            return
        connector = ZoteroConnector(config)
        self.run_async(
            self.zotero_test_button,
            self.zotero_status_var,
            "正在检查 Zotero 本地 API…",
            connector.check,
            lambda result: self.zotero_status_var.set(result.message),
        )

    @staticmethod
    def _write_results(widget: tk.Text, items: list[Any], empty_message: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if not items:
            widget.insert("1.0", empty_message)
        else:
            widget.insert("1.0", "\n\n".join(f"{index}. {item.display_text()}" for index, item in enumerate(items, 1)))
        widget.configure(state="disabled")

    def preview_zotero(self) -> None:
        config = self._zotero_settings()
        connector = ZoteroConnector(config)
        self.run_async(
            self.zotero_preview_button,
            self.zotero_status_var,
            "正在只读搜索 Zotero…",
            lambda: connector.search_preview(self.zotero_query_var.get()),
            lambda items: (
                self._write_results(self.zotero_results, items, "没有匹配的 Zotero 条目。"),
                self.zotero_status_var.set(f"只读预览完成：{len(items)} 条"),
            ),
        )

    def _build_ima(self, body: tk.Frame) -> None:
        config = self.settings.ima
        self.ima_enabled_var = tk.BooleanVar(value=config.enabled)
        self.ima_client_var = tk.StringVar()
        self.ima_key_var = tk.StringVar()
        self.ima_persist_var = tk.BooleanVar(value=config.persist_credentials)
        self.ima_kb_var = tk.StringVar(value=config.knowledge_base_name)
        self.ima_query_var = tk.StringVar()
        self.ima_status_var = tk.StringVar(
            value="已保存凭证" if self.secret_store.has("ima.api_key") else "尚未配置凭证"
        )

        outer, card = _card(body)
        outer.grid(row=3, column=0, sticky="ew", pady=(0, 20))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        heading = tk.Frame(card, background=SURFACE)
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        _section_title(heading, "IMA 共享知识库", "只调用官方 OpenAPI。若购买的共享库不可见，将停止接入，不抓取 IMA 客户端。")
        _label(card, "Client ID（留空表示使用已保存值）", row=1, column=0)
        _label(card, "API Key（留空表示使用已保存值）", row=1, column=1)
        _entry(card, self.ima_client_var, row=2, column=0, show="•")
        key_entry = _entry(card, self.ima_key_var, row=2, column=1, show="•")
        key_entry.grid_configure(padx=(10, 0))
        _label(card, "通过官方 OpenAPI 可见的知识库", row=3, span=2)
        self.ima_kb_box = ttk.Combobox(card, textvariable=self.ima_kb_var, state="readonly", style="App.TCombobox")
        self.ima_kb_box.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 13))
        self.ima_kb_box.bind("<<ComboboxSelected>>", self._save_ima_selection)
        options = tk.Frame(card, background=SURFACE)
        options.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Checkbutton(options, text="启用 IMA", variable=self.ima_enabled_var).pack(side="left")
        ttk.Checkbutton(options, text="使用 DPAPI 持久保存凭证", variable=self.ima_persist_var).pack(side="left", padx=(16, 0))
        search = tk.Frame(card, background=SURFACE_MUTED)
        search.grid(row=6, column=0, columnspan=2, sticky="ew")
        search.columnconfigure(0, weight=1)
        ttk.Entry(search, textvariable=self.ima_query_var, style="App.TEntry").grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        self.ima_preview_button = ttk.Button(search, text="搜索预览", style="Secondary.TButton", command=self.preview_ima)
        self.ima_preview_button.grid(row=0, column=1, padx=(0, 10), pady=10)
        self.ima_results = tk.Text(search, height=8, wrap="word", state="disabled", font=(FONT, 9), background=TEXT_AREA, relief="flat", padx=10, pady=8)
        self.ima_results.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        actions = tk.Frame(card, background=SURFACE)
        actions.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        tk.Label(actions, textvariable=self.ima_status_var, background=SURFACE, foreground=MUTED, font=(MONO_FONT, 8), wraplength=620, justify="left").pack(side="left")
        self.ima_test_button = ttk.Button(actions, text="保存凭证并探测", style="Primary.TButton", command=self.test_ima)
        self.ima_test_button.pack(side="right")

    def _ima_credentials(self) -> tuple[str | None, str | None]:
        client_id = self.ima_client_var.get().strip() or self.secret_store.get("ima.client_id")
        api_key = self.ima_key_var.get().strip() or self.secret_store.get("ima.api_key")
        return client_id, api_key

    def _save_ima_settings(self) -> ImaSettings:
        selected_name = self.ima_kb_var.get().strip()
        knowledge_base_id = self._ima_kb_map.get(selected_name, self.settings.ima.knowledge_base_id)
        config = ImaSettings(
            enabled=self.ima_enabled_var.get(),
            api_base="https://ima.qq.com",
            knowledge_base_id=knowledge_base_id,
            knowledge_base_name=selected_name,
            persist_credentials=self.ima_persist_var.get(),
        )
        client_id = self.ima_client_var.get().strip()
        api_key = self.ima_key_var.get().strip()
        if client_id:
            self.secret_store.set("ima.client_id", client_id, persist=self.ima_persist_var.get())
        if api_key:
            self.secret_store.set("ima.api_key", api_key, persist=self.ima_persist_var.get())
        self.settings.ima = config
        self.settings_store.save(self.settings)
        self.ima_client_var.set("")
        self.ima_key_var.set("")
        return config

    def test_ima(self) -> None:
        try:
            config = self._save_ima_settings()
        except (OSError, SecretStoreError) as error:
            self.ima_status_var.set(str(error))
            return
        client_id, api_key = self._ima_credentials()
        connector = ImaConnector(config, client_id=client_id, api_key=api_key)

        def complete(result: Any) -> None:
            knowledge_bases = result.details.get("knowledge_bases", [])
            self._ima_kb_map = {item["name"]: item["id"] for item in knowledge_bases}
            names = list(self._ima_kb_map)
            self.ima_kb_box.configure(values=names)
            if names and self.ima_kb_var.get() not in names:
                self.ima_kb_var.set(names[0])
            self._save_ima_selection()
            self.ima_status_var.set(result.message)

        self.run_async(
            self.ima_test_button,
            self.ima_status_var,
            "正在通过官方 OpenAPI 探测可见知识库…",
            connector.check,
            complete,
        )

    def _save_ima_selection(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        selected_name = self.ima_kb_var.get().strip()
        selected_id = self._ima_kb_map.get(selected_name)
        if not selected_name or not selected_id:
            return
        self.settings.ima = replace(
            self.settings.ima,
            knowledge_base_id=selected_id,
            knowledge_base_name=selected_name,
        )
        try:
            self.settings_store.save(self.settings)
        except OSError:
            return

    def preview_ima(self) -> None:
        try:
            config = self._save_ima_settings()
        except (OSError, SecretStoreError) as error:
            self.ima_status_var.set(str(error))
            return
        client_id, api_key = self._ima_credentials()
        connector = ImaConnector(config, client_id=client_id, api_key=api_key)
        self.run_async(
            self.ima_preview_button,
            self.ima_status_var,
            "正在只读搜索 IMA 共享知识库…",
            lambda: connector.search_preview(self.ima_query_var.get()),
            lambda items: (
                self._write_results(self.ima_results, items, "没有匹配的 IMA 知识条目。"),
                self.ima_status_var.set(f"只读预览完成：{len(items)} 条；内部 ID 未显示"),
            ),
        )


class HealthSettingsView(_BaseSettingsView):
    """One-click health dashboard for databases, local tools, and the model."""

    def __init__(
        self,
        master: tk.Widget,
        *,
        settings_store: SettingsStore,
        settings: AppSettings,
        secret_store: SecretStore,
        on_back: Callable[[], None],
    ) -> None:
        super().__init__(master, on_back=on_back)
        self.settings_store = settings_store
        self.settings = settings
        self.secret_store = secret_store
        self.status_var = tk.StringVar(value="点击“运行健康检查”测试当前配置；结果会保留最近失败原因。")
        content = self.build_header("系统健康检查", "集中检查数据库、Zotero、IMA、模型接口与本地解析工具。", scrollable=False)
        assert isinstance(content, tk.Frame)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        actions = tk.Frame(
            content,
            background=PIXEL_PANEL,
            highlightbackground=PIXEL_GRID,
            highlightthickness=1,
        )
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        tk.Label(
            actions,
            textvariable=self.status_var,
            background=PIXEL_PANEL,
            foreground=PIXEL_MUTED,
            font=(MONO_FONT, 9),
            padx=12,
            pady=10,
        ).pack(side="left")
        self.run_button = ttk.Button(
            actions,
            text="运行健康检查",
            image=_icon(self, "health", "white"),
            compound="left",
            style="Primary.TButton",
            command=self.run_checks,
        )
        self.run_button.pack(side="right", padx=8, pady=7)
        outer, body = _card(content)
        outer.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        columns = ("component", "status", "latency", "message")
        self.tree = ttk.Treeview(
            body,
            columns=columns,
            show="headings",
            style="Settings.Treeview",
        )
        for key, label, width in (("component", "组件", 170), ("status", "状态", 120), ("latency", "延迟", 80), ("message", "说明/最近失败", 520)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=key == "message")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = AutoHideScrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._load_history()

    def _registry(self) -> Any:
        from .health import HealthRegistry, HealthResult, local_runtime_probes
        from .search_engine import CrossrefProvider, OpenAlexProvider

        registry = HealthRegistry(self.settings_store.path.parent / "health.json")
        registry.register("OpenAlex", lambda: bool(OpenAlexProvider(timeout=min(8, self.settings.discovery.timeout_seconds)).search("test", limit=1)))
        registry.register("Crossref", lambda: bool(CrossrefProvider(timeout=min(8, self.settings.discovery.timeout_seconds)).search("test", limit=1)))
        registry.register("机构资源", lambda: LibraryConnector(self.settings.library).check() if self.settings.library.enabled else HealthResult("机构资源", True, "disabled", "未启用（不影响公开数据库检索）"))
        registry.register("Zotero", lambda: ZoteroConnector(self.settings.zotero).check() if self.settings.zotero.enabled else HealthResult("Zotero", True, "disabled", "未启用"))
        registry.register("IMA", lambda: ImaConnector(self.settings.ima, client_id=self.secret_store.get("ima.client_id"), api_key=self.secret_store.get("ima.api_key")).check() if self.settings.ima.enabled else HealthResult("IMA", True, "disabled", "未启用"))
        registry.register("大模型", lambda: LLMClient(self.settings.model, self.secret_store.get("model.api_key")).test_connection())
        for name, probe in local_runtime_probes().items():
            registry.register(name, probe)
        return registry

    def _display(self, results: list[Any]) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(results):
            self.tree.insert("", "end", iid=f"health-{index}", values=(item.component, f"{'可用' if item.ok else '失败'} / {item.status}", f"{item.latency_ms} ms" if item.latency_ms is not None else "—", item.message))
        failed = sum(not item.ok for item in results)
        self.status_var.set(f"检查完成：{len(results) - failed} 项可用，{failed} 项失败；失败原因已保存。")

    def _load_history(self) -> None:
        from .health import HealthRegistry, HealthResult

        payload = HealthRegistry(self.settings_store.path.parent / "health.json").load_history()
        latest = payload.get("latest", {}) if isinstance(payload, dict) else {}
        values = [HealthResult(**item) for item in latest.values() if isinstance(item, dict)]
        if values:
            self._display(values)

    def run_checks(self) -> None:
        registry = self._registry()
        self.run_async(self.run_button, self.status_var, "正在检查各组件；网络来源可能需要数秒…", registry.run, self._display)
