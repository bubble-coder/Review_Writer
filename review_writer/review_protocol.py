"""Rapid/systematic review protocols, screening ledger, and PRISMA counts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _strings(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


class ReviewMode(str, Enum):
    ORDINARY = "ordinary"
    RAPID = "rapid"
    SYSTEMATIC = "systematic"

    @property
    def label(self) -> str:
        return {
            self.ORDINARY: "普通调研",
            self.RAPID: "快速综述",
            self.SYSTEMATIC: "系统综述",
        }[self]


class ScreeningStage(str, Enum):
    TITLE_ABSTRACT = "title_abstract"
    FULL_TEXT = "full_text"


class ScreeningDecisionValue(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNCERTAIN = "uncertain"


@dataclass(slots=True)
class ReviewProtocol:
    title: str
    mode: ReviewMode = ReviewMode.ORDINARY
    research_questions: list[str] = field(default_factory=list)
    inclusion_criteria: list[str] = field(default_factory=list)
    exclusion_criteria: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    date_range: str = ""
    languages: list[str] = field(default_factory=list)
    study_designs: list[str] = field(default_factory=list)
    quality_tool: str = "custom"
    protocol_id: str = field(default_factory=lambda: f"protocol-{uuid4().hex}")
    registered_url: str = ""
    created_at: str = field(default_factory=_now_iso)
    confirmed_at: str = ""
    amendment_log: list[dict[str, str]] = field(default_factory=list)
    schema_version: int = 1

    def __post_init__(self) -> None:
        self.mode = ReviewMode(self.mode)
        self.research_questions = _strings(self.research_questions)
        self.inclusion_criteria = _strings(self.inclusion_criteria)
        self.exclusion_criteria = _strings(self.exclusion_criteria)
        self.databases = _strings(self.databases)
        self.languages = _strings(self.languages)
        self.study_designs = _strings(self.study_designs)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.title.strip():
            errors.append("综述协议标题不能为空")
        if self.mode is not ReviewMode.ORDINARY:
            if not self.inclusion_criteria:
                errors.append("快速/系统综述必须填写纳入标准")
            if not self.exclusion_criteria:
                errors.append("快速/系统综述必须填写排除标准")
            if not self.databases:
                errors.append("快速/系统综述必须记录计划检索的数据库")
        return errors

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["mode"] = self.mode.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewProtocol":
        allowed = {field.name for field in __import__("dataclasses").fields(cls)}
        return cls(**{key: value[key] for key in allowed if key in value})


@dataclass(slots=True)
class ScreeningDecision:
    paper_id: str
    stage: ScreeningStage
    decision: ScreeningDecisionValue
    reason_code: str = ""
    reason_detail: str = ""
    reviewer: str = "user"
    decided_at: str = field(default_factory=_now_iso)
    source_run_id: str = ""

    def __post_init__(self) -> None:
        self.stage = ScreeningStage(self.stage)
        self.decision = ScreeningDecisionValue(self.decision)
        if self.decision is ScreeningDecisionValue.EXCLUDE and not self.reason_code.strip():
            raise ValueError("排除文献必须记录标准化排除理由。")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["stage"] = self.stage.value
        value["decision"] = self.decision.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScreeningDecision":
        return cls(**dict(value))


@dataclass(slots=True)
class QualityAssessment:
    paper_id: str
    tool: str
    criteria: dict[str, int | None]
    notes: dict[str, str] = field(default_factory=dict)
    reviewer: str = "user"
    assessed_at: str = field(default_factory=_now_iso)

    @property
    def scored_items(self) -> int:
        return sum(value is not None for value in self.criteria.values())

    @property
    def total(self) -> int:
        return sum(int(value) for value in self.criteria.values() if value is not None)

    @property
    def maximum(self) -> int:
        return self.scored_items * 2

    @property
    def normalized_score(self) -> float | None:
        return round(self.total / self.maximum, 4) if self.maximum else None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update({"total": self.total, "maximum": self.maximum, "normalized_score": self.normalized_score})
        return value


@dataclass(frozen=True, slots=True)
class PrismaFlow:
    identified: int
    duplicates_removed: int
    screened: int
    title_abstract_excluded: int
    full_text_sought: int
    full_text_not_retrieved: int
    full_text_assessed: int
    full_text_excluded: int
    included: int
    exclusion_reasons: dict[str, int]


def calculate_prisma(
    paper_ids: Iterable[str],
    decisions: Iterable[ScreeningDecision | Mapping[str, Any]],
    *,
    duplicate_count: int = 0,
    full_text_unavailable_ids: Iterable[str] = (),
) -> PrismaFlow:
    """Calculate reproducible PRISMA counts from item-level decisions."""

    unique_ids = list(dict.fromkeys(str(value) for value in paper_ids if str(value)))
    decisions = [
        item if isinstance(item, ScreeningDecision) else ScreeningDecision.from_dict(item)
        for item in decisions
    ]
    latest: dict[tuple[str, ScreeningStage], ScreeningDecision] = {}
    for decision in decisions:
        latest[(decision.paper_id, decision.stage)] = decision
    title_rows = [item for (paper_id, stage), item in latest.items() if stage is ScreeningStage.TITLE_ABSTRACT and paper_id in unique_ids]
    title_excluded = [item for item in title_rows if item.decision is ScreeningDecisionValue.EXCLUDE]
    advanced = {
        paper_id for paper_id in unique_ids
        if (row := latest.get((paper_id, ScreeningStage.TITLE_ABSTRACT))) is not None
        and row.decision is ScreeningDecisionValue.INCLUDE
    }
    unavailable = set(full_text_unavailable_ids) & advanced
    full_rows = [
        item for (paper_id, stage), item in latest.items()
        if stage is ScreeningStage.FULL_TEXT and paper_id in advanced and paper_id not in unavailable
    ]
    full_excluded = [item for item in full_rows if item.decision is ScreeningDecisionValue.EXCLUDE]
    included = [item for item in full_rows if item.decision is ScreeningDecisionValue.INCLUDE]
    reasons: dict[str, int] = {}
    for item in full_excluded:
        reasons[item.reason_code] = reasons.get(item.reason_code, 0) + 1
    # ``paper_ids`` is the post-deduplication catalog; duplicate_count records
    # how many raw source records were collapsed into that catalog.
    screened = len(unique_ids)
    return PrismaFlow(
        identified=len(unique_ids) + max(0, duplicate_count),
        duplicates_removed=max(0, duplicate_count),
        screened=screened,
        title_abstract_excluded=len(title_excluded),
        full_text_sought=len(advanced),
        full_text_not_retrieved=len(unavailable),
        full_text_assessed=len(full_rows),
        full_text_excluded=len(full_excluded),
        included=len(included),
        exclusion_reasons=reasons,
    )


def render_prisma_markdown(flow: PrismaFlow) -> str:
    lines = [
        "# PRISMA 流程记录", "",
        "| 阶段 | 数量 |", "| --- | ---: |",
        f"| 数据库及其他来源识别 | {flow.identified} |",
        f"| 去除重复记录 | {flow.duplicates_removed} |",
        f"| 进入题名/摘要筛选 | {flow.screened} |",
        f"| 题名/摘要阶段排除 | {flow.title_abstract_excluded} |",
        f"| 尝试获取全文 | {flow.full_text_sought} |",
        f"| 未获得全文 | {flow.full_text_not_retrieved} |",
        f"| 全文评估 | {flow.full_text_assessed} |",
        f"| 全文阶段排除 | {flow.full_text_excluded} |",
        f"| 最终纳入 | {flow.included} |", "",
        "## 全文排除理由", "",
    ]
    if flow.exclusion_reasons:
        lines.extend(f"- {reason}: {count}" for reason, count in sorted(flow.exclusion_reasons.items()))
    else:
        lines.append("- 尚无全文排除记录。")
    return "\n".join(lines).rstrip() + "\n"
