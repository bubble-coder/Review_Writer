"""Local project persistence."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Literal

from .app_paths import projects_root
from .models import ResearchBrief


ProjectStatus = Literal["draft", "confirmed"]


def default_output_root() -> Path:
    """Return a stable, user-writable output folder."""

    return projects_root()


def safe_folder_name(topic: str, max_length: int = 40) -> str:
    """Make a readable, Windows-safe folder fragment while preserving CJK."""

    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", topic.strip())
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip(" ._")
    return (value[:max_length].rstrip(" ._") or "research_project")


def _new_project_directory(output_root: Path, topic: str, now: datetime) -> Path:
    base_name = f"{now:%Y%m%d-%H%M%S}_{safe_folder_name(topic)}"
    candidate = output_root / base_name
    suffix = 2
    while candidate.exists():
        candidate = output_root / f"{base_name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def save_project(
    *,
    brief: ResearchBrief,
    plan_text: str,
    status: ProjectStatus,
    output_root: Path | None = None,
    project_directory: Path | None = None,
    saved_at: datetime | None = None,
    generation_metadata: dict[str, Any] | None = None,
) -> Path:
    """Save or update an app-owned project directory and return its path."""

    if not plan_text.strip():
        raise ValueError("调研计划不能为空")
    if status not in {"draft", "confirmed"}:
        raise ValueError(f"不支持的项目状态：{status}")

    saved_at = saved_at or datetime.now().astimezone()
    output_root = output_root or default_output_root()
    output_root.mkdir(parents=True, exist_ok=True)

    if project_directory is None:
        project_directory = _new_project_directory(output_root, brief.topic, saved_at)
    else:
        project_directory.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": 3,
        "status": status,
        "saved_at": saved_at.isoformat(timespec="seconds"),
        "brief": brief.to_dict(),
        "files": {"research_plan": "research_plan.md"},
        "plan_generation": dict(generation_metadata or {}),
    }
    _atomic_write_text(
        project_directory / "project.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write_text(
        project_directory / "research_plan.md",
        plan_text.rstrip() + "\n",
    )
    return project_directory


def load_project(project_directory: Path) -> tuple[ResearchBrief, str, ProjectStatus, dict[str, Any]]:
    """Load an application-owned project for resuming the execution workflow."""

    directory = Path(project_directory).resolve()
    manifest_path = directory / "project.json"
    plan_path = directory / "research_plan.md"
    if not manifest_path.is_file() or not plan_path.is_file():
        raise ValueError("所选文件夹不是有效的 Review Writer 项目")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan_text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取项目文件：{error}") from error
    if not isinstance(manifest, dict):
        raise ValueError("项目清单结构无效")
    brief = ResearchBrief.from_dict(manifest.get("brief"))
    raw_status = str(manifest.get("status") or "draft")
    status: ProjectStatus = "confirmed" if raw_status == "confirmed" else "draft"
    return brief, plan_text, status, manifest
