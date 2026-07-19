"""Serializable domain models for the executable research workflow.

The models in this module intentionally contain no I/O.  They form a stable
boundary between the GUI, search providers, persistence, reading, and report
generation code.  Every public model can round-trip through JSON-compatible
dictionaries without losing enum values or nested records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import re
from typing import Any, Iterable, Mapping


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_doi(value: str | None) -> str:
    """Return a canonical DOI without resolver prefixes or trailing punctuation."""

    text = str(value or "").strip()
    text = re.sub(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", "", text, flags=re.I)
    return text.rstrip(".,;:)]}").strip().casefold()


class EvidenceLevel(str, Enum):
    """The strongest evidence that was actually obtained and validated."""

    FULL_TEXT = "full_text"
    ABSTRACT_ONLY = "abstract_only"
    KNOWLEDGE_SNIPPET = "knowledge_snippet"
    METADATA_ONLY = "metadata_only"

    @property
    def label(self) -> str:
        return {
            self.FULL_TEXT: "全文证据",
            self.ABSTRACT_ONLY: "仅摘要证据",
            self.KNOWLEDGE_SNIPPET: "知识库片段证据",
            self.METADATA_ONLY: "仅元数据",
        }[self]

    @property
    def rank(self) -> int:
        return {
            self.METADATA_ONLY: 0,
            self.KNOWLEDGE_SNIPPET: 1,
            self.ABSTRACT_ONLY: 2,
            self.FULL_TEXT: 3,
        }[self]

    @classmethod
    def parse(cls, value: Any) -> "EvidenceLevel":
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().casefold()
        aliases = {
            "full_text": cls.FULL_TEXT,
            "全文证据": cls.FULL_TEXT,
            "本地全文候选": cls.FULL_TEXT,
            "ima 全文候选": cls.FULL_TEXT,
            "abstract_only": cls.ABSTRACT_ONLY,
            "仅摘要证据": cls.ABSTRACT_ONLY,
            "摘要": cls.ABSTRACT_ONLY,
            "knowledge_snippet": cls.KNOWLEDGE_SNIPPET,
            "知识库片段证据": cls.KNOWLEDGE_SNIPPET,
            "ima 知识库片段证据": cls.KNOWLEDGE_SNIPPET,
            "metadata_only": cls.METADATA_ONLY,
            "仅元数据": cls.METADATA_ONLY,
            "元数据": cls.METADATA_ONLY,
            "zotero 元数据": cls.METADATA_ONLY,
        }
        return aliases.get(text, cls.METADATA_ONLY)


@dataclass(slots=True)
class KeywordNode:
    """One node in an editable concept/synonym keyword tree."""

    term: str
    synonyms: list[str] = field(default_factory=list)
    related_terms: list[str] = field(default_factory=list)
    children: list["KeywordNode"] = field(default_factory=list)
    note: str = ""

    def __post_init__(self) -> None:
        self.term = str(self.term).strip()
        self.synonyms = _strings(self.synonyms)
        self.related_terms = _strings(self.related_terms)
        self.children = [
            child if isinstance(child, KeywordNode) else KeywordNode.from_dict(child)
            for child in self.children
            if isinstance(child, (KeywordNode, Mapping))
        ]
        self.note = str(self.note or "").strip()

    def all_terms(self) -> list[str]:
        values = [self.term, *self.synonyms, *self.related_terms]
        for child in self.children:
            values.extend(child.all_terms())
        return _strings(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "synonyms": list(self.synonyms),
            "related_terms": list(self.related_terms),
            "children": [child.to_dict() for child in self.children],
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "KeywordNode":
        return cls(
            term=str(value.get("term") or ""),
            synonyms=_strings(value.get("synonyms")),
            related_terms=_strings(value.get("related_terms")),
            children=[
                cls.from_dict(item)
                for item in value.get("children", [])
                if isinstance(item, Mapping)
            ],
            note=str(value.get("note") or ""),
        )


@dataclass(slots=True)
class SearchStrategyBundle:
    """Confirmed keyword tree and recall/precision search expressions."""

    topic: str
    core_questions: list[str]
    keyword_tree: KeywordNode
    broad_queries: list[str] = field(default_factory=list)
    precision_queries: list[str] = field(default_factory=list)
    source_queries: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    start_year: int | None = None
    end_year: int | None = None
    generation_mode: str = "local"
    notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    schema_version: int = 1

    def __post_init__(self) -> None:
        self.topic = str(self.topic).strip()
        self.core_questions = _strings(self.core_questions)
        if not isinstance(self.keyword_tree, KeywordNode):
            self.keyword_tree = KeywordNode.from_dict(self.keyword_tree)
        self.broad_queries = _strings(self.broad_queries)
        self.precision_queries = _strings(self.precision_queries)
        cleaned: dict[str, dict[str, list[str]]] = {}
        source_values = self.source_queries if isinstance(self.source_queries, Mapping) else {}
        for source, modes in source_values.items():
            if not isinstance(modes, Mapping):
                continue
            cleaned[str(source)] = {
                "broad": _strings(modes.get("broad")),
                "precision": _strings(modes.get("precision")),
            }
        self.source_queries = cleaned
        self.generation_mode = str(self.generation_mode or "local").strip().casefold()
        self.notes = _strings(self.notes)
        self.created_at = str(self.created_at or _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "topic": self.topic,
            "core_questions": list(self.core_questions),
            "keyword_tree": self.keyword_tree.to_dict(),
            "broad_queries": list(self.broad_queries),
            "precision_queries": list(self.precision_queries),
            "source_queries": {
                source: {mode: list(queries) for mode, queries in modes.items()}
                for source, modes in self.source_queries.items()
            },
            "start_year": self.start_year,
            "end_year": self.end_year,
            "generation_mode": self.generation_mode,
            "notes": list(self.notes),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SearchStrategyBundle":
        tree = value.get("keyword_tree")
        source_queries = value.get("source_queries")
        return cls(
            topic=str(value.get("topic") or ""),
            core_questions=_strings(value.get("core_questions")),
            keyword_tree=KeywordNode.from_dict(tree) if isinstance(tree, Mapping) else KeywordNode(""),
            broad_queries=_strings(value.get("broad_queries")),
            precision_queries=_strings(value.get("precision_queries")),
            source_queries=dict(source_queries) if isinstance(source_queries, Mapping) else {},
            start_year=_optional_int(value.get("start_year")),
            end_year=_optional_int(value.get("end_year")),
            generation_mode=str(value.get("generation_mode") or "local"),
            notes=_strings(value.get("notes")),
            created_at=str(value.get("created_at") or _now_iso()),
            schema_version=_optional_int(value.get("schema_version")) or 1,
        )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _record_identifier(title: str, doi: str, first_author: str, year: int | None) -> str:
    key = doi or "|".join((title.casefold().strip(), first_author.casefold().strip(), str(year or "")))
    return "paper-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


@dataclass(slots=True)
class PaperRecord:
    """Unified bibliographic record with provenance and evidence state."""

    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    url: str = ""
    source: str = ""
    abstract: str = ""
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    source_id: str = ""
    record_id: str = ""
    sources: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.METADATA_ONLY
    access_status: str = ""
    citation_count: int | None = None
    relevance_score: float = 0.0
    keywords: list[str] = field(default_factory=list)
    retrieved_at: str = field(default_factory=_now_iso)
    field_provenance: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    field_conflicts: list[dict[str, Any]] = field(default_factory=list)
    document_asset_ids: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = str(self.title or "").strip()
        self.authors = _strings(self.authors)
        self.year = _optional_int(self.year)
        self.doi = normalize_doi(self.doi)
        self.url = str(self.url or "").strip()
        self.source = str(self.source or "").strip()
        self.abstract = str(self.abstract or "").strip()
        self.journal = str(self.journal or "").strip()
        self.volume = str(self.volume or "").strip()
        self.issue = str(self.issue or "").strip()
        self.pages = str(self.pages or "").strip()
        self.source_id = str(self.source_id or "").strip()
        self.sources = _strings([*self.sources, self.source])
        self.queries = _strings(self.queries)
        self.evidence_level = EvidenceLevel.parse(self.evidence_level)
        self.access_status = str(self.access_status or "").strip()
        self.citation_count = _optional_int(self.citation_count)
        self.relevance_score = float(_optional_float(self.relevance_score) or 0.0)
        self.keywords = _strings(self.keywords)
        self.retrieved_at = str(self.retrieved_at or _now_iso())
        self.field_provenance = {
            str(name): [dict(item) for item in values if isinstance(item, Mapping)]
            for name, values in self.field_provenance.items()
            if isinstance(values, (list, tuple))
        } if isinstance(self.field_provenance, Mapping) else {}
        if not self.field_provenance and self.source:
            for name in ("title", "authors", "year", "doi", "url", "abstract", "journal", "volume", "issue", "pages", "citation_count"):
                value = getattr(self, name)
                if value not in (None, "", [], {}):
                    self.field_provenance[name] = [
                        {"source": self.source, "source_id": self.source_id, "value": value, "retrieved_at": self.retrieved_at}
                    ]
        self.field_conflicts = [dict(item) for item in self.field_conflicts if isinstance(item, Mapping)]
        self.document_asset_ids = _strings(self.document_asset_ids)
        self.extra = dict(self.extra) if isinstance(self.extra, Mapping) else {}
        if self.source_id and self.source:
            source_ids = dict(self.extra.get("source_ids") or {})
            source_ids.setdefault(self.source, self.source_id)
            self.extra["source_ids"] = source_ids
        if not self.record_id:
            self.record_id = _record_identifier(
                self.title, self.doi, self.authors[0] if self.authors else "", self.year
            )

    @property
    def first_author(self) -> str:
        return self.authors[0] if self.authors else ""

    @property
    def id(self) -> str:
        """Compatibility alias for consumers that use a generic ``id`` field."""

        return self.record_id

    @property
    def query(self) -> str:
        """The first provenance query; all queries remain available in ``queries``."""

        return self.queries[0] if self.queries else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "doi": self.doi,
            "url": self.url,
            "source": self.source,
            "sources": list(self.sources),
            "abstract": self.abstract,
            "journal": self.journal,
            "volume": self.volume,
            "issue": self.issue,
            "pages": self.pages,
            "source_id": self.source_id,
            "queries": list(self.queries),
            "evidence_level": self.evidence_level.value,
            "evidence_label": self.evidence_level.label,
            "access_status": self.access_status,
            "citation_count": self.citation_count,
            "relevance_score": self.relevance_score,
            "keywords": list(self.keywords),
            "retrieved_at": self.retrieved_at,
            "field_provenance": {
                name: [dict(item) for item in values]
                for name, values in self.field_provenance.items()
            },
            "field_conflicts": [dict(item) for item in self.field_conflicts],
            "document_asset_ids": list(self.document_asset_ids),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PaperRecord":
        return cls(
            record_id=str(value.get("record_id") or ""),
            title=str(value.get("title") or ""),
            authors=_strings(value.get("authors")),
            year=_optional_int(value.get("year")),
            doi=str(value.get("doi") or ""),
            url=str(value.get("url") or ""),
            source=str(value.get("source") or ""),
            sources=_strings(value.get("sources")),
            abstract=str(value.get("abstract") or ""),
            journal=str(value.get("journal") or ""),
            volume=str(value.get("volume") or ""),
            issue=str(value.get("issue") or ""),
            pages=str(value.get("pages") or ""),
            source_id=str(value.get("source_id") or ""),
            queries=_strings(value.get("queries") or ([value.get("query")] if value.get("query") else [])),
            evidence_level=EvidenceLevel.parse(value.get("evidence_level") or value.get("evidence_label")),
            access_status=str(value.get("access_status") or ""),
            citation_count=_optional_int(value.get("citation_count")),
            relevance_score=float(_optional_float(value.get("relevance_score")) or 0.0),
            keywords=_strings(value.get("keywords")),
            retrieved_at=str(value.get("retrieved_at") or _now_iso()),
            field_provenance=(dict(value["field_provenance"]) if isinstance(value.get("field_provenance"), Mapping) else {}),
            field_conflicts=(list(value["field_conflicts"]) if isinstance(value.get("field_conflicts"), list) else []),
            document_asset_ids=_strings(value.get("document_asset_ids")),
            extra=(dict(value["extra"]) if isinstance(value.get("extra"), Mapping) else {}),
        )


@dataclass(slots=True)
class EvidenceBlock:
    """A source-anchored evidence fragment used by reading and auditing stages."""

    paper_id: str
    text: str
    evidence_level: EvidenceLevel
    block_id: str = ""
    locator: str = ""
    asset_id: str = ""
    page_number: int | None = None
    paragraph_number: int | None = None
    section: str = ""
    figure: str = ""
    table: str = ""
    extraction_method: str = ""
    verification_status: str = "unverified"
    source_hash: str = ""
    supports: list[str] = field(default_factory=list)
    note: str = ""

    def __post_init__(self) -> None:
        self.paper_id = str(self.paper_id or "").strip()
        self.text = str(self.text or "").strip()
        self.evidence_level = EvidenceLevel.parse(self.evidence_level)
        self.locator = str(self.locator or "").strip()
        self.asset_id = str(self.asset_id or "").strip()
        self.page_number = _optional_int(self.page_number)
        self.paragraph_number = _optional_int(self.paragraph_number)
        self.section = str(self.section or "").strip()
        self.figure = str(self.figure or "").strip()
        self.table = str(self.table or "").strip()
        self.extraction_method = str(self.extraction_method or "").strip()
        self.verification_status = str(self.verification_status or "unverified").strip()
        self.source_hash = str(self.source_hash or "").strip()
        self.supports = _strings(self.supports)
        self.note = str(self.note or "").strip()
        if not self.block_id:
            key = "|".join((self.paper_id, self.locator, self.text))
            self.block_id = "evidence-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "paper_id": self.paper_id,
            "text": self.text,
            "evidence_level": EvidenceLevel.parse(self.evidence_level).value,
            "locator": self.locator,
            "asset_id": self.asset_id,
            "page_number": self.page_number,
            "paragraph_number": self.paragraph_number,
            "section": self.section,
            "figure": self.figure,
            "table": self.table,
            "extraction_method": self.extraction_method,
            "verification_status": self.verification_status,
            "source_hash": self.source_hash,
            "supports": list(self.supports),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceBlock":
        return cls(
            block_id=str(value.get("block_id") or ""),
            paper_id=str(value.get("paper_id") or ""),
            text=str(value.get("text") or ""),
            evidence_level=EvidenceLevel.parse(value.get("evidence_level")),
            locator=str(value.get("locator") or ""),
            asset_id=str(value.get("asset_id") or ""),
            page_number=_optional_int(value.get("page_number")),
            paragraph_number=_optional_int(value.get("paragraph_number")),
            section=str(value.get("section") or ""),
            figure=str(value.get("figure") or ""),
            table=str(value.get("table") or ""),
            extraction_method=str(value.get("extraction_method") or ""),
            verification_status=str(value.get("verification_status") or "unverified"),
            source_hash=str(value.get("source_hash") or ""),
            supports=_strings(value.get("supports")),
            note=str(value.get("note") or ""),
        )


@dataclass(slots=True)
class ReadingNote:
    """Structured reading card for one core paper."""

    paper_id: str
    research_question: str = ""
    study_design: str = ""
    population_or_data: str = ""
    methods: str = ""
    findings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    related_core_questions: list[str] = field(default_factory=list)
    evidence_blocks: list[EvidenceBlock] = field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.METADATA_ONLY
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.paper_id = str(self.paper_id or "").strip()
        self.research_question = str(self.research_question or "").strip()
        self.study_design = str(self.study_design or "").strip()
        self.population_or_data = str(self.population_or_data or "").strip()
        self.methods = str(self.methods or "").strip()
        self.findings = _strings(self.findings)
        self.limitations = _strings(self.limitations)
        self.related_core_questions = _strings(self.related_core_questions)
        self.evidence_blocks = [
            item if isinstance(item, EvidenceBlock) else EvidenceBlock.from_dict(item)
            for item in self.evidence_blocks
            if isinstance(item, (EvidenceBlock, Mapping))
        ]
        self.evidence_level = EvidenceLevel.parse(self.evidence_level)
        self.warnings = _strings(self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "research_question": self.research_question,
            "study_design": self.study_design,
            "population_or_data": self.population_or_data,
            "methods": self.methods,
            "findings": list(self.findings),
            "limitations": list(self.limitations),
            "related_core_questions": list(self.related_core_questions),
            "evidence_blocks": [item.to_dict() for item in self.evidence_blocks],
            "evidence_level": EvidenceLevel.parse(self.evidence_level).value,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReadingNote":
        return cls(
            paper_id=str(value.get("paper_id") or ""),
            research_question=str(value.get("research_question") or ""),
            study_design=str(value.get("study_design") or ""),
            population_or_data=str(value.get("population_or_data") or ""),
            methods=str(value.get("methods") or ""),
            findings=_strings(value.get("findings")),
            limitations=_strings(value.get("limitations")),
            related_core_questions=_strings(value.get("related_core_questions")),
            evidence_blocks=[
                EvidenceBlock.from_dict(item)
                for item in value.get("evidence_blocks", [])
                if isinstance(item, Mapping)
            ],
            evidence_level=EvidenceLevel.parse(value.get("evidence_level")),
            warnings=_strings(value.get("warnings")),
        )


@dataclass(slots=True)
class ClaimAuditItem:
    """One report claim and the result of checking its cited evidence."""

    claim_id: str
    claim: str
    citation_ids: list[str] = field(default_factory=list)
    evidence_block_ids: list[str] = field(default_factory=list)
    status: str = "needs_review"
    reason: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.METADATA_ONLY

    def __post_init__(self) -> None:
        self.claim_id = str(self.claim_id or "").strip()
        self.claim = str(self.claim or "").strip()
        self.citation_ids = _strings(self.citation_ids)
        self.evidence_block_ids = _strings(self.evidence_block_ids)
        self.status = str(self.status or "needs_review").strip()
        self.reason = str(self.reason or "").strip()
        self.evidence_level = EvidenceLevel.parse(self.evidence_level)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "citation_ids": list(self.citation_ids),
            "evidence_block_ids": list(self.evidence_block_ids),
            "status": self.status,
            "reason": self.reason,
            "evidence_level": EvidenceLevel.parse(self.evidence_level).value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ClaimAuditItem":
        return cls(
            claim_id=str(value.get("claim_id") or ""),
            claim=str(value.get("claim") or ""),
            citation_ids=_strings(value.get("citation_ids")),
            evidence_block_ids=_strings(value.get("evidence_block_ids")),
            status=str(value.get("status") or "needs_review"),
            reason=str(value.get("reason") or ""),
            evidence_level=EvidenceLevel.parse(value.get("evidence_level")),
        )


@dataclass(slots=True)
class WorkflowState:
    """Small serializable checkpoint independent of the storage implementation."""

    project_id: str
    current_stage: str = "strategy"
    stages: dict[str, str] = field(
        default_factory=lambda: {
            "strategy": "pending",
            "search": "locked",
            "reading": "locked",
            "report": "locked",
        }
    )
    selected_paper_ids: list[str] = field(default_factory=list)
    core_paper_ids: list[str] = field(default_factory=list)
    artifact_paths: dict[str, str] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now_iso)
    schema_version: int = 1

    def __post_init__(self) -> None:
        self.project_id = str(self.project_id or "").strip()
        self.current_stage = str(self.current_stage or "strategy").strip()
        self.stages = (
            {str(key): str(value) for key, value in self.stages.items()}
            if isinstance(self.stages, Mapping)
            else {}
        )
        self.selected_paper_ids = _strings(self.selected_paper_ids)
        self.core_paper_ids = _strings(self.core_paper_ids)
        self.artifact_paths = (
            {str(key): str(value) for key, value in self.artifact_paths.items()}
            if isinstance(self.artifact_paths, Mapping)
            else {}
        )
        self.updated_at = str(self.updated_at or _now_iso())
        self.schema_version = _optional_int(self.schema_version) or 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "current_stage": self.current_stage,
            "stages": dict(self.stages),
            "selected_paper_ids": list(self.selected_paper_ids),
            "core_paper_ids": list(self.core_paper_ids),
            "artifact_paths": dict(self.artifact_paths),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowState":
        stages = value.get("stages")
        artifacts = value.get("artifact_paths")
        return cls(
            project_id=str(value.get("project_id") or ""),
            current_stage=str(value.get("current_stage") or "strategy"),
            stages={
                str(key): str(item)
                for key, item in (stages.items() if isinstance(stages, Mapping) else [])
            },
            selected_paper_ids=_strings(value.get("selected_paper_ids")),
            core_paper_ids=_strings(value.get("core_paper_ids")),
            artifact_paths={
                str(key): str(item)
                for key, item in (artifacts.items() if isinstance(artifacts, Mapping) else [])
            },
            updated_at=str(value.get("updated_at") or _now_iso()),
            schema_version=_optional_int(value.get("schema_version")) or 1,
        )


def papers_to_dicts(papers: Iterable[PaperRecord]) -> list[dict[str, Any]]:
    """Convenience helper for persistence layers."""

    return [paper.to_dict() for paper in papers]
