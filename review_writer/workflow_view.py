"""Tkinter execution workbench for research workflow stages 3 through 6."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from .generators import LLMClient
from .iconography import get_icon
from .integrations import ImaConnector, ZoteroConnector
from .markdown_view import MarkdownPreviewToggle
from .models import ResearchBrief
from .secret_store import SecretStore
from .settings import AppSettings, SettingsStore
from .theme import ThemePalette, get_palette
from .ui_utils import AutoHideScrollbar
from .workflow_models import EvidenceLevel, PaperRecord, ReadingNote, SearchStrategyBundle
from .workflow_store import WorkflowStore


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
OPS_BACKGROUND = "#122033"
OPS_PANEL = "#203651"
OPS_TEXT = "#f8fafc"
OPS_MUTED = "#aebdce"
OPS_GRID = "#263a50"


def apply_fonts(ui_font: str, mono_font: str) -> None:
    """Update module font families before widgets are created."""

    global FONT, MONO_FONT
    FONT = ui_font
    MONO_FONT = mono_font


def apply_palette(palette: ThemePalette) -> None:
    """Update module colors before new views are built or recolored."""

    global BACKGROUND, SURFACE, SURFACE_MUTED, BORDER, TEXT, MUTED, SUBTLE
    global PRIMARY, PRIMARY_PALE, SUCCESS, SUCCESS_PALE, WARNING, WARNING_PALE, TEXT_AREA
    global OPS_BACKGROUND, OPS_PANEL, OPS_TEXT, OPS_MUTED, OPS_GRID
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
    OPS_BACKGROUND = palette.sidebar
    OPS_PANEL = palette.sidebar_active
    OPS_TEXT = palette.sidebar_text
    OPS_MUTED = palette.sidebar_muted
    OPS_GRID = palette.sidebar_divider


def _card(parent: tk.Widget) -> tuple[tk.Frame, tk.Frame]:
    outer = tk.Frame(
        parent,
        background=SURFACE,
        highlightbackground=PRIMARY,
        highlightthickness=2,
        borderwidth=0,
    )
    inner = tk.Frame(outer, background=SURFACE)
    inner.pack(fill="both", expand=True, padx=18, pady=16)
    return outer, inner


def _icon(master: tk.Misc, name: str, role: str = "primary") -> tk.PhotoImage:
    colors = {
        "primary": PRIMARY,
        "text": TEXT,
        "muted": MUTED,
        "sidebar_text": OPS_TEXT,
        "white": "#ffffff",
    }
    return get_icon(master, name, colors[role], role=role)


def _set_text(widget: tk.Text, value: str, *, readonly: bool = False) -> None:
    previous_state = str(widget.cget("state"))
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value)
    if readonly or previous_state == "disabled":
        widget.configure(state="disabled")
    controller = getattr(widget, "_markdown_preview_controller", None)
    if controller is not None and controller.mode.get() == "preview":
        controller.refresh()


def _text(widget: tk.Text) -> str:
    return widget.get("1.0", "end-1c").strip()


class ExecutionWorkspace(tk.Frame):
    """A resumable, user-gated interface for stages 3, 4, 5, and 6."""

    def __init__(
        self,
        master: tk.Widget,
        *,
        settings_store: SettingsStore,
        settings: AppSettings,
        secret_store: SecretStore,
        on_back: Callable[[], None],
        on_database_settings: Callable[[], None],
        on_model_settings: Callable[[], None],
    ) -> None:
        super().__init__(master, background=BACKGROUND)
        self.settings_store = settings_store
        self.settings = settings
        self.secret_store = secret_store
        self.palette = get_palette(settings.appearance.theme_id, settings.appearance.custom_accent)
        self.ui_font = FONT
        self.mono_font = MONO_FONT
        self.markdown_views: dict[str, MarkdownPreviewToggle] = {}
        self.on_back = on_back
        self.on_database_settings = on_database_settings
        self.on_model_settings = on_model_settings

        self.project_directory: Path | None = None
        self.store: WorkflowStore | None = None
        self.brief: ResearchBrief | None = None
        self.confirmed_plan = ""
        self.strategy: SearchStrategyBundle | None = None
        self.papers: list[PaperRecord] = []
        self.reading_notes: list[ReadingNote] = []
        self.core_paper_ids: list[str] = []
        self.report_bundle: Any = None
        self.review_protocol: Any = None
        self.screening_decisions: list[Any] = []
        self.quality_assessments: dict[str, Any] = {}
        self.task_store: Any = None
        self.task_worker: Any = None
        self._task_poll_id: str | None = None

        self.project_title_var = tk.StringVar(value="尚未载入项目")
        self.workflow_status_var = tk.StringVar(value="请先确认或打开一个调研项目")
        self.strategy_status_var = tk.StringVar(value="待生成")
        self.search_status_var = tk.StringVar(value="等待检索式确认")
        self.reading_status_var = tk.StringVar(value="等待核心论文")
        self.report_status_var = tk.StringVar(value="等待精读确认")
        self.audit_status_var = tk.StringVar(value="等待文献总结报告")
        self.review_status_var = tk.StringVar(value="请选择调研模式并确认协议")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_header()
        self._build_notebook()
        self._sync_source_defaults()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self:
            return
        if self._task_poll_id is not None:
            try:
                self.after_cancel(self._task_poll_id)
            except tk.TclError:
                pass
            self._task_poll_id = None
        if self.task_worker is not None:
            self.task_worker.stop(timeout=1.0)

    def apply_visual_preferences(
        self,
        palette: ThemePalette,
        ui_font: str,
        mono_font: str,
    ) -> None:
        """Refresh Markdown previews after a live theme or font change."""

        self.palette = palette
        self.ui_font = ui_font
        self.mono_font = mono_font
        for view in self.markdown_views.values():
            view.configure_visuals(
                palette=palette,
                ui_font=ui_font,
                mono_font=mono_font,
            )

    # -- layout ---------------------------------------------------------

    def _build_header(self) -> None:
        header = tk.Frame(
            self,
            background=OPS_BACKGROUND,
            highlightbackground=PRIMARY,
            highlightthickness=2,
        )
        header.grid(row=0, column=0, sticky="ew", padx=30, pady=(22, 12))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="[ RESEARCH_OPS // EXECUTION_WORKSPACE ]",
            background=OPS_BACKGROUND,
            foreground=PRIMARY,
            font=(MONO_FONT, 9, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(12, 0))
        tk.Label(
            header,
            textvariable=self.project_title_var,
            background=OPS_BACKGROUND,
            foreground=OPS_TEXT,
            font=(MONO_FONT, 18, "bold"),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(5, 2))
        tk.Label(
            header,
            textvariable=self.workflow_status_var,
            background=OPS_BACKGROUND,
            foreground=OPS_MUTED,
            font=(MONO_FONT, 9),
            anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 12))
        actions = tk.Frame(header, background=OPS_BACKGROUND)
        actions.grid(row=0, column=1, rowspan=3, sticky="e", padx=14)
        ttk.Button(
            actions,
            text="设置",
            image=_icon(self, "database", "sidebar_text"),
            compound="left",
            style="Ops.TButton",
            command=self.on_database_settings,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="模型",
            image=_icon(self, "model", "sidebar_text"),
            compound="left",
            style="Ops.TButton",
            command=self.on_model_settings,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="项目",
            image=_icon(self, "folder", "sidebar_text"),
            compound="left",
            style="Ops.TButton",
            command=self.open_project_folder,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="返回计划",
            image=_icon(self, "back", "white"),
            compound="left",
            style="OpsAccent.TButton",
            command=self.on_back,
        ).pack(side="left", padx=(8, 0))

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self, style="Workspace.TNotebook")
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=30, pady=(0, 22))
        self.review_tab = tk.Frame(self.notebook, background=BACKGROUND)
        self.strategy_tab = tk.Frame(self.notebook, background=BACKGROUND)
        self.search_tab = tk.Frame(self.notebook, background=BACKGROUND)
        self.reading_tab = tk.Frame(self.notebook, background=BACKGROUND)
        self.report_tab = tk.Frame(self.notebook, background=BACKGROUND)
        self.tasks_tab = tk.Frame(self.notebook, background=BACKGROUND)
        self.library_tab = tk.Frame(self.notebook, background=BACKGROUND)
        for tab, icon, label in (
            (self.review_tab, "review", "02 // 综述协议"),
            (self.strategy_tab, "search", "03 // 关键词与检索式"),
            (self.search_tab, "transfer", "04 // 文献检索与全文"),
            (self.reading_tab, "reading", "05 // 结构化精读"),
            (self.report_tab, "report", "06 // 报告与核验"),
            (self.tasks_tab, "tasks", "07 // 任务与日志"),
            (self.library_tab, "database", "08 // 跨项目证据"),
        ):
            self.notebook.add(
                tab,
                text=label,
                image=_icon(self, icon),
                compound="left",
            )
        self._build_review_tab()
        self._build_strategy_tab()
        self._build_search_tab()
        self._build_reading_tab()
        self._build_report_tab()
        self._build_tasks_tab()
        self._build_library_tab()

    def _build_library_tab(self) -> None:
        tab = self.library_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        controls = tk.Frame(tab, background=BACKGROUND)
        controls.grid(row=0, column=0, sticky="ew", pady=(12, 10))
        controls.columnconfigure(0, weight=1)
        self.library_query_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.library_query_var, style="App.TEntry").grid(row=0, column=0, sticky="ew")
        ttk.Button(controls, text="检索已有证据与笔记", style="Primary.TButton", command=self.search_existing_evidence).grid(row=0, column=1, padx=(8, 0))
        outer, body = _card(tab)
        outer.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        columns = ("project", "type", "title", "locator", "evidence", "excerpt")
        self.library_tree = ttk.Treeview(body, columns=columns, show="headings", style="Ops.Treeview")
        for key, label, width in (("project", "项目", 130), ("type", "类型", 75), ("title", "论文", 240), ("locator", "定位", 120), ("evidence", "证据等级", 90), ("excerpt", "内容", 360)):
            self.library_tree.heading(key, text=label)
            self.library_tree.column(key, width=width, stretch=key in {"title", "excerpt"})
        self.library_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = AutoHideScrollbar(body, orient="vertical", command=self.library_tree.yview)
        horizontal = AutoHideScrollbar(body, orient="horizontal", command=self.library_tree.xview)
        self.library_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

    def _update_evidence_index(self) -> None:
        if self.project_directory is None or self.brief is None:
            return
        from .evidence_index import EvidenceIndex

        index = EvidenceIndex(self.settings_store.path.parent / "evidence_index.sqlite3")
        index.index_project(self.project_directory.name, self.project_directory, self.brief.topic, self.papers, self.reading_notes)

    def search_existing_evidence(self) -> None:
        query = self.library_query_var.get().strip()
        if not query:
            return
        from .evidence_index import EvidenceIndex

        rows = EvidenceIndex(self.settings_store.path.parent / "evidence_index.sqlite3").search(query)
        self.library_tree.delete(*self.library_tree.get_children())
        for index, row in enumerate(rows):
            self.library_tree.insert("", "end", iid=f"library-{index}", values=(row.get("project_title"), row.get("content_type"), row.get("title"), row.get("locator"), row.get("evidence_level"), str(row.get("body") or "")[:300]))

    def _build_tasks_tab(self) -> None:
        tab = self.tasks_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        actions = tk.Frame(tab, background=BACKGROUND)
        actions.grid(row=0, column=0, sticky="ew", pady=(12, 10))
        self.task_status_var = tk.StringVar(value="载入项目后显示持久化任务")
        tk.Label(actions, textvariable=self.task_status_var, background=BACKGROUND, foreground=MUTED, font=(FONT, 9)).pack(side="left")
        ttk.Button(actions, text="刷新", style="Secondary.TButton", command=self._refresh_tasks).pack(side="right")
        ttk.Button(actions, text="查看日志", style="Secondary.TButton", command=self.show_task_log).pack(side="right", padx=(0, 8))
        ttk.Button(actions, text="重试/继续", style="Secondary.TButton", command=self.resume_task).pack(side="right", padx=(0, 8))
        ttk.Button(actions, text="取消", style="Secondary.TButton", command=self.cancel_task).pack(side="right", padx=(0, 8))
        outer, body = _card(tab)
        outer.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        columns = ("type", "state", "progress", "message", "attempts", "updated")
        self.task_tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse", style="Ops.Treeview")
        for key, label, width in (
            ("type", "任务", 140), ("state", "状态", 90), ("progress", "进度", 90),
            ("message", "最近信息", 360), ("attempts", "尝试", 65), ("updated", "更新时间", 170),
        ):
            self.task_tree.heading(key, text=label)
            self.task_tree.column(key, width=width, stretch=key == "message")
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = AutoHideScrollbar(body, orient="vertical", command=self.task_tree.yview)
        horizontal = AutoHideScrollbar(body, orient="horizontal", command=self.task_tree.xview)
        self.task_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

    def _selected_task_id(self) -> str:
        selection = self.task_tree.selection()
        return selection[0] if selection else ""

    def cancel_task(self) -> None:
        if self.task_store is not None and (task_id := self._selected_task_id()):
            self.task_store.request_cancel(task_id)
            self._refresh_tasks()

    def resume_task(self) -> None:
        if self.task_store is not None and (task_id := self._selected_task_id()):
            try:
                self.task_store.resume(task_id)
                if self.task_worker is not None:
                    self.task_worker.wake()
            except (KeyError, ValueError) as error:
                messagebox.showwarning("无法继续任务", str(error), parent=self)
            self._refresh_tasks()

    def show_task_log(self) -> None:
        if self.task_store is None or not (task_id := self._selected_task_id()):
            return
        events = self.task_store.events(task_id)
        window = tk.Toplevel(self)
        window.title(f"任务日志 · {task_id}")
        window.geometry("820x520")
        text_widget = tk.Text(window, wrap="word", font=(MONO_FONT, 9), padx=12, pady=10)
        text_widget.pack(fill="both", expand=True)
        for event in events:
            text_widget.insert("end", f"[{event['at']}] {event['level'].upper()} {event['event_type']} · {event['message']}\n")
        text_widget.configure(state="disabled")

    def _refresh_tasks(self) -> None:
        if not hasattr(self, "task_tree"):
            return
        self.task_tree.delete(*self.task_tree.get_children())
        if self.task_store is None:
            return
        records = self.task_store.list(limit=200)
        for task in records:
            progress = f"{task.progress_current}/{task.progress_total}" if task.progress_total else "—"
            self.task_tree.insert("", "end", iid=task.task_id, values=(task.task_type, task.state, progress, task.progress_message or task.error_message, f"{task.attempts}/{task.max_attempts}", task.updated_at))
        active = sum(task.state in {"pending", "running", "retry_wait", "paused"} for task in records)
        self.task_status_var.set(f"共 {len(records)} 个任务；{active} 个未结束。任务、检查点和日志均保存在项目目录。")

    def _poll_tasks(self) -> None:
        try:
            self._apply_completed_tasks()
            self._refresh_tasks()
            self._task_poll_id = self.after(600, self._poll_tasks)
        except tk.TclError:
            self._task_poll_id = None

    def _build_review_tab(self) -> None:
        tab = self.review_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        controls = tk.Frame(tab, background=BACKGROUND)
        controls.grid(row=0, column=0, sticky="ew", pady=(12, 10))
        controls.columnconfigure(0, weight=1)
        tk.Label(controls, textvariable=self.review_status_var, background=BACKGROUND, foreground=MUTED, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        self.review_mode_var = tk.StringVar(value="ordinary")
        ttk.Combobox(
            controls,
            textvariable=self.review_mode_var,
            values=("ordinary", "rapid", "systematic"),
            state="readonly",
            width=16,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(controls, text="确认综述协议", style="Success.TButton", command=self.confirm_review_protocol).grid(row=0, column=2, padx=(8, 0))

        outer, body = _card(tab)
        outer.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure((0, 1), weight=1, uniform="protocol")
        body.rowconfigure(1, weight=1)
        tk.Label(body, text="纳入标准（每行一条）", background=SURFACE, foreground=TEXT, font=(FONT, 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(body, text="排除标准（每行一条）", background=SURFACE, foreground=TEXT, font=(FONT, 10, "bold")).grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.inclusion_text = tk.Text(body, wrap="word", font=(MONO_FONT, 9), background=TEXT_AREA, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10)
        self.exclusion_text = tk.Text(body, wrap="word", font=(MONO_FONT, 9), background=TEXT_AREA, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10)
        self.inclusion_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0), padx=(0, 8))
        self.exclusion_text.grid(row=1, column=1, sticky="nsew", pady=(8, 0), padx=(8, 0))
        _set_text(self.inclusion_text, "主题与核心问题相关\n处于指定年份范围\n可获得足够的摘要或全文信息")
        _set_text(self.exclusion_text, "重复记录\n主题不相关\n不符合研究对象或研究设计\n无法核验关键证据")

    def confirm_review_protocol(self) -> None:
        try:
            brief, store, _directory = self._require_project()
            from .review_protocol import ReviewMode, ReviewProtocol

            mode = ReviewMode(self.review_mode_var.get())
            protocol = ReviewProtocol(
                title=brief.topic,
                mode=mode,
                research_questions=brief.core_questions,
                inclusion_criteria=self._query_lines(self.inclusion_text),
                exclusion_criteria=self._query_lines(self.exclusion_text),
                databases=self._selected_source_names(),
                date_range=f"{brief.start_year}-{brief.end_year}",
                confirmed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            errors = protocol.validate()
            if errors:
                raise ValueError("；".join(errors))
            self.review_protocol = protocol
            store.save_json("review_protocol", "review/protocol.json", protocol.to_dict())
            self.review_status_var.set(f"已确认：{mode.label}；后续筛选决定和 PRISMA 数字将逐条留痕")
        except (OSError, ValueError) as error:
            messagebox.showwarning("无法确认综述协议", str(error), parent=self)

    def _selected_source_names(self) -> list[str]:
        names: list[str] = []
        for name, variable in (
            ("OpenAlex", getattr(self, "source_openalex_var", None)),
            ("Crossref", getattr(self, "source_crossref_var", None)),
            ("Zotero", getattr(self, "source_zotero_var", None)),
            ("IMA", getattr(self, "source_ima_var", None)),
        ):
            if variable is not None and variable.get():
                names.append(name)
        return names

    def _build_strategy_tab(self) -> None:
        tab = self.strategy_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        controls = tk.Frame(tab, background=BACKGROUND)
        controls.grid(row=0, column=0, sticky="ew", pady=(12, 10))
        controls.columnconfigure(0, weight=1)
        tk.Label(controls, textvariable=self.strategy_status_var, background=BACKGROUND, foreground=MUTED, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        self.strategy_mode_var = tk.StringVar(value="local")
        ttk.Radiobutton(controls, text="本地规则", variable=self.strategy_mode_var, value="local").grid(row=0, column=1, padx=(8, 0))
        ttk.Radiobutton(controls, text="大模型 Agent", variable=self.strategy_mode_var, value="agent").grid(row=0, column=2, padx=(8, 0))
        self.strategy_generate_button = ttk.Button(controls, text="生成", style="Primary.TButton", command=self.generate_strategy)
        self.strategy_generate_button.grid(row=0, column=3, padx=(12, 0))
        ttk.Button(controls, text="确认并进入检索", style="Success.TButton", command=self.confirm_strategy).grid(row=0, column=4, padx=(8, 0))

        editor_outer, editor = _card(tab)
        editor_outer.grid(row=1, column=0, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.columnconfigure(1, weight=1)
        editor.rowconfigure(1, weight=1)
        keyword_heading = tk.Frame(editor, background=SURFACE)
        keyword_heading.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        tk.Label(keyword_heading, text="KEYWORD_TREE.md", background=SURFACE, foreground=PRIMARY, font=(MONO_FONT, 10, "bold")).pack(side="left")
        tk.Label(editor, text="检索式（每行一条，可编辑）", background=SURFACE, foreground=TEXT, font=(FONT, 10, "bold")).grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.keyword_tree_text = tk.Text(editor, wrap="word", undo=True, font=(MONO_FONT, 9), background=TEXT_AREA, foreground=TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10)
        keyword_grid = {"row": 1, "column": 0, "sticky": "nsew", "pady": (7, 0), "padx": (0, 8)}
        self.keyword_tree_text.grid(**keyword_grid)
        keyword_view = MarkdownPreviewToggle(
            self.keyword_tree_text,
            editor,
            palette=self.palette,
            ui_font=self.ui_font,
            mono_font=self.mono_font,
            grid_options=keyword_grid,
            editable=True,
        )
        keyword_view.mount_switcher(keyword_heading).pack(side="right")
        self.markdown_views["keyword_tree"] = keyword_view
        queries = tk.Frame(editor, background=SURFACE)
        queries.grid(row=1, column=1, sticky="nsew", pady=(7, 0), padx=(8, 0))
        queries.columnconfigure(0, weight=1)
        queries.rowconfigure(1, weight=1)
        queries.rowconfigure(3, weight=1)
        tk.Label(queries, text="宽检索（召回）", background=SURFACE, foreground=MUTED, font=(FONT, 9, "bold")).grid(row=0, column=0, sticky="w")
        self.broad_queries_text = tk.Text(queries, wrap="word", undo=True, height=8, font=(MONO_FONT, 9), background=TEXT_AREA, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=10, pady=8)
        self.broad_queries_text.grid(row=1, column=0, sticky="nsew", pady=(5, 10))
        tk.Label(queries, text="精检索（高相关筛选）", background=SURFACE, foreground=MUTED, font=(FONT, 9, "bold")).grid(row=2, column=0, sticky="w")
        self.precision_queries_text = tk.Text(queries, wrap="word", undo=True, height=8, font=(MONO_FONT, 9), background=TEXT_AREA, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=10, pady=8)
        self.precision_queries_text.grid(row=3, column=0, sticky="nsew", pady=(5, 0))

    def _build_search_tab(self) -> None:
        tab = self.search_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        controls_outer, controls = _card(tab)
        controls_outer.grid(row=0, column=0, sticky="ew", pady=(12, 10))
        controls.columnconfigure(1, weight=1)
        self.source_openalex_var = tk.BooleanVar(value=True)
        self.source_crossref_var = tk.BooleanVar(value=True)
        self.source_zotero_var = tk.BooleanVar(value=True)
        self.source_ima_var = tk.BooleanVar(value=False)
        sources = tk.Frame(controls, background=SURFACE)
        sources.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        for text, variable in (
            ("OpenAlex", self.source_openalex_var),
            ("Crossref", self.source_crossref_var),
            ("Zotero", self.source_zotero_var),
            ("IMA", self.source_ima_var),
        ):
            ttk.Checkbutton(sources, text=text, variable=variable).pack(side="left", padx=(0, 14))
        ttk.Button(sources, text="刷新数据库配置", style="Secondary.TButton", command=self._sync_source_defaults).pack(side="right")
        tk.Label(controls, text="本次检索式", background=SURFACE, foreground=TEXT, font=(FONT, 9, "bold")).grid(row=1, column=0, sticky="w")
        self.search_query_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.search_query_var, style="App.TEntry").grid(row=1, column=1, sticky="ew", padx=(10, 10))
        self.search_limit_var = tk.StringVar(value="20")
        ttk.Spinbox(controls, from_=1, to=100, textvariable=self.search_limit_var, width=6).grid(row=1, column=2)
        self.search_button = ttk.Button(controls, text="执行多源检索", style="Primary.TButton", command=self.run_search)
        self.search_button.grid(row=1, column=3, padx=(10, 0))

        status_row = tk.Frame(tab, background=BACKGROUND)
        status_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        status_row.columnconfigure(0, weight=1)
        tk.Label(status_row, textvariable=self.search_status_var, background=BACKGROUND, foreground=MUTED, font=(FONT, 9)).grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 7))
        ttk.Button(status_row, text="标记/取消核心", style="Secondary.TButton", command=self.toggle_core_papers).grid(row=1, column=1)
        ttk.Button(status_row, text="筛选纳入", style="Secondary.TButton", command=lambda: self.mark_screening("include")).grid(row=1, column=2, padx=(8, 0))
        ttk.Button(status_row, text="筛选排除", style="Secondary.TButton", command=lambda: self.mark_screening("exclude")).grid(row=1, column=3, padx=(8, 0))
        ttk.Button(status_row, text="质量评分", style="Secondary.TButton", command=self.score_selected_quality).grid(row=1, column=4, padx=(8, 0))
        ttk.Button(status_row, text="写入 Zotero", style="Secondary.TButton", command=self.enrich_zotero).grid(row=1, column=5, padx=(8, 0))
        ttk.Button(status_row, text="关联全文/补充材料", style="Secondary.TButton", command=self.attach_local_pdf).grid(row=2, column=1, pady=(7, 0))
        self.download_button = ttk.Button(status_row, text="合规获取所选全文", style="Secondary.TButton", command=self.download_selected)
        self.download_button.grid(row=2, column=2, padx=(8, 0), pady=(7, 0))
        ttk.Button(status_row, text="确认文献集并进入精读", style="Success.TButton", command=self.confirm_papers).grid(row=2, column=3, padx=(8, 0), pady=(7, 0))
        ttk.Button(status_row, text="OCR 所选扫描件", style="Secondary.TButton", command=self.run_ocr_selected).grid(row=2, column=4, padx=(8, 0), pady=(7, 0))

        table_outer, table = _card(tab)
        table_outer.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ("core", "screening", "year", "title", "source", "doi", "evidence", "access", "score", "reason")
        self.paper_tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="extended", style="Ops.Treeview")
        headings = {"core": "核心", "screening": "筛选", "year": "年份", "title": "标题", "source": "来源", "doi": "DOI", "evidence": "证据", "access": "访问状态", "score": "核心分", "reason": "推荐理由"}
        widths = {"core": 42, "screening": 52, "year": 50, "title": 220, "source": 75, "doi": 115, "evidence": 82, "access": 105, "score": 58, "reason": 150}
        for column in columns:
            self.paper_tree.heading(column, text=headings[column])
            self.paper_tree.column(column, width=widths[column], minwidth=40, stretch=column in {"title", "access", "reason"})
        scrollbar = AutoHideScrollbar(table, orient="vertical", command=self.paper_tree.yview)
        horizontal = AutoHideScrollbar(table, orient="horizontal", command=self.paper_tree.xview)
        self.paper_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.paper_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

    def _build_reading_tab(self) -> None:
        tab = self.reading_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        controls = tk.Frame(tab, background=BACKGROUND)
        controls.grid(row=0, column=0, sticky="ew", pady=(12, 10))
        controls.columnconfigure(0, weight=1)
        tk.Label(controls, textvariable=self.reading_status_var, background=BACKGROUND, foreground=MUTED, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        self.reading_mode_var = tk.StringVar(value="local")
        ttk.Radiobutton(controls, text="本地结构化", variable=self.reading_mode_var, value="local").grid(row=0, column=1)
        ttk.Radiobutton(controls, text="大模型 Agent", variable=self.reading_mode_var, value="agent").grid(row=0, column=2, padx=(8, 0))
        self.reading_generate_button = ttk.Button(controls, text="精读所选论文", style="Primary.TButton", command=self.generate_reading_notes)
        self.reading_generate_button.grid(row=0, column=3, padx=(12, 0))
        ttk.Button(controls, text="确认精读并进入报告", style="Success.TButton", command=self.confirm_readings).grid(row=0, column=4, padx=(8, 0))
        panes = tk.PanedWindow(tab, orient="horizontal", sashwidth=6, background=BACKGROUND, relief="flat")
        panes.grid(row=1, column=0, sticky="nsew")
        left_outer, left = _card(panes)
        right_outer, right = _card(panes)
        panes.add(left_outer, minsize=370)
        panes.add(right_outer, minsize=470)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        tk.Label(left, text="核心论文", background=SURFACE, foreground=TEXT, font=(FONT, 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.reading_tree = ttk.Treeview(left, columns=("title", "evidence", "status"), show="headings", selectmode="extended", style="Ops.Treeview")
        for column, label, width in (("title", "标题", 260), ("evidence", "证据", 90), ("status", "精读状态", 90)):
            self.reading_tree.heading(column, text=label)
            self.reading_tree.column(column, width=width, stretch=column == "title")
        self.reading_tree.grid(row=1, column=0, sticky="nsew")
        self.reading_tree.bind("<<TreeviewSelect>>", self._show_selected_reading)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        reading_heading = tk.Frame(right, background=SURFACE)
        reading_heading.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(reading_heading, text="READING_CARD.md", background=SURFACE, foreground=PRIMARY, font=(MONO_FONT, 10, "bold")).pack(side="left")
        self.reading_preview = tk.Text(right, wrap="word", font=(MONO_FONT, 9), background=TEXT_AREA, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10, state="disabled")
        reading_grid = {"row": 1, "column": 0, "sticky": "nsew"}
        self.reading_preview.grid(**reading_grid)
        reading_view = MarkdownPreviewToggle(
            self.reading_preview,
            right,
            palette=self.palette,
            ui_font=self.ui_font,
            mono_font=self.mono_font,
            grid_options=reading_grid,
            editable=False,
        )
        reading_view.mount_switcher(reading_heading).pack(side="right")
        self.markdown_views["reading_card"] = reading_view

    def _build_report_tab(self) -> None:
        tab = self.report_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(tab, style="SubBookmark.TNotebook")
        notebook.grid(row=0, column=0, sticky="nsew", pady=(12, 0))
        self.report_audit_notebook = notebook
        summary_tab = tk.Frame(notebook, background=BACKGROUND)
        audit_tab = tk.Frame(notebook, background=BACKGROUND)
        notebook.add(summary_tab, text="文献总结报告")
        notebook.add(audit_tab, text="核验报告")

        summary_tab.columnconfigure(0, weight=1)
        summary_tab.rowconfigure(1, weight=1)
        controls = tk.Frame(summary_tab, background=BACKGROUND)
        controls.grid(row=0, column=0, sticky="ew", pady=(12, 10), padx=12)
        controls.columnconfigure(0, weight=1)
        tk.Label(controls, textvariable=self.report_status_var, background=BACKGROUND, foreground=MUTED, font=(FONT, 9)).grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 7))
        self.report_mode_var = tk.StringVar(value="local")
        self.report_template_var = tk.StringVar(value="academic_review")
        ttk.Radiobutton(controls, text="本地证据综合", variable=self.report_mode_var, value="local").grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(controls, text="大模型 Agent", variable=self.report_mode_var, value="agent").grid(row=1, column=1, padx=(8, 0))
        ttk.Combobox(controls, textvariable=self.report_template_var, values=("academic_review", "grant_proposal", "industry_research"), state="readonly", width=16).grid(row=1, column=2, padx=(8, 0))
        self.report_generate_button = ttk.Button(controls, text="生成文献总结报告", style="Primary.TButton", command=self.generate_report)
        self.report_generate_button.grid(row=2, column=0, pady=(7, 0))
        ttk.Button(controls, text="保存当前草稿", style="Secondary.TButton", command=self.save_report_draft).grid(row=2, column=1, padx=(8, 0), pady=(7, 0))
        ttk.Button(controls, text="导出总结报告", style="Secondary.TButton", command=self.export_report).grid(row=2, column=2, padx=(8, 0), pady=(7, 0))
        ttk.Button(controls, text="导出 RIS", style="Secondary.TButton", command=self.export_ris).grid(row=2, column=3, padx=(8, 0), pady=(7, 0))
        report_outer, report = _card(summary_tab)
        report_outer.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        report.columnconfigure(0, weight=1)
        report.rowconfigure(1, weight=1)
        report_heading = tk.Frame(report, background=SURFACE)
        report_heading.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(report_heading, text="LITERATURE_SUMMARY.md", background=SURFACE, foreground=PRIMARY, font=(MONO_FONT, 10, "bold")).pack(side="left")
        self.report_text = tk.Text(report, wrap="word", undo=True, font=(MONO_FONT, 9), background=TEXT_AREA, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10)
        self.report_text.bind("<<Modified>>", self._on_report_text_modified, add="+")
        report_grid = {"row": 1, "column": 0, "sticky": "nsew"}
        self.report_text.grid(**report_grid)
        report_view = MarkdownPreviewToggle(
            self.report_text,
            report,
            palette=self.palette,
            ui_font=self.ui_font,
            mono_font=self.mono_font,
            grid_options=report_grid,
            editable=True,
        )
        report_view.mount_switcher(report_heading).pack(side="right")
        self.markdown_views["report"] = report_view

        audit_tab.columnconfigure(0, weight=1)
        audit_tab.rowconfigure(1, weight=1)
        audit_controls = tk.Frame(audit_tab, background=BACKGROUND)
        audit_controls.grid(row=0, column=0, sticky="ew", pady=(12, 10), padx=12)
        audit_controls.columnconfigure(0, weight=1)
        tk.Label(audit_controls, textvariable=self.audit_status_var, background=BACKGROUND, foreground=MUTED, font=(FONT, 9)).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 7))
        self.audit_mode_var = tk.StringVar(value="local")
        ttk.Radiobutton(audit_controls, text="本地确定性核验", variable=self.audit_mode_var, value="local").grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(audit_controls, text="大模型语义核验", variable=self.audit_mode_var, value="agent").grid(row=1, column=1, padx=(8, 0))
        self.audit_generate_button = ttk.Button(audit_controls, text="生成核验报告", style="Primary.TButton", command=self.generate_audit)
        self.audit_generate_button.grid(row=2, column=0, pady=(7, 0))
        ttk.Button(audit_controls, text="重新核验当前报告", style="Secondary.TButton", command=self.reaudit_report).grid(row=2, column=1, padx=(8, 0), pady=(7, 0))
        ttk.Button(audit_controls, text="导出核验报告", style="Secondary.TButton", command=self.export_audit_report).grid(row=2, column=2, padx=(8, 0), pady=(7, 0))
        audit_outer, audit = _card(audit_tab)
        audit_outer.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        audit.columnconfigure(0, weight=1)
        audit.rowconfigure(1, weight=1)
        audit_heading = tk.Frame(audit, background=SURFACE)
        audit_heading.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(audit_heading, text="VERIFICATION_REPORT.md", background=SURFACE, foreground=PRIMARY, font=(MONO_FONT, 10, "bold")).pack(side="left")
        self.audit_text = tk.Text(audit, wrap="word", font=(MONO_FONT, 9), background=TEXT_AREA, relief="flat", highlightthickness=1, highlightbackground=BORDER, padx=12, pady=10, state="disabled")
        audit_grid = {"row": 1, "column": 0, "sticky": "nsew"}
        self.audit_text.grid(**audit_grid)
        audit_view = MarkdownPreviewToggle(
            self.audit_text,
            audit,
            palette=self.palette,
            ui_font=self.ui_font,
            mono_font=self.mono_font,
            grid_options=audit_grid,
            editable=False,
        )
        audit_view.mount_switcher(audit_heading).pack(side="right")
        self.markdown_views["audit"] = audit_view

    def _on_report_text_modified(self, event: tk.Event[tk.Misc]) -> None:
        widget = event.widget
        if not isinstance(widget, tk.Text) or not widget.edit_modified():
            return
        widget.edit_modified(False)
        self.report_status_var.set("当前文献总结草稿有未保存修改")
        self.audit_status_var.set("核验报告已过期：总结报告发生修改")

    # -- project lifecycle ---------------------------------------------

    def set_project(
        self,
        *,
        brief: ResearchBrief,
        confirmed_plan: str,
        project_directory: Path,
    ) -> None:
        self.brief = brief
        self.confirmed_plan = confirmed_plan.strip()
        self.project_directory = Path(project_directory).resolve()
        self.store = WorkflowStore(self.project_directory)
        manifest = self.store.initialize()
        if self.task_worker is not None:
            self.task_worker.stop()
        from .task_queue import TaskStore, TaskWorker

        self.task_store = TaskStore(self.store.task_database_path)
        self.task_worker = TaskWorker(self.task_store)
        self.task_worker.register("literature_search", self._execute_search_task)
        self.task_worker.register("fulltext_download", self._execute_fulltext_task)
        self.task_worker.register("ocr_document", self._execute_ocr_task)
        self.task_worker.register("structured_reading", self._execute_reading_task)
        self.task_worker.register("report_generation", self._execute_report_task)
        self.task_worker.register("audit_generation", self._execute_audit_task)
        self.task_worker.start()
        if self._task_poll_id is not None:
            try:
                self.after_cancel(self._task_poll_id)
            except tk.TclError:
                pass
        self._poll_tasks()
        self.project_title_var.set(brief.topic)

        raw_strategy = self.store.load_json("search/search_strategy.json")
        self.strategy = (
            SearchStrategyBundle.from_dict(raw_strategy)
            if isinstance(raw_strategy, dict)
            else None
        )
        raw_papers = self.store.load_json("search/papers.json", [])
        self.papers = [
            PaperRecord.from_dict(item)
            for item in raw_papers
            if isinstance(item, dict)
        ]
        raw_notes = self.store.load_json("reading_notes/index.json", [])
        self.reading_notes = [
            ReadingNote.from_dict(item)
            for item in raw_notes
            if isinstance(item, dict)
        ]
        from .review_protocol import QualityAssessment, ReviewProtocol, ScreeningDecision

        raw_protocol = self.store.load_json("review/protocol.json")
        self.review_protocol = ReviewProtocol.from_dict(raw_protocol) if isinstance(raw_protocol, dict) else None
        self.screening_decisions = [
            ScreeningDecision.from_dict(item)
            for item in self.store.load_json("review/screening.json", [])
            if isinstance(item, dict)
        ]
        self.quality_assessments = {
            str(item.get("paper_id")): QualityAssessment(
                paper_id=str(item.get("paper_id")), tool=str(item.get("tool") or "custom"),
                criteria=dict(item.get("criteria") or {}), notes=dict(item.get("notes") or {}),
                reviewer=str(item.get("reviewer") or "user"), assessed_at=str(item.get("assessed_at") or ""),
            )
            for item in self.store.load_json("review/quality_assessments.json", [])
            if isinstance(item, dict) and item.get("paper_id")
        }
        if self.review_protocol is not None:
            self.review_mode_var.set(self.review_protocol.mode.value)
            _set_text(self.inclusion_text, "\n".join(self.review_protocol.inclusion_criteria))
            _set_text(self.exclusion_text, "\n".join(self.review_protocol.exclusion_criteria))
            self.review_status_var.set(f"已载入：{self.review_protocol.mode.label}")
        workflow = manifest.get("workflow", {})
        self.core_paper_ids = list(workflow.get("core_paper_ids") or [])
        self._display_strategy()
        self._refresh_paper_tree()
        self._refresh_reading_tree()
        report_text = self.store.read_text("report/literature_summary.md") or self.store.read_text("report/research_report.md")
        audit_text = self.store.read_text("audit/verification_report.md") or self.store.read_text("report/claim_citation_audit.md")
        _set_text(self.report_text, report_text)
        _set_text(self.audit_text, audit_text, readonly=True)
        self._refresh_report_audit_status(manifest, report_text=report_text)
        self._refresh_workflow_status(manifest)

    def _execute_search_task(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        from .search_engine import CrossrefProvider, ImaSearchProvider, OpenAlexProvider, ZoteroSearchProvider, search_all

        sources = set(payload.get("sources") or [])
        config = self.settings.discovery
        providers: list[Any] = []
        if "OpenAlex" in sources:
            providers.append(OpenAlexProvider(mailto=config.polite_email, timeout=config.timeout_seconds))
        if "Crossref" in sources:
            providers.append(CrossrefProvider(mailto=config.polite_email, timeout=config.timeout_seconds))
        if "Zotero" in sources:
            providers.append(ZoteroSearchProvider(ZoteroConnector(self.settings.zotero)))
        if "IMA" in sources:
            providers.append(ImaSearchProvider(ImaConnector(self.settings.ima, client_id=self.secret_store.get("ima.client_id"), api_key=self.secret_store.get("ima.api_key"))))
        context.progress(0, len(providers), "正在执行多源检索", checkpoint={"completed_sources": []})
        from .source_adapter import ResponseCache

        result = search_all(
            providers,
            [str(payload["query"])],
            start_year=payload.get("start_year"),
            end_year=payload.get("end_year"),
            limit_per_query=int(payload.get("limit") or 20),
            response_cache=ResponseCache(self.project_directory / "cache" / "search.sqlite3") if self.project_directory else None,
        )
        if not result.papers and result.failures:
            from .task_queue import RetryableTaskError

            raise RetryableTaskError("所有数据库请求均失败，将按退避策略重试。", code="all_sources_failed")
        context.progress(len(providers), len(providers), "检索完成", checkpoint={"completed_sources": list(result.providers), "run_id": result.run_id})
        if self.store is not None:
            self.store.save_json(f"search_run_{result.run_id}", f"runs/{result.run_id}.json", result.to_dict())
        return result.to_dict()

    def _apply_completed_tasks(self) -> None:
        if self.task_store is None or self.store is None:
            return
        manifest = self.store.load_manifest()
        applied = set(manifest.get("workflow", {}).get("applied_task_ids") or [])
        for task in reversed(self.task_store.list(states={"succeeded"}, limit=200)):
            if task.task_id in applied or not task.result:
                continue
            if task.task_type == "literature_search":
                self._apply_search_task(task.result)
                self.store.mark_task_applied(task.task_id)
                applied.add(task.task_id)
            elif task.task_type == "fulltext_download":
                self._apply_paper_updates(task.result)
                self.store.mark_task_applied(task.task_id)
                applied.add(task.task_id)
            elif task.task_type == "ocr_document":
                self._apply_paper_updates(task.result)
                self.store.mark_task_applied(task.task_id)
                applied.add(task.task_id)
            elif task.task_type == "structured_reading":
                self._apply_reading_task(task.result)
                self.store.mark_task_applied(task.task_id)
                applied.add(task.task_id)
            elif task.task_type == "report_generation":
                self._apply_report_task(task.result)
                self.store.mark_task_applied(task.task_id)
                applied.add(task.task_id)
            elif task.task_type == "audit_generation":
                self._apply_audit_task(task.result)
                self.store.mark_task_applied(task.task_id)
                applied.add(task.task_id)

    def _apply_search_task(self, result: dict[str, Any]) -> None:
        if self.store is None:
            return
        from .search_engine import deduplicate_papers, rank_papers

        incoming = [PaperRecord.from_dict(item) for item in result.get("papers", []) if isinstance(item, dict)]
        query = " ".join(str(item) for item in result.get("executed_queries", []))
        self.papers = rank_papers(deduplicate_papers([*self.papers, *incoming]), query=query)
        self.store.save_json("last_search_run", "search/last_search_run.json", result)
        self._save_papers()
        self.store.set_stage("search", "in_progress", message=f"{len(result.get('providers', []))} 个来源返回 {len(incoming)} 篇候选")
        self._refresh_paper_tree()
        failures = result.get("failures") or []
        self.search_status_var.set(f"检索任务已完成：新增候选 {len(incoming)} 篇，项目共 {len(self.papers)} 篇" + (f"；{len(failures)} 个请求失败" if failures else ""))
        self._refresh_workflow_status()

    def _execute_fulltext_task(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        if self.project_directory is None or self.store is None:
            raise ValueError("项目未载入。")
        from .fulltext import DownloadRequest, FullTextDownloader, apply_fulltext_result

        paper_ids = list(payload.get("paper_ids") or [])
        by_id = {paper.record_id: paper for paper in self.papers}
        checkpoint = context.checkpoint
        completed = set(checkpoint.get("completed_paper_ids") or [])
        updated_payload = {item["record_id"]: item for item in checkpoint.get("papers", []) if isinstance(item, dict) and item.get("record_id")}
        downloader = FullTextDownloader(self.project_directory, self.settings.library)
        for index, paper_id in enumerate(paper_ids, 1):
            context.check_cancelled()
            if paper_id in completed:
                continue
            paper = by_id.get(paper_id)
            if paper is None:
                continue
            request = DownloadRequest.from_paper(paper)
            if not paper.doi and bool((paper.extra or {}).get("is_open_access")):
                request = replace(request, open_access=True)
            batch = downloader.download(
                [request],
                include_supporting_information=self.settings.library.include_supporting_information,
                abstracts={paper.record_id: paper.abstract},
            )
            if batch.results:
                result = batch.results[0]
                updated = apply_fulltext_result(paper, result)
                if result.file and Path(result.file).is_file():
                    from .document_ingest import DocumentIngestor

                    ingest = DocumentIngestor().ingest(updated, Path(result.file), role="main")
                    extra = dict(updated.extra)
                    assets = list(extra.get("document_assets") or [])
                    assets.append(ingest.asset.to_dict())
                    extra["document_assets"] = assets
                    updated = replace(updated, document_asset_ids=[*updated.document_asset_ids, ingest.asset.asset_id], extra=extra)
                    self.store.save_json(f"document_asset_{ingest.asset.asset_id}", f"fulltext/{ingest.asset.asset_id}.json", ingest.asset.to_dict())
                    self.store.save_json(f"document_blocks_{ingest.asset.asset_id}", f"fulltext/extracted_text/{ingest.asset.asset_id}_blocks.json", [block.to_dict() for block in ingest.evidence_blocks])
                by_id[paper_id] = updated
                updated_payload[paper_id] = updated.to_dict()
                self.store.save_json(f"fulltext_result_{paper_id}", f"fulltext/{paper_id}_result.json", result.to_dict())
            completed.add(paper_id)
            context.progress(index, len(paper_ids), f"已处理 {index}/{len(paper_ids)} 篇全文", checkpoint={"completed_paper_ids": sorted(completed), "papers": list(updated_payload.values())})
        return {"papers": list(updated_payload.values()), "completed": len(completed), "total": len(paper_ids)}

    def _apply_paper_updates(self, result: dict[str, Any]) -> None:
        updates = {item["record_id"]: PaperRecord.from_dict(item) for item in result.get("papers", []) if isinstance(item, dict) and item.get("record_id")}
        self.papers = [updates.get(paper.record_id, paper) for paper in self.papers]
        self._save_papers()
        self._refresh_paper_tree()
        self._refresh_reading_tree()
        self.search_status_var.set(f"全文任务完成：已处理 {result.get('completed', 0)}/{result.get('total', 0)} 篇")

    def _execute_ocr_task(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        if self.project_directory is None or self.store is None:
            raise ValueError("项目未载入。")
        paper = self._paper_by_id(str(payload.get("paper_id") or ""))
        source = Path(str(payload.get("source_path") or ""))
        if paper is None or not source.is_file():
            raise ValueError("OCR 源 PDF 不存在或论文记录已移除。")
        from .document_ingest import DocumentIngestor, OCRRunner

        target = self.project_directory / "fulltext" / "PDFs" / f"{paper.record_id}_ocr.pdf"
        context.progress(0, 2, "正在执行本地 OCR")
        OCRRunner().run(source, target)
        context.progress(1, 2, "OCR 完成，正在重新校验文本层")
        result = DocumentIngestor().ingest(paper, target, role="main")
        if result.asset.verification_status != "verified" or not result.evidence_blocks:
            raise ValueError("OCR 输出仍未通过题名/文本层校验，保持需人工核对。")
        extra = dict(paper.extra)
        assets = list(extra.get("document_assets") or [])
        assets.append(result.asset.to_dict())
        extra.update({"document_assets": assets, "local_file": str(target), "ocr_verified": True})
        updated = replace(paper, evidence_level=EvidenceLevel.FULL_TEXT, access_status="OCR 输出已重新校验，可作为全文证据", document_asset_ids=[*paper.document_asset_ids, result.asset.asset_id], extra=extra)
        self.store.save_json(f"document_asset_{result.asset.asset_id}", f"fulltext/{result.asset.asset_id}.json", result.asset.to_dict())
        self.store.save_json(f"document_blocks_{result.asset.asset_id}", f"fulltext/extracted_text/{result.asset.asset_id}_blocks.json", [block.to_dict() for block in result.evidence_blocks])
        context.progress(2, 2, "OCR 输出已通过校验", checkpoint={"asset_id": result.asset.asset_id})
        return {"papers": [updated.to_dict()], "completed": 1, "total": 1}

    def _execute_reading_task(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        if self.brief is None:
            raise ValueError("项目未载入。")
        from .reader import read_paper_deterministically, read_paper_with_llm

        mode = str(payload.get("mode") or "local")
        paper_ids = list(payload.get("paper_ids") or [])
        checkpoint = context.checkpoint
        notes = {item["paper_id"]: item for item in checkpoint.get("notes", []) if isinstance(item, dict) and item.get("paper_id")}
        for index, paper_id in enumerate(paper_ids, 1):
            context.check_cancelled()
            if paper_id in notes:
                continue
            paper = self._paper_by_id(paper_id)
            if paper is None:
                continue
            deterministic = read_paper_deterministically(paper, core_questions=self.brief.core_questions)
            if mode == "agent" and deterministic.evidence_blocks:
                note = read_paper_with_llm(paper, evidence_blocks=deterministic.evidence_blocks, client=self._model_client("reading"), core_questions=self.brief.core_questions)
            else:
                note = deterministic
            notes[paper_id] = note.to_dict()
            context.progress(index, len(paper_ids), f"已精读 {index}/{len(paper_ids)} 篇", checkpoint={"notes": list(notes.values())})
        return {"notes": list(notes.values()), "mode": mode, "total": len(paper_ids)}

    def _apply_reading_task(self, result: dict[str, Any]) -> None:
        incoming = [ReadingNote.from_dict(item) for item in result.get("notes", []) if isinstance(item, dict)]
        by_id = {note.paper_id: note for note in self.reading_notes}
        by_id.update({note.paper_id: note for note in incoming})
        self.reading_notes = [by_id[paper_id] for paper_id in self.core_paper_ids if paper_id in by_id]
        self._save_reading_notes()
        if self.store is not None:
            self.store.set_stage("reading", "in_progress", message=f"已生成 {len(self.reading_notes)}/{len(self.core_paper_ids)} 篇结构化精读卡")
        self._refresh_reading_tree()
        self.reading_status_var.set(f"精读任务完成：生成 {len(incoming)} 篇精读卡")

    def _execute_report_task(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        if self.brief is None or self.strategy is None:
            raise ValueError("项目缺少调研需求或检索策略。")
        from .reporting import generate_literature_summary_bundle

        mode = str(payload.get("mode") or "local")
        template = str(payload.get("template") or "academic_review")
        context.progress(0, 2, "正在生成文献总结报告")
        client = self._model_client("report_synthesis") if mode == "agent" else None
        bundle = generate_literature_summary_bundle(
            self.brief, self.confirmed_plan, self.strategy, self.papers, self.reading_notes,
            llm_client=client, use_llm_synthesis=mode == "agent",
        )
        from .report_templates import apply_report_template

        report_text = apply_report_template(bundle.research_report, template)
        context.progress(1, 2, "正在保存总结报告、论断台账与参考文献")
        self._save_summary_bundle(bundle, report_text=report_text, mode=mode, template=template)
        context.progress(2, 2, "文献总结报告完成；等待独立核验")
        return {
            "research_report": report_text,
            "references_bib": bundle.references_bib,
            "claim_count": len(bundle.claim_ledger),
        }

    def _apply_report_task(self, result: dict[str, Any]) -> None:
        _set_text(self.report_text, str(result.get("research_report") or ""))
        if self.store is not None:
            self.store.set_stage("report", "complete", next_stage="audit", message="文献总结报告已生成；核验尚未运行")
        self.report_status_var.set(f"文献总结报告完成：{int(result.get('claim_count') or 0)} 条可追溯论断")
        self.audit_status_var.set("文献总结报告已更新；请单独生成核验报告")
        self._refresh_workflow_status()

    def _execute_audit_task(self, context: Any, payload: dict[str, Any]) -> dict[str, Any]:
        if self.store is None:
            raise ValueError("项目未载入。")
        from .report_lifecycle import ReportState, nonclaim_text_hash, synchronize_claims_from_report
        from .reporting import generate_verification_bundle

        report_text = self.store.read_text("report/literature_summary.md") or self.store.read_text("report/research_report.md")
        if not report_text.strip():
            raise ValueError("文献总结报告为空。")
        claims = self._load_claim_ledger()
        if not claims:
            raise ValueError("论断台账为空，不能生成核验报告。")
        synchronized = synchronize_claims_from_report(report_text, claims)
        if synchronized.errors:
            raise ValueError("；".join(synchronized.errors))
        generated_snapshot = self.store.read_text("report/literature_summary.generated.md")
        report_state = ReportState.from_dict(self.store.load_json("report/report_state.json", {}))
        if generated_snapshot:
            nonclaim_changed = nonclaim_text_hash(generated_snapshot) != nonclaim_text_hash(report_text)
        else:
            nonclaim_changed = bool(report_state and report_state.nonclaim_hash and report_state.nonclaim_hash != nonclaim_text_hash(report_text))
        mode = str(payload.get("mode") or "local")
        context.progress(0, 2, "正在核对报告论断与证据绑定")
        client = self._model_client("report_semantic_audit") if mode == "agent" else None
        bundle = generate_verification_bundle(
            synchronized.claims,
            self.papers,
            self.reading_notes,
            llm_client=client,
            use_llm_semantic_audit=mode == "agent",
        )
        if (mode == "local" and synchronized.changed_claim_ids) or nonclaim_changed:
            bundle = self._mark_manual_claim_edits(
                bundle,
                synchronized.changed_claim_ids if mode == "local" else (),
                nonclaim_changed=nonclaim_changed,
            )
        context.progress(1, 2, "正在保存独立核验报告与交付门禁")
        self._save_verification_bundle(
            bundle,
            report_text=report_text,
            claims=synchronized.claims,
            mode=mode,
        )
        context.progress(2, 2, "核验报告完成")
        return {
            "verification_report": bundle.claim_citation_audit,
            "overall_status": bundle.audit.overall_status,
            "changed_claim_ids": list(synchronized.changed_claim_ids),
            "nonclaim_changed": nonclaim_changed,
        }

    def _apply_audit_task(self, result: dict[str, Any]) -> None:
        _set_text(self.audit_text, str(result.get("verification_report") or ""), readonly=True)
        overall = str(result.get("overall_status") or "warning")
        changed = list(result.get("changed_claim_ids") or [])
        nonclaim_changed = bool(result.get("nonclaim_changed"))
        if self.store is not None:
            self.store.set_stage("audit", "complete" if overall == "pass" else "warning", message=f"独立核验报告已生成；总体状态 {overall}")
        suffix = f"；{len(changed)} 条人工改写论断需要语义复核" if changed else ""
        if nonclaim_changed:
            suffix += "；论断台账之外的正文发生修改，需人工复核"
        self.audit_status_var.set(f"核验报告完成；总体状态：{overall}{suffix}")
        self._refresh_workflow_status()

    def _require_project(self) -> tuple[ResearchBrief, WorkflowStore, Path]:
        if self.brief is None or self.store is None or self.project_directory is None:
            raise ValueError("请先确认或打开一个调研项目。")
        return self.brief, self.store, self.project_directory

    def _model_client(self, purpose: str = "workflow") -> LLMClient:
        model = self.settings.model
        api_key = self.secret_store.get("model.api_key")
        if not model.is_configured(api_key):
            raise ValueError("大模型尚未配置完整，请先打开“大模型设置”。")
        def audit_callback(event: dict[str, Any]) -> None:
            if self.store is None:
                return
            from .llm_policy import estimate_tokens
            from .provenance import content_hash, now_iso

            records = self.store.load_json("runs/model_invocations.json", [])
            if not isinstance(records, list):
                records = []
            records.append(
                {
                    "purpose": purpose,
                    "provider": model.provider_name,
                    "model": model.model,
                    "protocol": model.protocol,
                    "prompt_version": f"review-writer:{purpose}:v1",
                    "system_prompt_hash": content_hash(event.get("system_prompt", "")),
                    "user_payload_hash": content_hash(event.get("user_prompt", "")),
                    "response_hash": content_hash(event.get("response", "")),
                    "input_tokens_estimated": estimate_tokens(str(event.get("system_prompt", "")) + str(event.get("user_prompt", ""))),
                    "output_tokens_estimated": estimate_tokens(str(event.get("response", ""))),
                    "max_output_tokens": event.get("max_output_tokens"),
                    "pricing_source": model.pricing_source,
                    "pricing_updated_at": model.pricing_updated_at,
                    "input_price_per_million": model.input_price_per_million,
                    "output_price_per_million": model.output_price_per_million,
                    "price_currency": model.price_currency,
                    "finished_at": now_iso(),
                    "status": "succeeded",
                }
            )
            self.store.save_json("model_invocations", "runs/model_invocations.json", records[-1000:])

        return LLMClient(model, api_key, audit_callback=audit_callback)

    def _confirm_external_model_call(self, purpose: str, materials: list[Any]) -> bool:
        from .llm_policy import DataClass, ModelCallPolicy, preflight_model_call

        model = self.settings.model
        class_map = {
            "public_metadata": DataClass.PUBLIC_METADATA,
            "abstract": DataClass.ABSTRACT,
            "open_fulltext": DataClass.OPEN_FULLTEXT,
            "licensed_fulltext": DataClass.LICENSED_FULLTEXT,
            "private_notes": DataClass.PRIVATE_NOTES,
            "sensitive": DataClass.SENSITIVE,
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
        preflight = preflight_model_call(model, policy, materials, purpose=purpose)
        if not preflight.allowed:
            details = []
            if preflight.blocked_material_ids:
                details.append("超出允许外发等级：" + "、".join(preflight.blocked_material_ids[:10]))
            details.extend(preflight.warnings)
            messagebox.showwarning("模型调用策略已阻断", "\n".join(details), parent=self)
            return False
        if preflight.estimated_cost is not None:
            cost = f"；估算上限约 {preflight.estimated_cost:.4f} {preflight.currency}"
        elif model.price_tiers:
            cost = "；当前输入长度未匹配已核验价格档位，费用待核验"
        else:
            cost = "；目录没有可靠的结构化价格，费用待核验"
        price_source = model.pricing_source or ("用户手工配置" if model.pricing_mode == "manual" else "未记录")
        summary = (
            f"将向 {model.provider_name} / {model.model} 发送 {len(materials)} 份材料。\n"
            f"预计输入约 {preflight.estimated_input_tokens:,} Token，最大输出 {preflight.output_token_limit:,} Token{cost}。\n"
            f"价格来源：{price_source}；核验/更新时间：{model.pricing_updated_at or '未记录'}。\n"
            f"最高允许材料等级：{policy.maximum_data_class.label}。"
        )
        return not preflight.requires_confirmation or messagebox.askyesno("确认数据外发与费用", summary + "\n\n是否继续？", parent=self)

    def _run_async(
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
                result = worker()
                error: Exception | None = None
            except Exception as caught:  # normalize worker errors at the UI boundary
                result = None
                error = caught

            def finish() -> None:
                button.state(["!disabled"])
                if error is not None:
                    status_var.set(f"失败：{error}")
                    messagebox.showerror("执行失败", str(error), parent=self)
                    return
                complete(result)

            try:
                self.after(0, finish)
            except tk.TclError:
                return

        threading.Thread(target=target, daemon=True).start()

    def _refresh_workflow_status(self, manifest: dict[str, Any] | None = None) -> None:
        if self.store is None:
            return
        manifest = manifest or self.store.load_manifest()
        workflow = manifest.get("workflow", {})
        stages = workflow.get("stages", {})
        labels = {
            "strategy": "检索策略",
            "search": "文献集",
            "reading": "精读",
            "report": "总结报告",
            "audit": "核验报告",
        }
        status_labels = {
            "locked": "锁定",
            "pending": "待处理",
            "in_progress": "进行中",
            "complete": "完成",
            "warning": "有警告",
        }
        summary = "  ·  ".join(
            f"{labels[key]}：{status_labels.get(str(stages.get(key)), stages.get(key, '待处理'))}"
            for key in ("strategy", "search", "reading", "report", "audit")
        )
        self.workflow_status_var.set(summary)
        current = str(workflow.get("current_stage") or "strategy")
        indices = {"strategy": 1, "search": 2, "reading": 3, "report": 4, "audit": 4}
        self.notebook.select(indices.get(current, 1))

    def _refresh_report_audit_status(
        self,
        manifest: dict[str, Any] | None = None,
        *,
        report_text: str | None = None,
    ) -> None:
        if self.store is None:
            return
        from .report_lifecycle import AuditState, audit_matches_current

        manifest = manifest or self.store.load_manifest()
        stages = manifest.get("workflow", {}).get("stages", {})
        report_text = report_text if report_text is not None else _text(self.report_text)
        ledger = self.store.load_json("report/claim_ledger.json", [])
        ledger = ledger if isinstance(ledger, list) else []
        if report_text:
            self.report_status_var.set(f"文献总结报告已载入；阶段状态：{stages.get('report', 'pending')}")
        else:
            self.report_status_var.set("等待精读确认后生成文献总结报告")
        audit_state = AuditState.from_dict(self.store.load_json("audit/audit_state.json", {}))
        if not audit_state:
            legacy = self.store.read_text("report/claim_citation_audit.md")
            self.audit_status_var.set("已载入旧版核验报告，建议针对当前总结报告重新核验" if legacy else "等待文献总结报告")
        elif audit_matches_current(audit_state, report_text, ledger):
            self.audit_status_var.set(f"核验报告有效；总体状态：{audit_state.overall_status}")
        else:
            self.audit_status_var.set("核验报告已过期：当前总结报告或论断台账已变化，请重新核验")

    def open_project_folder(self) -> None:
        if self.project_directory is None:
            messagebox.showinfo("尚无项目", "请先确认或打开一个调研项目。", parent=self)
            return
        try:
            subprocess.Popen(
                ["explorer.exe", str(self.project_directory)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            messagebox.showerror("无法打开文件夹", str(error), parent=self)

    # -- stage 3: search strategy --------------------------------------

    @staticmethod
    def _query_lines(widget: tk.Text) -> list[str]:
        values: list[str] = []
        for line in _text(widget).splitlines():
            value = line.strip().strip("`").strip()
            if not value or value.startswith("#"):
                continue
            if value.startswith(("- ", "* ")):
                value = value[2:].strip()
            if value and value not in values:
                values.append(value)
        return values

    def generate_strategy(self) -> None:
        try:
            brief, _store, _directory = self._require_project()
            mode = self.strategy_mode_var.get()
            if mode == "agent":
                client = self._model_client("search_strategy")
                from .llm_policy import DataClass, MaterialDescriptor

                materials = [MaterialDescriptor("research_brief", DataClass.PUBLIC_METADATA, len(str(brief.to_dict())), "调研需求")]
                if not self._confirm_external_model_call("search_strategy", materials):
                    return
            else:
                client = None
        except ValueError as error:
            messagebox.showwarning("无法生成检索策略", str(error), parent=self)
            return
        from .search_strategy import generate_agent_strategy, generate_local_strategy

        def worker() -> SearchStrategyBundle:
            if mode == "agent":
                assert client is not None
                return generate_agent_strategy(brief, client)
            return generate_local_strategy(brief)

        def complete(bundle: SearchStrategyBundle) -> None:
            self.strategy = bundle
            self._display_strategy()
            self.strategy_status_var.set(
                "Agent 检索策略已生成，等待人工确认"
                if mode == "agent"
                else "本地检索策略已生成，等待人工确认"
            )

        self._run_async(
            self.strategy_generate_button,
            self.strategy_status_var,
            "正在生成关键词树与宽/精检索式…",
            worker,
            complete,
        )

    def _display_strategy(self) -> None:
        if self.strategy is None:
            _set_text(self.keyword_tree_text, "")
            _set_text(self.broad_queries_text, "")
            _set_text(self.precision_queries_text, "")
            return
        from .search_strategy import render_keyword_tree_markdown

        _set_text(self.keyword_tree_text, render_keyword_tree_markdown(self.strategy))
        _set_text(self.broad_queries_text, "\n".join(self.strategy.broad_queries))
        _set_text(self.precision_queries_text, "\n".join(self.strategy.precision_queries))
        self.strategy_mode_var.set(self.strategy.generation_mode)
        if self.strategy.broad_queries and not self.search_query_var.get().strip():
            self.search_query_var.set(self.strategy.broad_queries[0])

    def confirm_strategy(self) -> None:
        try:
            _brief, store, _directory = self._require_project()
            if self.strategy is None:
                raise ValueError("请先生成关键词树与检索式。")
            broad = self._query_lines(self.broad_queries_text)
            precision = self._query_lines(self.precision_queries_text)
            if not broad or not precision:
                raise ValueError("宽检索和精检索都必须至少保留一条检索式。")
            from .search_strategy import build_source_queries

            self.strategy = replace(
                self.strategy,
                broad_queries=broad,
                precision_queries=precision,
                source_queries=build_source_queries(
                    broad,
                    precision,
                    start_year=self.strategy.start_year,
                    end_year=self.strategy.end_year,
                ),
            )
            from .search_strategy import (
                render_keyword_tree_markdown,
                render_search_strategies_markdown,
            )

            store.save_json(
                "search_strategy_data",
                "search/search_strategy.json",
                self.strategy.to_dict(),
            )
            # Preserve the user's edited keyword-tree Markdown verbatim.
            store.save_markdown(
                "keyword_tree",
                "search/keyword_tree.md",
                _text(self.keyword_tree_text) or render_keyword_tree_markdown(self.strategy),
            )
            store.save_markdown(
                "search_strategies",
                "search/search_strategies.md",
                render_search_strategies_markdown(self.strategy),
            )
            manifest = store.set_stage(
                "strategy",
                "complete",
                next_stage="search",
                message="关键词树与宽/精检索式已由用户确认",
            )
        except (OSError, ValueError) as error:
            messagebox.showwarning("无法确认检索策略", str(error), parent=self)
            return
        self.search_query_var.set(self.strategy.broad_queries[0])
        self.strategy_status_var.set("已确认并保存到项目文件夹")
        self.search_status_var.set("可执行多源元数据检索")
        self._refresh_workflow_status(manifest)

    # -- stage 4: literature and full text -----------------------------

    def _sync_source_defaults(self) -> None:
        config = self.settings.discovery
        self.source_openalex_var.set(config.openalex_enabled)
        self.source_crossref_var.set(config.crossref_enabled)
        self.source_zotero_var.set(self.settings.zotero.enabled)
        self.source_ima_var.set(self.settings.ima.enabled)
        self.search_limit_var.set(str(config.default_limit))

    def _selected_papers(self, tree: ttk.Treeview | None = None) -> list[PaperRecord]:
        tree = tree or self.paper_tree
        selected = set(tree.selection())
        return [paper for paper in self.papers if paper.record_id in selected]

    def _save_papers(self) -> None:
        if self.store is None:
            return
        from .search_engine import render_bibtex, render_literature_catalog

        self.store.save_json(
            "papers_data",
            "search/papers.json",
            [paper.to_dict() for paper in self.papers],
        )
        self.store.save_markdown(
            "literature_catalog",
            "search/literature_catalog.md",
            render_literature_catalog(self.papers),
        )
        self.store.save_markdown(
            "references_bib",
            "search/references.bib",
            render_bibtex(self.papers),
        )
        self._update_evidence_index()

    def _refresh_paper_tree(self) -> None:
        self.paper_tree.delete(*self.paper_tree.get_children())
        latest_screening = {item.paper_id: item for item in self.screening_decisions}
        for paper in self.papers:
            scorecard = paper.extra.get("core_score") if isinstance(paper.extra.get("core_score"), dict) else {}
            reasons = scorecard.get("reasons") if isinstance(scorecard.get("reasons"), list) else []
            decision = latest_screening.get(paper.record_id)
            self.paper_tree.insert(
                "",
                "end",
                iid=paper.record_id,
                values=(
                    "★" if paper.record_id in self.core_paper_ids else "",
                    ({"include": "纳入", "exclude": "排除", "uncertain": "待定"}.get(decision.decision.value, "") if decision else ""),
                    paper.year or "",
                    paper.title,
                    ", ".join(paper.sources or [paper.source]),
                    paper.doi,
                    paper.evidence_level.label,
                    paper.access_status,
                    f"{paper.relevance_score * 100:.1f}",
                    "；".join(reasons[:2]),
                ),
            )

    def _save_review_records(self) -> None:
        if self.store is None:
            return
        from .review_protocol import calculate_prisma, render_prisma_markdown

        self.store.save_json("screening_decisions", "review/screening.json", [item.to_dict() for item in self.screening_decisions])
        self.store.save_json("quality_assessments", "review/quality_assessments.json", [item.to_dict() for item in self.quality_assessments.values()])
        duplicate_count = sum(max(0, len(paper.sources) - 1) for paper in self.papers)
        unavailable = [paper.record_id for paper in self.papers if paper.evidence_level is not EvidenceLevel.FULL_TEXT]
        flow = calculate_prisma([paper.record_id for paper in self.papers], self.screening_decisions, duplicate_count=duplicate_count, full_text_unavailable_ids=unavailable)
        self.store.save_json("prisma_flow_data", "review/prisma.json", asdict(flow))
        self.store.save_markdown("prisma_flow", "review/prisma.md", render_prisma_markdown(flow))

    def mark_screening(self, decision_value: str) -> None:
        selected = self._selected_papers()
        if not selected:
            messagebox.showinfo("请选择文献", "请先选择需要筛选的论文。", parent=self)
            return
        from .review_protocol import ScreeningDecision, ScreeningDecisionValue, ScreeningStage

        reason = ""
        detail = ""
        if decision_value == "exclude":
            reason = simpledialog.askstring("排除理由", "请输入标准化排除理由（例如 wrong_population）：", parent=self) or ""
            if not reason.strip():
                return
            detail = simpledialog.askstring("补充说明", "可选：记录具体排除依据。", parent=self) or ""
        latest = {(item.paper_id, item.stage): item for item in self.screening_decisions}
        for paper in selected:
            stage = ScreeningStage.FULL_TEXT if paper.evidence_level is EvidenceLevel.FULL_TEXT else ScreeningStage.TITLE_ABSTRACT
            latest[(paper.record_id, stage)] = ScreeningDecision(
                paper.record_id, stage, ScreeningDecisionValue(decision_value), reason_code=reason, reason_detail=detail
            )
        self.screening_decisions = list(latest.values())
        self._save_review_records()
        self._refresh_paper_tree()
        self.search_status_var.set(f"已记录 {len(selected)} 篇文献的逐条筛选决定")

    def score_selected_quality(self) -> None:
        selected = self._selected_papers()
        if not selected:
            messagebox.showinfo("请选择文献", "请先选择需要评分的论文。", parent=self)
            return
        score = simpledialog.askinteger("研究质量评分", "请输入总体质量评分（0=低，1=中，2=高）：", parent=self, minvalue=0, maxvalue=2)
        if score is None:
            return
        note = simpledialog.askstring("评分依据", "请简要记录偏倚风险、设计完整性或报告质量依据：", parent=self) or ""
        from .review_protocol import QualityAssessment

        for paper in selected:
            assessment = QualityAssessment(paper.record_id, "custom-3-level", {"overall": score}, {"overall": note})
            self.quality_assessments[paper.record_id] = assessment
            paper.extra["quality_assessment"] = assessment.to_dict()
        self._save_review_records()
        self._save_papers()
        self.search_status_var.set(f"已记录 {len(selected)} 篇论文的研究质量评分")

    def enrich_zotero(self) -> None:
        from html import escape

        selected = self._selected_papers()
        if not selected:
            messagebox.showinfo("请选择 Zotero 文献", "请选择至少一篇来自 Zotero 的论文。", parent=self)
            return
        if not self.settings.zotero.allow_confirmed_writes:
            messagebox.showwarning("Zotero 保持只读", "请先在数据库设置中启用“允许预览后确认写入标签/笔记”。", parent=self)
            return
        connector = ZoteroConnector(self.settings.zotero)
        proposals = []
        skipped = []
        project_tag = f"Review Writer/{self.project_directory.name if self.project_directory else 'Project'}"
        try:
            for paper in selected:
                item_key = str((paper.extra.get("source_ids") or {}).get("Zotero") or (paper.source_id if paper.source == "Zotero" else ""))
                if not item_key:
                    skipped.append(paper.title)
                    continue
                note = self._note_by_paper_id(paper.record_id)
                note_html = ""
                if note is not None:
                    findings = "".join(f"<li>{escape(item)}</li>" for item in note.findings)
                    note_html = f"<h2>Review Writer 精读摘记</h2><p>证据等级：{note.evidence_level.label}</p><ul>{findings}</ul><p>请回到项目证据块核对原文定位。</p>"
                proposals.append(connector.propose_enrichment(item_key, add_tags=[project_tag], note_html=note_html))
        except Exception as error:
            messagebox.showerror("无法生成 Zotero 变更预览", str(error), parent=self)
            return
        if not proposals:
            messagebox.showinfo("没有可写入条目", "所选论文没有可识别的 Zotero 条目 ID。", parent=self)
            return
        preview = "\n".join(f"• {item.title}：新增标签 {', '.join(item.add_tags) or '无'}；新增笔记 {'是' if item.note_html else '否'}" for item in proposals)
        if skipped:
            preview += f"\n\n另有 {len(skipped)} 篇非 Zotero 条目已跳过。"
        if not messagebox.askyesno("确认 Zotero 变更清单", preview + "\n\n写入前会校验条目版本，避免覆盖用户刚刚进行的修改。是否确认？", parent=self):
            return
        try:
            receipts = connector.apply_enrichment(proposals, user_confirmed=True)
            if self.store is not None:
                self.store.save_json("zotero_write_receipts", "runs/zotero_write_receipts.json", receipts)
        except Exception as error:
            messagebox.showerror("Zotero 写入失败", str(error), parent=self)
            return
        self.search_status_var.set(f"Zotero 写入完成：{len(receipts)} 个条目；回执已保存")

    def run_search(self) -> None:
        try:
            brief, store, _directory = self._require_project()
            if self.strategy is None:
                raise ValueError("请先生成并确认关键词树与检索式。")
            manifest = store.load_manifest()
            if manifest.get("workflow", {}).get("stages", {}).get("search") == "locked":
                raise ValueError("检索阶段尚未解锁，请先确认检索策略。")
            query = self.search_query_var.get().strip()
            if not query:
                raise ValueError("本次检索式不能为空。")
            limit = int(self.search_limit_var.get())
            if not 1 <= limit <= 100:
                raise ValueError("每个来源返回上限应在 1—100 之间。")
            sources = self._selected_source_names()
            if "Zotero" in sources and not self.settings.zotero.enabled:
                raise ValueError("Zotero 数据源未在数据库设置中启用。")
            if "IMA" in sources and not self.settings.ima.enabled:
                raise ValueError("IMA 数据源未在数据库设置中启用。")
            if not sources:
                raise ValueError("请至少选择一个已配置的数据源。")
            if self.task_store is None or self.task_worker is None:
                raise ValueError("持久化任务系统尚未初始化，请重新打开项目。")
        except (TypeError, ValueError) as error:
            messagebox.showwarning("无法执行检索", str(error), parent=self)
            return

        task = self.task_store.enqueue(
            "literature_search",
            {
                "query": query,
                "limit": limit,
                "start_year": brief.start_year,
                "end_year": brief.end_year,
                "sources": sources,
            },
            max_attempts=3,
        )
        self.task_worker.wake()
        self.search_status_var.set(f"检索任务已入队：{task.task_id}；可在“任务与日志”中查看、取消或重试")
        self.notebook.select(self.search_tab)
        self._refresh_tasks()

    def toggle_core_papers(self) -> None:
        selected = [paper.record_id for paper in self._selected_papers()]
        if not selected:
            messagebox.showinfo("请选择文献", "请先在结果表中选择一篇或多篇文献。", parent=self)
            return
        current = set(self.core_paper_ids)
        for paper_id in selected:
            if paper_id in current:
                current.remove(paper_id)
            else:
                current.add(paper_id)
        self.core_paper_ids = [paper.record_id for paper in self.papers if paper.record_id in current]
        if self.store:
            self.store.set_paper_selection(
                selected_paper_ids=[paper.record_id for paper in self.papers],
                core_paper_ids=self.core_paper_ids,
            )
        self._refresh_paper_tree()
        self._refresh_reading_tree()

    def attach_local_pdf(self) -> None:
        selected = self._selected_papers()
        if len(selected) != 1:
            messagebox.showinfo("请选择一篇文献", "关联本地 PDF 时请只选择一篇文献。", parent=self)
            return
        try:
            _brief, store, directory = self._require_project()
        except ValueError as error:
            messagebox.showwarning("无法关联 PDF", str(error), parent=self)
            return
        source = filedialog.askopenfilename(
            parent=self,
            title="选择已经合法获得的全文或补充材料",
            filetypes=(("支持的文档", "*.pdf *.html *.htm *.txt *.md"), ("所有文件", "*.*")),
        )
        if not source:
            return
        paper = selected[0]
        role = "supplement" if messagebox.askyesno("文档角色", "所选文件是否为补充材料？\n选择“否”表示论文主全文。", parent=self) else "main"
        suffix = Path(source).suffix.casefold()
        folder = "SupportingInformation" if role == "supplement" else ("PDFs" if suffix == ".pdf" else "HTML")
        target = directory / "fulltext" / folder / f"{paper.record_id}{'_supplement' if role == 'supplement' else ''}{suffix}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            from .document_ingest import DocumentIngestor

            result = DocumentIngestor().ingest(paper, target, role=role)
            if result.asset.verification_status == "rejected":
                target.unlink(missing_ok=True)
                raise ValueError("文档校验失败：" + "；".join(result.asset.warnings))
            evidence = (
                EvidenceLevel.FULL_TEXT
                if role == "main" and result.asset.verification_status == "verified" and result.evidence_blocks
                else paper.evidence_level
            )
            extra = dict(paper.extra)
            assets = list(extra.get("document_assets") or [])
            assets.append(result.asset.to_dict())
            extra["document_assets"] = assets
            if role == "main":
                extra["local_file"] = str(target)
            updated = replace(
                paper,
                evidence_level=evidence,
                access_status=(
                    "本地全文已验证并提取可追溯证据块"
                    if evidence == EvidenceLevel.FULL_TEXT
                    else ("补充材料已登记，不单独提升全文证据等级" if role == "supplement" else "文档需 OCR/人工核对，未作为全文证据")
                ),
                document_asset_ids=[*paper.document_asset_ids, result.asset.asset_id],
                extra=extra,
            )
            self.papers = [updated if item.record_id == paper.record_id else item for item in self.papers]
            self._save_papers()
            store.save_json(f"document_asset_{result.asset.asset_id}", f"fulltext/{result.asset.asset_id}.json", result.asset.to_dict())
            store.save_json(f"document_blocks_{result.asset.asset_id}", f"fulltext/extracted_text/{result.asset.asset_id}_blocks.json", [block.to_dict() for block in result.evidence_blocks])
            if result.extracted_text:
                store.save_markdown(f"document_text_{result.asset.asset_id}", f"fulltext/extracted_text/{result.asset.asset_id}.txt", result.extracted_text)
        except (OSError, ValueError) as error:
            messagebox.showerror("文档校验失败", str(error), parent=self)
            return
        self._refresh_paper_tree()
        self._refresh_reading_tree()
        self.search_status_var.set(f"已登记并校验文档：{paper.title}；未通过校验的材料不会升级证据等级")

    def download_selected(self) -> None:
        selected = self._selected_papers()
        if not selected:
            messagebox.showinfo("请选择文献", "请先选择需要获取全文的论文。", parent=self)
            return
        requires_library = any(
            not bool((paper.extra or {}).get("is_open_access")) for paper in selected
        )
        if requires_library and not self.settings.library.enabled:
            messagebox.showwarning(
                "机构资源未启用",
                "所选文献中包含非开放获取候选。请先在“数据库设置”中启用机构资源并配置实际入口。",
                parent=self,
            )
            return
        limit = min(self.settings.library.max_batch_size, 20)
        if len(selected) > limit:
            messagebox.showwarning("超过安全批次", f"当前配置单批最多处理 {limit} 篇。", parent=self)
            return
        if not messagebox.askyesno(
            "确认获取全文",
            f"将处理 {len(selected)} 篇已选论文。仅使用开放获取或你已授权的浏览器会话；遇到登录、验证码或出版商检查会停止并提示。是否继续？",
            parent=self,
        ):
            return
        try:
            self._require_project()
            if self.task_store is None or self.task_worker is None:
                raise ValueError("持久化任务系统尚未初始化。")
        except (OSError, ValueError) as error:
            messagebox.showerror("无法启动全文获取", str(error), parent=self)
            return

        task = self.task_store.enqueue("fulltext_download", {"paper_ids": [paper.record_id for paper in selected]}, max_attempts=2)
        self.task_worker.wake()
        self.search_status_var.set(f"全文任务已入队：{task.task_id}；逐篇保存检查点")
        self._refresh_tasks()

    def run_ocr_selected(self) -> None:
        selected = self._selected_papers()
        if len(selected) != 1:
            messagebox.showinfo("请选择扫描件", "运行 OCR 时请只选择一篇已关联 PDF 的论文。", parent=self)
            return
        paper = selected[0]
        source = Path(str(paper.extra.get("local_file") or ""))
        if source.suffix.casefold() != ".pdf" or not source.is_file():
            messagebox.showwarning("没有可用 PDF", "所选论文没有已登记的本地 PDF。", parent=self)
            return
        if self.task_store is None or self.task_worker is None:
            messagebox.showwarning("任务系统未就绪", "请重新打开项目后再试。", parent=self)
            return
        if not messagebox.askyesno("确认本地 OCR", "OCR 将在本机生成新的 PDF，并在完成后重新执行题名、页数和文本层校验。原文件不会被覆盖。是否继续？", parent=self):
            return
        task = self.task_store.enqueue("ocr_document", {"paper_id": paper.record_id, "source_path": str(source)}, max_attempts=1)
        self.task_worker.wake()
        self.search_status_var.set(f"OCR 任务已入队：{task.task_id}")
        self._refresh_tasks()

    def confirm_papers(self) -> None:
        try:
            _brief, store, _directory = self._require_project()
            if not self.papers:
                raise ValueError("请先完成至少一次文献检索。")
            if not self.core_paper_ids:
                raise ValueError("请在文献表中至少标记一篇核心论文。")
            self._save_papers()
            store.set_paper_selection(
                selected_paper_ids=[paper.record_id for paper in self.papers],
                core_paper_ids=self.core_paper_ids,
            )
            manifest = store.set_stage(
                "search",
                "complete",
                next_stage="reading",
                message=f"确认 {len(self.papers)} 篇文献，其中 {len(self.core_paper_ids)} 篇核心论文",
            )
        except (OSError, ValueError) as error:
            messagebox.showwarning("无法确认文献集", str(error), parent=self)
            return
        self.search_status_var.set("文献目录已确认；未获得全文的论文保留明确证据等级")
        self._refresh_reading_tree()
        self._refresh_workflow_status(manifest)

    # -- stage 5: structured reading -----------------------------------

    def _paper_by_id(self, paper_id: str) -> PaperRecord | None:
        return next((paper for paper in self.papers if paper.record_id == paper_id), None)

    def _note_by_paper_id(self, paper_id: str) -> ReadingNote | None:
        return next((note for note in self.reading_notes if note.paper_id == paper_id), None)

    def _refresh_reading_tree(self) -> None:
        self.reading_tree.delete(*self.reading_tree.get_children())
        for paper_id in self.core_paper_ids:
            paper = self._paper_by_id(paper_id)
            if paper is None:
                continue
            note = self._note_by_paper_id(paper_id)
            self.reading_tree.insert(
                "",
                "end",
                iid=paper_id,
                values=(
                    paper.title,
                    paper.evidence_level.label,
                    "已生成" if note else "待精读",
                ),
            )

    def _show_selected_reading(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        selection = self.reading_tree.selection()
        if not selection:
            return
        paper = self._paper_by_id(selection[0])
        note = self._note_by_paper_id(selection[0])
        if paper is None:
            return
        if note is None:
            _set_text(
                self.reading_preview,
                f"# {paper.title}\n\n尚未生成结构化精读卡。\n\n当前证据等级：{paper.evidence_level.label}",
            )
            return
        from .reader import render_reading_note_markdown

        _set_text(self.reading_preview, render_reading_note_markdown(paper, note))

    def _save_reading_notes(self) -> None:
        if self.store is None:
            return
        from .reader import render_reading_note_markdown

        self.store.save_json(
            "reading_notes_index",
            "reading_notes/index.json",
            [note.to_dict() for note in self.reading_notes],
        )
        for note in self.reading_notes:
            paper = self._paper_by_id(note.paper_id)
            if paper is None:
                continue
            self.store.save_json(
                f"reading_note_data_{note.paper_id}",
                f"reading_notes/{note.paper_id}.json",
                note.to_dict(),
            )
            self.store.save_markdown(
                f"reading_note_{note.paper_id}",
                f"reading_notes/{note.paper_id}.md",
                render_reading_note_markdown(paper, note),
            )
        self._update_evidence_index()

    def generate_reading_notes(self) -> None:
        try:
            _brief, store, _directory = self._require_project()
            if store.load_manifest().get("workflow", {}).get("stages", {}).get("reading") == "locked":
                raise ValueError("精读阶段尚未解锁，请先确认文献集。")
            selection = list(self.reading_tree.selection())
            paper_ids = selection or list(self.core_paper_ids)
            if not [paper_id for paper_id in paper_ids if self._paper_by_id(paper_id)]:
                raise ValueError("请先确认核心论文，或在精读表中选择论文。")
            mode = self.reading_mode_var.get()
            if mode == "agent":
                self._model_client()
                from .llm_policy import DataClass, MaterialDescriptor

                materials = []
                for paper_id in paper_ids:
                    paper = self._paper_by_id(paper_id)
                    if paper is None:
                        continue
                    if paper.evidence_level is EvidenceLevel.FULL_TEXT:
                        data_class = DataClass.OPEN_FULLTEXT if paper.extra.get("is_open_access") else DataClass.LICENSED_FULLTEXT
                        characters = int((paper.extra.get("pdf_verification") or {}).get("extracted_characters") or len(paper.abstract))
                    elif paper.evidence_level is EvidenceLevel.ABSTRACT_ONLY:
                        data_class, characters = DataClass.ABSTRACT, len(paper.abstract)
                    else:
                        data_class, characters = DataClass.PUBLIC_METADATA, len(paper.title) + 200
                    materials.append(MaterialDescriptor(paper.record_id, data_class, characters, paper.title))
                if not self._confirm_external_model_call("reading", materials):
                    return
            if self.task_store is None or self.task_worker is None:
                raise ValueError("持久化任务系统尚未初始化。")
        except ValueError as error:
            messagebox.showwarning("无法开始精读", str(error), parent=self)
            return
        task = self.task_store.enqueue("structured_reading", {"paper_ids": paper_ids, "mode": mode}, max_attempts=2)
        self.task_worker.wake()
        self.reading_status_var.set(f"精读任务已入队：{task.task_id}；每篇论文完成后保存检查点")
        self._refresh_tasks()

    def confirm_readings(self) -> None:
        try:
            _brief, store, _directory = self._require_project()
            completed = {note.paper_id for note in self.reading_notes}
            missing = [paper_id for paper_id in self.core_paper_ids if paper_id not in completed]
            if missing:
                raise ValueError(f"还有 {len(missing)} 篇核心论文未生成结构化精读卡。")
            if not self.reading_notes:
                raise ValueError("至少需要一篇结构化精读卡。")
            self._save_reading_notes()
            manifest = store.set_stage(
                "reading",
                "complete",
                next_stage="report",
                message=f"用户确认 {len(self.reading_notes)} 篇结构化精读卡",
            )
        except (OSError, ValueError) as error:
            messagebox.showwarning("无法确认精读", str(error), parent=self)
            return
        self.reading_status_var.set("精读卡已确认，可先生成文献总结报告，再单独运行核验")
        self._refresh_workflow_status(manifest)

    # -- stage 6: independent summary and verification -----------------

    def _load_claim_ledger(self) -> tuple[Any, ...]:
        if self.store is None:
            return ()
        from .reporting import ClaimLedgerEntry

        payload = self.store.load_json("report/claim_ledger.json", [])
        if not isinstance(payload, list):
            return ()
        return tuple(
            ClaimLedgerEntry(
                claim_id=str(item.get("claim_id") or ""),
                core_question_index=int(item.get("core_question_index") or 0),
                claim_text=str(item.get("claim_text") or ""),
                citation_ids=tuple(item.get("citation_ids") or ()),
                evidence_block_ids=tuple(item.get("evidence_block_ids") or ()),
                evidence_level=str(item.get("evidence_level") or "仅元数据"),
                source=str(item.get("source") or "deterministic"),
            )
            for item in payload
            if isinstance(item, dict)
        )

    def _save_summary_bundle(
        self,
        bundle: Any,
        *,
        report_text: str,
        mode: str,
        template: str,
    ) -> None:
        if self.store is None:
            return
        from .report_lifecycle import ReportState, ledger_hash, nonclaim_text_hash, text_hash

        self.store.save_markdown("literature_summary", "report/literature_summary.md", report_text)
        self.store.save_markdown("literature_summary_generated", "report/literature_summary.generated.md", report_text)
        # Compatibility mirror for existing projects and external tooling.
        self.store.save_markdown("research_report_legacy", "report/research_report.md", report_text)
        self.store.save_markdown(
            "report_references_bib",
            "report/references.bib",
            bundle.references_bib,
        )
        self.store.save_json(
            "claim_ledger",
            "report/claim_ledger.json",
            [asdict(item) for item in bundle.claim_ledger],
        )
        state = ReportState(
            report_hash=text_hash(report_text),
            ledger_hash=ledger_hash(bundle.claim_ledger),
            generation_mode=mode,
            template=template,
            nonclaim_hash=nonclaim_text_hash(report_text),
        )
        self.store.save_json(
            "report_state",
            "report/report_state.json",
            state.to_dict(),
        )

    def _mark_manual_claim_edits(
        self,
        bundle: Any,
        changed_claim_ids: tuple[str, ...],
        *,
        nonclaim_changed: bool = False,
    ) -> Any:
        from .reporting import AuditCheck, AuditReport, ClaimAuditResult, render_claim_citation_audit

        changed = set(changed_claim_ids)
        rank = {"pass": 0, "warning": 1, "manual_needed": 2, "fail": 3}
        results = []
        for result in bundle.audit.results:
            if result.claim_id not in changed and not nonclaim_changed:
                results.append(result)
                continue
            if nonclaim_changed and result.claim_id in changed:
                detail = "该论断及论断台账之外的报告正文均被人工修改；当前自动核验不能覆盖全部新增语义。"
            elif nonclaim_changed:
                detail = "论断台账之外的报告正文被人工修改；当前自动核验不能确认新增正文均有证据支持。"
            else:
                detail = "该论断在报告中被人工改写；本地结构核验不能确认改写后的语义仍受原证据支持。"
            checks = (*result.checks, AuditCheck(
                "manual_edit_semantics",
                "manual_needed",
                detail,
            ))
            status = max((check.status for check in checks), key=rank.get)
            results.append(ClaimAuditResult(result.claim_id, status, result.citation_ids, checks))
        audit = AuditReport(
            max((item.status for item in results), key=rank.get, default="pass"),
            tuple(results),
        )
        return replace(bundle, audit=audit, claim_citation_audit=render_claim_citation_audit(audit))

    def _save_verification_bundle(
        self,
        bundle: Any,
        *,
        report_text: str,
        claims: tuple[Any, ...],
        mode: str,
    ) -> None:
        if self.store is None:
            return
        from .provenance import DeliveryPolicy, evaluate_delivery_gate
        from .report_lifecycle import AuditState, ledger_hash, text_hash

        gate = evaluate_delivery_gate(bundle.audit.results, policy=DeliveryPolicy.STRICT)
        self.store.save_markdown("verification_report", "audit/verification_report.md", bundle.claim_citation_audit)
        self.store.save_markdown("claim_citation_audit_legacy", "report/claim_citation_audit.md", bundle.claim_citation_audit)
        self.store.save_json("verification_data", "audit/verification_report.json", asdict(bundle.audit))
        self.store.save_json("claim_audit_data_legacy", "report/claim_citation_audit.json", asdict(bundle.audit))
        self.store.save_json("claim_ledger", "report/claim_ledger.json", [asdict(item) for item in claims])
        gate_payload = {
            "allowed": gate.allowed,
            "policy": gate.policy,
            "blocking_claim_ids": list(gate.blocking_claim_ids),
            "warning_claim_ids": list(gate.warning_claim_ids),
            "message": gate.message,
        }
        self.store.save_json("delivery_gate", "audit/delivery_gate.json", gate_payload)
        self.store.save_json("delivery_gate_legacy", "report/delivery_gate.json", gate_payload)
        state = AuditState(
            report_hash=text_hash(report_text),
            ledger_hash=ledger_hash(claims),
            audit_mode=mode,
            overall_status=bundle.audit.overall_status,
        )
        self.store.save_json(
            "audit_state",
            "audit/audit_state.json",
            state.to_dict(),
        )

    def generate_report(self) -> None:
        try:
            _brief, store, _directory = self._require_project()
            if store.load_manifest().get("workflow", {}).get("stages", {}).get("report") == "locked":
                raise ValueError("报告阶段尚未解锁，请先确认结构化精读卡。")
            if not self.reading_notes:
                raise ValueError("请先生成并确认核心论文精读卡。")
            if self.strategy is None:
                raise ValueError("项目缺少已确认的检索策略。")
            mode = self.report_mode_var.get()
            if mode == "agent":
                self._model_client()
                from .llm_policy import DataClass, MaterialDescriptor

                materials = []
                for note in self.reading_notes:
                    paper = self._paper_by_id(note.paper_id)
                    level = note.evidence_level
                    data_class = (
                        DataClass.OPEN_FULLTEXT if level is EvidenceLevel.FULL_TEXT and paper and paper.extra.get("is_open_access")
                        else DataClass.LICENSED_FULLTEXT if level is EvidenceLevel.FULL_TEXT
                        else DataClass.ABSTRACT if level is EvidenceLevel.ABSTRACT_ONLY
                        else DataClass.PUBLIC_METADATA
                    )
                    characters = sum(len(block.text) for block in note.evidence_blocks) + 300
                    materials.append(MaterialDescriptor(note.paper_id, data_class, characters, paper.title if paper else note.paper_id))
                if not self._confirm_external_model_call("synthesis", materials):
                    return
            if self.task_store is None or self.task_worker is None:
                raise ValueError("持久化任务系统尚未初始化。")
        except ValueError as error:
            messagebox.showwarning("无法生成报告", str(error), parent=self)
            return
        task = self.task_store.enqueue("report_generation", {"mode": mode, "template": self.report_template_var.get()}, max_attempts=2)
        self.task_worker.wake()
        self.report_status_var.set(f"文献总结任务已入队：{task.task_id}；核验不会自动启动")
        self._refresh_tasks()

    def save_report_draft(self, *, silent: bool = False) -> bool:
        try:
            _brief, store, _directory = self._require_project()
            report_text = _text(self.report_text)
            if not report_text:
                raise ValueError("当前文献总结报告为空。")
            store.save_markdown("literature_summary", "report/literature_summary.md", report_text)
            store.save_markdown("research_report_legacy", "report/research_report.md", report_text)
            claims = self._load_claim_ledger()
            from .report_lifecycle import AuditState, audit_matches_current

            audit_state = AuditState.from_dict(store.load_json("audit/audit_state.json", {}))
            current = audit_matches_current(audit_state, report_text, claims)
            store.set_stage("report", "complete", message="用户保存文献总结草稿")
            if not current:
                store.set_stage("audit", "pending", message="总结报告已变化，旧核验结果过期")
        except (OSError, TypeError, ValueError) as error:
            if not silent:
                messagebox.showwarning("无法保存总结报告", str(error), parent=self)
            return False
        self.report_text.edit_modified(False)
        self.report_status_var.set("文献总结草稿已保存")
        self.audit_status_var.set("核验报告仍然有效" if current else "核验报告已过期，请重新生成")
        self._refresh_workflow_status()
        return True

    def generate_audit(self) -> None:
        if not self.save_report_draft(silent=True):
            messagebox.showwarning("无法生成核验报告", "请先生成并保存文献总结报告。", parent=self)
            return
        try:
            _brief, store, _directory = self._require_project()
            report_text = _text(self.report_text)
            claims = self._load_claim_ledger()
            if not claims:
                raise ValueError("项目中没有可重用的论断台账，请先生成文献总结报告。")
            from .report_lifecycle import synchronize_claims_from_report

            synchronized = synchronize_claims_from_report(report_text, claims)
            if synchronized.errors:
                raise ValueError("；".join(synchronized.errors))
            mode = self.audit_mode_var.get()
            if mode == "agent":
                self._model_client()
                from .llm_policy import DataClass, MaterialDescriptor

                materials = []
                for note in self.reading_notes:
                    paper = self._paper_by_id(note.paper_id)
                    level = note.evidence_level
                    data_class = (
                        DataClass.OPEN_FULLTEXT if level is EvidenceLevel.FULL_TEXT and paper and paper.extra.get("is_open_access")
                        else DataClass.LICENSED_FULLTEXT if level is EvidenceLevel.FULL_TEXT
                        else DataClass.ABSTRACT if level is EvidenceLevel.ABSTRACT_ONLY
                        else DataClass.PUBLIC_METADATA
                    )
                    materials.append(MaterialDescriptor(note.paper_id, data_class, sum(len(block.text) for block in note.evidence_blocks) + 300, paper.title if paper else note.paper_id))
                if not self._confirm_external_model_call("audit", materials):
                    return
            if self.task_store is None or self.task_worker is None:
                raise ValueError("持久化任务系统尚未初始化。")
        except ValueError as error:
            messagebox.showwarning("无法生成核验报告", str(error), parent=self)
            return
        task = self.task_store.enqueue("audit_generation", {"mode": mode}, max_attempts=2)
        self.task_worker.wake()
        store.set_stage("audit", "pending", message=f"核验任务已入队：{task.task_id}")
        self.audit_status_var.set(f"核验任务已入队：{task.task_id}；不会重新生成总结报告")
        self._refresh_tasks()

    def reaudit_report(self) -> None:
        """Queue a new independent audit using the selected audit mode."""

        self.generate_audit()

    def export_report(self) -> None:
        try:
            _brief, store, directory = self._require_project()
            if not self.save_report_draft(silent=True):
                raise ValueError("请先生成并保存文献总结报告。")
            report_text = _text(self.report_text)
            if not report_text:
                raise ValueError("请先生成文献总结报告。")
            from .report_lifecycle import AuditState, audit_matches_current

            ledger = store.load_json("report/claim_ledger.json", [])
            ledger = ledger if isinstance(ledger, list) else []
            audit_state = AuditState.from_dict(store.load_json("audit/audit_state.json", {}))
            audit_current = audit_matches_current(audit_state, report_text, ledger)
            audit = store.load_json("audit/verification_report.json", {})
            if not isinstance(audit, dict):
                audit = store.load_json("report/claim_citation_audit.json", {})
            audit_items = audit.get("results", []) if isinstance(audit, dict) else []
            extension = simpledialog.askstring("导出格式", "请输入格式：md、docx 或 pdf", parent=self, initialvalue="docx")
            if not extension:
                return
            extension = extension.strip().casefold()
            if extension not in {"md", "markdown", "docx", "pdf"}:
                raise ValueError("支持的报告格式为 md、docx、pdf。")
            suffix = ".md" if extension in {"md", "markdown"} else f".{extension}"
            target = filedialog.asksaveasfilename(parent=self, title="导出文献总结报告", initialdir=str(directory / "exports"), initialfile=f"literature_summary{suffix}", defaultextension=suffix)
            if not target:
                return
            from .exports import export_markdown_document, export_verified_report
            from .provenance import DeliveryPolicy

            if not audit_current:
                if not messagebox.askyesno(
                    "尚无有效核验报告",
                    "当前总结报告尚未核验，或核验结果已因报告修改而过期。是否导出带醒目标记的草稿？",
                    parent=self,
                ):
                    return
                draft = "> ⚠ **未核验草稿**：本文件没有与当前正文及论断台账匹配的有效核验报告，不得标记为已核验最终报告。\n\n" + report_text
                export_markdown_document(draft, Path(target), format=extension, title="文献总结报告（未核验草稿）")
            else:
                try:
                    export_verified_report(report_text, audit_items, Path(target), format=extension, policy=DeliveryPolicy.STRICT)
                except ValueError as gate_error:
                    if not messagebox.askyesno("严格交付已阻断", f"{gate_error}\n\n是否改为导出带醒目警告的版本？", parent=self):
                        return
                    export_verified_report(report_text, audit_items, Path(target), format=extension, policy=DeliveryPolicy.WARN)
            self.report_status_var.set(f"已导出：{target}")
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("导出失败", str(error), parent=self)

    def export_audit_report(self) -> None:
        try:
            _brief, store, directory = self._require_project()
            audit_text = _text(self.audit_text)
            if not audit_text:
                raise ValueError("请先生成核验报告。")
            report_text = _text(self.report_text)
            ledger = store.load_json("report/claim_ledger.json", [])
            ledger = ledger if isinstance(ledger, list) else []
            from .report_lifecycle import AuditState, audit_matches_current

            state = AuditState.from_dict(store.load_json("audit/audit_state.json", {}))
            if not audit_matches_current(state, report_text, ledger):
                audit_text = "> ⚠ **历史核验结果**：该核验报告与当前总结报告或论断台账不一致，请重新核验。\n\n" + audit_text
            extension = simpledialog.askstring("导出格式", "请输入格式：md、docx 或 pdf", parent=self, initialvalue="docx")
            if not extension:
                return
            extension = extension.strip().casefold()
            if extension not in {"md", "markdown", "docx", "pdf"}:
                raise ValueError("支持的核验报告格式为 md、docx、pdf。")
            suffix = ".md" if extension in {"md", "markdown"} else f".{extension}"
            target = filedialog.asksaveasfilename(parent=self, title="导出核验报告", initialdir=str(directory / "exports"), initialfile=f"verification_report{suffix}", defaultextension=suffix)
            if not target:
                return
            from .exports import export_verification_report

            export_verification_report(audit_text, Path(target), format=extension)
            self.audit_status_var.set(f"核验报告已导出：{target}")
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("核验报告导出失败", str(error), parent=self)

    def export_ris(self) -> None:
        try:
            _brief, _store, directory = self._require_project()
            target = filedialog.asksaveasfilename(parent=self, title="导出 RIS", initialdir=str(directory / "exports"), initialfile="references.ris", defaultextension=".ris")
            if not target:
                return
            from .exports import render_ris

            Path(target).write_text(render_ris(self.papers), encoding="utf-8")
            self.report_status_var.set(f"RIS 已导出：{target}")
        except (OSError, ValueError) as error:
            messagebox.showerror("RIS 导出失败", str(error), parent=self)
