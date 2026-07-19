"""Explainable multi-dimensional core-paper ranking."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
import math
from typing import Iterable, Mapping

from .workflow_models import EvidenceLevel, PaperRecord


DEFAULT_WEIGHTS = {
    "topic_match": 0.30,
    "recency": 0.10,
    "citations": 0.12,
    "source_quality": 0.10,
    "study_design": 0.16,
    "fulltext_availability": 0.10,
    "evidence_level": 0.12,
}

DESIGN_SCORES = {
    "systematic_review": 1.0, "meta_analysis": 1.0, "randomized_controlled_trial": 0.95,
    "cohort": 0.8, "case_control": 0.75, "cross_sectional": 0.6,
    "qualitative": 0.6, "case_series": 0.4, "editorial": 0.2, "unknown": 0.35,
}


@dataclass(slots=True)
class CorePaperScore:
    paper_id: str
    total: float
    dimensions: dict[str, float]
    weights: dict[str, float]
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _source_quality(paper: PaperRecord) -> float:
    explicit = paper.extra.get("source_quality_score")
    if explicit is not None:
        try:
            return max(0.0, min(1.0, float(explicit)))
        except (TypeError, ValueError):
            pass
    source = f"{paper.source} {paper.journal}".casefold()
    if any(word in source for word in ("retracted", "predatory")):
        return 0.0
    if "preprint" in source or "arxiv" in source:
        return 0.45
    if paper.journal and paper.doi:
        return 0.75
    return 0.5


def _study_design(paper: PaperRecord) -> tuple[str, float]:
    design = str(paper.extra.get("study_design") or "unknown").strip().casefold().replace(" ", "_")
    return design, DESIGN_SCORES.get(design, DESIGN_SCORES["unknown"])


def score_core_papers(
    papers: Iterable[PaperRecord],
    *,
    weights: Mapping[str, float] | None = None,
    current_year: int | None = None,
) -> list[CorePaperScore]:
    values = list(papers)
    configured = dict(DEFAULT_WEIGHTS)
    if weights:
        configured.update({key: max(0.0, float(value)) for key, value in weights.items() if key in configured})
    weight_total = sum(configured.values()) or 1.0
    configured = {key: value / weight_total for key, value in configured.items()}
    max_citations = max((paper.citation_count or 0 for paper in values), default=0)
    year = current_year or date.today().year
    results: list[CorePaperScore] = []
    for paper in values:
        topic = max(0.0, min(1.0, float(paper.extra.get("topic_match_score", paper.relevance_score) or 0.0)))
        recency = max(0.0, min(1.0, 1 - max(0, year - (paper.year or year - 20)) / 20))
        citations = math.log1p(paper.citation_count or 0) / math.log1p(max_citations) if max_citations else 0.0
        design_name, design = _study_design(paper)
        fulltext = 1.0 if paper.evidence_level is EvidenceLevel.FULL_TEXT else (0.5 if paper.extra.get("local_file") else 0.0)
        evidence = paper.evidence_level.rank / 3
        dimensions = {
            "topic_match": topic, "recency": recency, "citations": citations,
            "source_quality": _source_quality(paper), "study_design": design,
            "fulltext_availability": fulltext, "evidence_level": evidence,
        }
        total = sum(dimensions[key] * configured[key] for key in configured)
        reasons: list[str] = []
        missing: list[str] = []
        if topic >= 0.7:
            reasons.append("主题匹配度高")
        if citations >= 0.7 and paper.citation_count is not None:
            reasons.append(f"引用表现较强（{paper.citation_count} 次）")
        if design >= 0.8:
            reasons.append(f"研究设计证据等级较高（{design_name}）")
        if fulltext == 1.0:
            reasons.append("已有核验全文")
        if paper.evidence_level.rank <= EvidenceLevel.ABSTRACT_ONLY.rank:
            reasons.append(f"当前仅有{paper.evidence_level.label}，推荐需谨慎")
        if paper.citation_count is None:
            missing.append("引用量")
        if design_name == "unknown":
            missing.append("研究设计")
        if not paper.journal:
            missing.append("期刊/来源质量")
        results.append(CorePaperScore(paper.record_id, round(total, 6), dimensions, configured, reasons, missing))
    return sorted(results, key=lambda item: (item.total, item.paper_id), reverse=True)


def apply_core_scores(papers: Iterable[PaperRecord], *, weights: Mapping[str, float] | None = None) -> list[PaperRecord]:
    values = list(papers)
    by_id = {item.paper_id: item for item in score_core_papers(values, weights=weights)}
    for paper in values:
        score = by_id[paper.record_id]
        paper.extra["core_score"] = score.to_dict()
        paper.relevance_score = score.total
    return sorted(values, key=lambda paper: (paper.relevance_score, paper.year or 0), reverse=True)

