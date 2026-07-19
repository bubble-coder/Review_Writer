"""Domain models for the research-planning workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
import re
from typing import Any


_LEADING_LIST_MARKER = re.compile(r"^\s*(?:[-*•]+|\d+[.)、])\s*")


def parse_multiline_items(value: str) -> list[str]:
    """Turn one-question-per-line input into clean list items.

    Common bullets and numbered-list prefixes are removed so the generated
    Markdown can apply its own stable numbering.
    """

    items: list[str] = []
    for line in value.splitlines():
        cleaned = _LEADING_LIST_MARKER.sub("", line).strip()
        if cleaned:
            items.append(cleaned)
    return items


@dataclass(slots=True)
class ResearchBrief:
    """The user-confirmed input contract for a literature research project."""

    topic: str
    objectives: str
    core_questions: list[str] = field(default_factory=list)
    start_year: int = 2000
    end_year: int = field(default_factory=lambda: date.today().year)
    delivery_format: str = "Markdown 综合报告"
    delivery_requirements: str = ""
    scope_notes: str = ""
    generation_mode: str = "local"

    @classmethod
    def from_form(
        cls,
        *,
        topic: str,
        objectives: str,
        core_questions: str,
        start_year: str,
        end_year: str,
        delivery_format: str,
        delivery_requirements: str,
        scope_notes: str = "",
        generation_mode: str = "local",
    ) -> "ResearchBrief":
        """Build a brief from raw UI strings, raising one useful error message."""

        errors: list[str] = []
        topic = topic.strip()
        objectives = objectives.strip()
        questions = parse_multiline_items(core_questions)
        delivery_format = delivery_format.strip()
        delivery_requirements = delivery_requirements.strip()
        scope_notes = scope_notes.strip()
        generation_mode = generation_mode.strip().lower()

        if not topic:
            errors.append("请填写调研主题")
        if not objectives:
            errors.append("请填写调研目标")
        if not questions:
            errors.append("请至少填写一个核心问题（每行一个）")
        if not delivery_format:
            errors.append("请选择或填写报告交付形式")
        if not delivery_requirements:
            errors.append("请填写报告交付要求")
        if generation_mode not in {"local", "agent"}:
            errors.append("计划生成方式必须是本地规则模板或大模型 Agent")

        parsed_start = cls._parse_year(start_year, "文献起始年份", errors)
        parsed_end = cls._parse_year(end_year, "文献结束年份", errors)
        current_year = date.today().year

        if parsed_start is not None and not 1000 <= parsed_start <= current_year:
            errors.append(f"文献起始年份应在 1000—{current_year} 之间")
        if parsed_end is not None and not 1000 <= parsed_end <= current_year:
            errors.append(f"文献结束年份应在 1000—{current_year} 之间")
        if (
            parsed_start is not None
            and parsed_end is not None
            and parsed_start > parsed_end
        ):
            errors.append("文献起始年份不能晚于结束年份")

        if errors:
            raise ValueError("\n".join(f"• {error}" for error in errors))

        return cls(
            topic=topic,
            objectives=objectives,
            core_questions=questions,
            start_year=parsed_start or 2000,
            end_year=parsed_end or current_year,
            delivery_format=delivery_format,
            delivery_requirements=delivery_requirements,
            scope_notes=scope_notes,
            generation_mode=generation_mode,
        )

    @staticmethod
    def _parse_year(value: str, label: str, errors: list[str]) -> int | None:
        value = value.strip()
        if not value:
            errors.append(f"请填写{label}")
            return None
        try:
            return int(value)
        except ValueError:
            errors.append(f"{label}必须是四位数字")
            return None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> "ResearchBrief":
        """Restore a previously validated brief from a project manifest."""

        if not isinstance(raw, dict):
            raise ValueError("项目中的调研需求结构无效")
        questions = raw.get("core_questions")
        if not isinstance(questions, list):
            questions = parse_multiline_items(str(questions or ""))
        normalized_questions = [str(item).strip() for item in questions if str(item).strip()]
        if not normalized_questions:
            raise ValueError("项目中没有有效的核心问题")
        try:
            start_year = int(raw.get("start_year", 2000))
            end_year = int(raw.get("end_year", date.today().year))
        except (TypeError, ValueError) as error:
            raise ValueError("项目中的文献年份范围无效") from error
        topic = str(raw.get("topic") or "").strip()
        objectives = str(raw.get("objectives") or "").strip()
        if not topic or not objectives:
            raise ValueError("项目中的调研主题或目标为空")
        generation_mode = str(raw.get("generation_mode") or "local").strip().lower()
        if generation_mode not in {"local", "agent"}:
            generation_mode = "local"
        return cls(
            topic=topic,
            objectives=objectives,
            core_questions=normalized_questions,
            start_year=start_year,
            end_year=end_year,
            delivery_format=str(raw.get("delivery_format") or "Markdown 综合报告").strip(),
            delivery_requirements=str(raw.get("delivery_requirements") or "").strip(),
            scope_notes=str(raw.get("scope_notes") or "").strip(),
            generation_mode=generation_mode,
        )
