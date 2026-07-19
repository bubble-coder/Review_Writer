"""Durable project workflow storage for research stages 3 through 6.

The store deliberately keeps credentials out of project folders.  It owns only
research inputs, public bibliographic metadata, local artifact paths, evidence
notes, reports, and an append-only event log.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any


WORKFLOW_SCHEMA_VERSION = 4


LEGACY_AUDIT_PATHS = (
    "report/claim_citation_audit.md",
    "report/claim_citation_audit.json",
    "report/delivery_gate.json",
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


class WorkflowStore:
    """Read and write workflow artifacts under one application-owned project."""

    DEFAULT_WORKFLOW: dict[str, Any] = {
        "current_stage": "strategy",
        "stages": {
            "strategy": "pending",
            "search": "locked",
            "reading": "locked",
            "report": "locked",
            "audit": "locked",
        },
        "selected_paper_ids": [],
        "core_paper_ids": [],
        "applied_task_ids": [],
        "updated_at": "",
        "events": [],
    }

    def __init__(self, project_directory: Path) -> None:
        self.project_directory = Path(project_directory).resolve()
        self.manifest_path = self.project_directory / "project.json"

    def initialize(self) -> dict[str, Any]:
        """Upgrade an existing planner project and create the artifact layout."""

        self.project_directory.mkdir(parents=True, exist_ok=True)
        for relative in (
            "search",
            "fulltext/PDFs",
            "fulltext/SupportingInformation",
            "fulltext/extracted_text",
            "reading_notes",
            "report",
            "audit",
            "review",
            "runs",
            "exports",
            "cache",
            "diagnostics",
        ):
            (self.project_directory / relative).mkdir(parents=True, exist_ok=True)

        manifest = self.load_manifest()
        previous_schema = int(manifest.get("schema_version", 1) or 1)
        manifest["schema_version"] = max(
            int(manifest.get("schema_version", 1)), WORKFLOW_SCHEMA_VERSION
        )
        if previous_schema < WORKFLOW_SCHEMA_VERSION:
            migrations = manifest.setdefault("migrations", [])
            migrations.append(
                {
                    "from": previous_schema,
                    "to": WORKFLOW_SCHEMA_VERSION,
                    "at": _now_iso(),
                    "strategy": "additive",
                }
            )
        manifest.setdefault("files", {})
        workflow = manifest.get("workflow")
        original_stages = workflow.get("stages") if isinstance(workflow, dict) else None
        had_audit_stage = isinstance(original_stages, dict) and "audit" in original_stages
        if not isinstance(workflow, dict):
            workflow = deepcopy(self.DEFAULT_WORKFLOW)
        else:
            defaults = deepcopy(self.DEFAULT_WORKFLOW)
            defaults.update(workflow)
            stages = defaults["stages"]
            if not isinstance(stages, dict):
                stages = {}
            defaults["stages"] = {
                **self.DEFAULT_WORKFLOW["stages"],
                **stages,
            }
            defaults["events"] = list(defaults.get("events") or [])[-200:]
            workflow = defaults

        # Schema v3 treated report generation and verification as one stage. On
        # upgrade, preserve an explicitly stored audit state; otherwise derive
        # only what the old project can prove from its report status and legacy
        # verification artifacts. A completed report unlocks verification, but
        # must not be marked verified without an audit artifact.
        if previous_schema < 4 and not had_audit_stage:
            report_status = str(workflow["stages"].get("report") or "locked")
            if report_status in {"complete", "warning"}:
                has_legacy_audit = any(
                    self.path_for(relative_path).is_file()
                    for relative_path in LEGACY_AUDIT_PATHS
                )
                workflow["stages"]["audit"] = (
                    report_status if has_legacy_audit else "pending"
                )
                workflow["current_stage"] = "audit"
            else:
                workflow["stages"]["audit"] = "locked"
        workflow["updated_at"] = _now_iso()
        manifest["workflow"] = workflow
        self.save_manifest(manifest)
        return manifest

    @property
    def task_database_path(self) -> Path:
        return self.path_for("diagnostics/tasks.sqlite3")

    def save_run(self, run: Any) -> Path:
        """Persist an immutable run manifest and register its artifact path."""

        payload = run.to_dict() if hasattr(run, "to_dict") else dict(run)
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in run_id):
            raise ValueError("运行记录缺少安全的 run_id。")
        return self.save_json(f"run_{run_id}", f"runs/{run_id}.json", payload)

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"schema_version": WORKFLOW_SCHEMA_VERSION, "files": {}}
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            value = {}
        return value if isinstance(value, dict) else {}

    def save_manifest(self, manifest: dict[str, Any]) -> Path:
        _atomic_write_text(
            self.manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        return self.manifest_path

    def _register_file(self, key: str, path: Path) -> None:
        manifest = self.load_manifest()
        manifest.setdefault("schema_version", WORKFLOW_SCHEMA_VERSION)
        files = manifest.setdefault("files", {})
        if not isinstance(files, dict):
            files = {}
            manifest["files"] = files
        files[key] = path.relative_to(self.project_directory).as_posix()
        workflow = manifest.setdefault("workflow", deepcopy(self.DEFAULT_WORKFLOW))
        if isinstance(workflow, dict):
            workflow["updated_at"] = _now_iso()
        self.save_manifest(manifest)

    def path_for(self, relative_path: str) -> Path:
        candidate = (self.project_directory / relative_path).resolve()
        try:
            candidate.relative_to(self.project_directory)
        except ValueError as error:
            raise ValueError("工作流文件必须位于当前项目目录内") from error
        return candidate

    def save_json(self, key: str, relative_path: str, payload: Any) -> Path:
        path = self.path_for(relative_path)
        _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self._register_file(key, path)
        return path

    def load_json(self, relative_path: str, default: Any = None) -> Any:
        path = self.path_for(relative_path)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return default

    def save_markdown(self, key: str, relative_path: str, content: str) -> Path:
        path = self.path_for(relative_path)
        _atomic_write_text(path, content.rstrip() + "\n")
        self._register_file(key, path)
        return path

    def read_text(self, relative_path: str, default: str = "") -> str:
        path = self.path_for(relative_path)
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return default

    def set_stage(
        self,
        stage: str,
        status: str,
        *,
        next_stage: str | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        if stage not in self.DEFAULT_WORKFLOW["stages"]:
            raise ValueError(f"未知工作流阶段：{stage}")
        if status not in {"locked", "pending", "in_progress", "complete", "warning"}:
            raise ValueError(f"未知阶段状态：{status}")
        manifest = self.initialize()
        workflow = manifest["workflow"]
        workflow["stages"][stage] = status
        workflow["current_stage"] = next_stage or stage
        if next_stage:
            workflow["stages"][next_stage] = "pending"
        event = {
            "at": _now_iso(),
            "stage": stage,
            "status": status,
            "message": message,
        }
        workflow.setdefault("events", []).append(event)
        workflow["events"] = workflow["events"][-200:]
        workflow["updated_at"] = event["at"]
        self.save_manifest(manifest)
        return manifest

    def project_brief(self) -> dict[str, Any]:
        brief = self.load_manifest().get("brief")
        return brief if isinstance(brief, dict) else {}

    def set_paper_selection(
        self,
        *,
        selected_paper_ids: list[str],
        core_paper_ids: list[str],
    ) -> dict[str, Any]:
        manifest = self.initialize()
        workflow = manifest["workflow"]
        workflow["selected_paper_ids"] = list(dict.fromkeys(selected_paper_ids))
        workflow["core_paper_ids"] = list(dict.fromkeys(core_paper_ids))
        workflow["updated_at"] = _now_iso()
        self.save_manifest(manifest)
        return manifest

    def mark_task_applied(self, task_id: str) -> dict[str, Any]:
        manifest = self.initialize()
        workflow = manifest["workflow"]
        values = list(workflow.get("applied_task_ids") or [])
        if task_id not in values:
            values.append(task_id)
        workflow["applied_task_ids"] = values[-1000:]
        workflow["updated_at"] = _now_iso()
        self.save_manifest(manifest)
        return manifest
