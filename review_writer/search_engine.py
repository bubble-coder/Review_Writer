"""Explicit, stdlib-only multi-source literature search and export.

Constructing providers is side-effect free.  Network or local connector access
occurs only when ``search``/``search_all`` is called by an explicit user action.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
import html
import json
import math
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from .workflow_models import EvidenceLevel, PaperRecord, normalize_doi
from .source_adapter import SourceCapabilities, SourcePage, collect_pages


class SearchProviderError(RuntimeError):
    """A sanitized provider-specific search failure."""


@runtime_checkable
class SearchProvider(Protocol):
    """Contract implemented by public APIs and local database adapters."""

    name: str

    def search(
        self,
        query: str,
        *,
        start_year: int | None = None,
        end_year: int | None = None,
        limit: int = 20,
    ) -> list[PaperRecord]: ...


@dataclass(slots=True)
class ProviderFailure:
    provider: str
    query: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "query": self.query, "message": self.message}


@dataclass(slots=True)
class SearchRunResult:
    papers: list[PaperRecord] = field(default_factory=list)
    failures: list[ProviderFailure] = field(default_factory=list)
    executed_queries: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    run_id: str = field(default_factory=lambda: f"search-{uuid4().hex}")
    source_requests: list[dict[str, Any]] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.papers) or not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "papers": [paper.to_dict() for paper in self.papers],
            "failures": [failure.to_dict() for failure in self.failures],
            "executed_queries": list(self.executed_queries),
            "providers": list(self.providers),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_id": self.run_id,
            "source_requests": [dict(item) for item in self.source_requests],
            "filters": dict(self.filters),
        }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_text(value: Any) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _read_json(url: str, *, headers: Mapping[str, str], timeout: int) -> dict[str, Any]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(10_000_001)
    except HTTPError as error:
        raise SearchProviderError(f"HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise SearchProviderError(f"连接失败：{str(error)[:300]}") from error
    if len(raw) > 10_000_000:
        raise SearchProviderError("响应超过 10 MB 安全限制。")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SearchProviderError("数据库没有返回有效 JSON。") from error
    if not isinstance(value, dict):
        raise SearchProviderError("数据库响应结构无效。")
    return value


def _validate_search(query: str, limit: int) -> tuple[str, int]:
    query = str(query or "").strip()
    if not query:
        raise SearchProviderError("检索式不能为空。")
    if len(query) > 4000:
        raise SearchProviderError("检索式超过 4000 字符，请拆分后重试。")
    return query, max(1, min(int(limit), 200))


def _year(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 1000 <= parsed <= date.today().year + 2 else None


def _reconstruct_openalex_abstract(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    positions: list[tuple[int, str]] = []
    for word, indices in value.items():
        if not isinstance(indices, list):
            continue
        for index in indices:
            if isinstance(index, int):
                positions.append((index, str(word)))
    return " ".join(word for _, word in sorted(positions))


class OpenAlexProvider:
    """OpenAlex discovery provider (free public metadata API)."""

    name = "OpenAlex"
    requests_per_second = 10.0
    api_version = "works-api/current"
    capabilities = SourceCapabilities(pagination=True, citation_counts=True, abstracts=True, fulltext_links=True, maximum_page_size=100, requests_per_second=10.0)

    def __init__(
        self,
        *,
        api_base: str = "https://api.openalex.org",
        mailto: str = "",
        timeout: int = 20,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.mailto = mailto.strip()
        self.timeout = max(1, int(timeout))

    def fetch_page(
        self,
        query: str,
        *,
        cursor: str = "",
        limit: int = 20,
        filters: Mapping[str, Any] | None = None,
    ) -> SourcePage[PaperRecord]:
        query, limit = _validate_search(query, limit)
        filters = dict(filters or {})
        start_year = filters.get("start_year")
        end_year = filters.get("end_year")
        filter_values: list[str] = []
        if start_year:
            filter_values.append(f"from_publication_date:{int(start_year)}-01-01")
        if end_year:
            filter_values.append(f"to_publication_date:{int(end_year)}-12-31")
        params: dict[str, Any] = {"search": query, "per-page": min(limit, 100), "cursor": cursor or "*"}
        if filter_values:
            params["filter"] = ",".join(filter_values)
        if self.mailto:
            params["mailto"] = self.mailto
        payload = _read_json(
            f"{self.api_base}/works?{urlencode(params)}",
            headers={"User-Agent": self._user_agent()},
            timeout=self.timeout,
        )
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise SearchProviderError("OpenAlex 响应缺少 results 列表。")
        papers: list[PaperRecord] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            title = _clean_text(row.get("display_name") or row.get("title"))
            if not title:
                continue
            authors: list[str] = []
            for authorship in row.get("authorships", []):
                if not isinstance(authorship, Mapping):
                    continue
                author = authorship.get("author")
                if isinstance(author, Mapping) and author.get("display_name"):
                    authors.append(_clean_text(author["display_name"]))
            primary = row.get("primary_location")
            primary = primary if isinstance(primary, Mapping) else {}
            source = primary.get("source")
            source = source if isinstance(source, Mapping) else {}
            abstract = _reconstruct_openalex_abstract(row.get("abstract_inverted_index"))
            landing_url = str(primary.get("landing_page_url") or "")
            doi = normalize_doi(str(row.get("doi") or ""))
            url = landing_url or (f"https://doi.org/{doi}" if doi else str(row.get("id") or ""))
            open_access = row.get("open_access")
            open_access = open_access if isinstance(open_access, Mapping) else {}
            papers.append(
                PaperRecord(
                    title=title,
                    authors=authors,
                    year=_year(row.get("publication_year")),
                    doi=doi,
                    url=url,
                    source=self.name,
                    abstract=abstract,
                    journal=_clean_text(source.get("display_name")),
                    source_id=str(row.get("id") or "").rsplit("/", 1)[-1],
                    queries=[query],
                    evidence_level=(
                        EvidenceLevel.ABSTRACT_ONLY if abstract else EvidenceLevel.METADATA_ONLY
                    ),
                    access_status=(
                        "开放获取全文候选（尚未下载校验）"
                        if open_access.get("is_oa")
                        else "仅发现元数据/摘要"
                    ),
                    citation_count=_yearless_int(row.get("cited_by_count")),
                    relevance_score=float(row.get("relevance_score") or 0.0),
                    extra={
                        "openalex_id": str(row.get("id") or ""),
                        "is_open_access": bool(open_access.get("is_oa")),
                        "oa_status": str(open_access.get("oa_status") or ""),
                    },
                )
            )
        meta = payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {}
        return SourcePage(papers, next_cursor=str(meta.get("next_cursor") or ""), total=_yearless_int(meta.get("count")), database_version=self.api_version)

    def search(
        self,
        query: str,
        *,
        start_year: int | None = None,
        end_year: int | None = None,
        limit: int = 20,
    ) -> list[PaperRecord]:
        query, limit = _validate_search(query, limit)
        return collect_pages(self, query, maximum=limit, filters={"start_year": start_year, "end_year": end_year})

    def _user_agent(self) -> str:
        suffix = f" mailto:{self.mailto}" if self.mailto else ""
        return f"ReviewWriter/0.7 ({suffix.strip() or 'local desktop app'})"


def _yearless_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class CrossrefProvider:
    """Crossref bibliographic metadata provider."""

    name = "Crossref"
    requests_per_second = 5.0
    api_version = "REST/current"
    capabilities = SourceCapabilities(pagination=True, citation_counts=True, abstracts=True, maximum_page_size=100, requests_per_second=5.0)

    def __init__(
        self,
        *,
        api_base: str = "https://api.crossref.org",
        mailto: str = "",
        timeout: int = 20,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.mailto = mailto.strip()
        self.timeout = max(1, int(timeout))

    def fetch_page(
        self,
        query: str,
        *,
        cursor: str = "",
        limit: int = 20,
        filters: Mapping[str, Any] | None = None,
    ) -> SourcePage[PaperRecord]:
        query, limit = _validate_search(query, limit)
        filters_map = dict(filters or {})
        start_year = filters_map.get("start_year")
        end_year = filters_map.get("end_year")
        params: dict[str, Any] = {"query.bibliographic": query, "rows": min(limit, 100), "cursor": cursor or "*"}
        filter_values: list[str] = []
        if start_year:
            filter_values.append(f"from-pub-date:{int(start_year)}-01-01")
        if end_year:
            filter_values.append(f"until-pub-date:{int(end_year)}-12-31")
        if filter_values:
            params["filter"] = ",".join(filter_values)
        if self.mailto:
            params["mailto"] = self.mailto
        payload = _read_json(
            f"{self.api_base}/works?{urlencode(params)}",
            headers={"User-Agent": self._user_agent()},
            timeout=self.timeout,
        )
        message = payload.get("message")
        rows = message.get("items") if isinstance(message, Mapping) else None
        if not isinstance(rows, list):
            raise SearchProviderError("Crossref 响应缺少 message.items 列表。")
        papers: list[PaperRecord] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            title = _first_text(row.get("title"))
            if not title:
                continue
            authors: list[str] = []
            for author in row.get("author", []):
                if not isinstance(author, Mapping):
                    continue
                name = " ".join(
                    part for part in (_clean_text(author.get("given")), _clean_text(author.get("family"))) if part
                )
                if name:
                    authors.append(name)
            abstract = _clean_text(row.get("abstract"))
            doi = normalize_doi(str(row.get("DOI") or ""))
            year = _crossref_year(row)
            papers.append(
                PaperRecord(
                    title=title,
                    authors=authors,
                    year=year,
                    doi=doi,
                    url=str(row.get("URL") or (f"https://doi.org/{doi}" if doi else "")),
                    source=self.name,
                    abstract=abstract,
                    journal=_first_text(row.get("container-title")),
                    volume=str(row.get("volume") or ""),
                    issue=str(row.get("issue") or ""),
                    pages=str(row.get("page") or ""),
                    source_id=doi,
                    queries=[query],
                    evidence_level=(
                        EvidenceLevel.ABSTRACT_ONLY if abstract else EvidenceLevel.METADATA_ONLY
                    ),
                    access_status="仅发现元数据/摘要",
                    citation_count=_yearless_int(row.get("is-referenced-by-count")),
                    relevance_score=float(row.get("score") or 0.0),
                    extra={"type": str(row.get("type") or "")},
                )
            )
        return SourcePage(papers, next_cursor=str(message.get("next-cursor") or "") if isinstance(message, Mapping) else "", total=_yearless_int(message.get("total-results")) if isinstance(message, Mapping) else None, database_version=self.api_version)

    def search(
        self,
        query: str,
        *,
        start_year: int | None = None,
        end_year: int | None = None,
        limit: int = 20,
    ) -> list[PaperRecord]:
        query, limit = _validate_search(query, limit)
        return collect_pages(self, query, maximum=limit, filters={"start_year": start_year, "end_year": end_year})

    def _user_agent(self) -> str:
        contact = f"; mailto:{self.mailto}" if self.mailto else ""
        return f"ReviewWriter/0.7 (local desktop app{contact})"


def _first_text(value: Any) -> str:
    if isinstance(value, list) and value:
        return _clean_text(value[0])
    return _clean_text(value) if isinstance(value, str) else ""


def _crossref_year(row: Mapping[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        value = row.get(key)
        if not isinstance(value, Mapping):
            continue
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            parsed = _year(parts[0][0])
            if parsed:
                return parsed
        parsed = _year(value.get("date-time", "")[:4] if isinstance(value.get("date-time"), str) else None)
        if parsed:
            return parsed
    return None


class _PreviewConnector(Protocol):
    def search_preview(self, query: str, *, limit: int = 5) -> list[Any]: ...


class ZoteroSearchProvider:
    """Read-only adapter for the existing ``ZoteroConnector``."""

    name = "Zotero"
    api_version = "local-api-v3"

    def __init__(self, connector: _PreviewConnector) -> None:
        self.connector = connector

    def search(
        self,
        query: str,
        *,
        start_year: int | None = None,
        end_year: int | None = None,
        limit: int = 20,
    ) -> list[PaperRecord]:
        query, limit = _validate_search(query, limit)
        try:
            rows = self.connector.search_preview(query, limit=limit)
        except Exception as error:
            raise SearchProviderError(f"Zotero 检索失败：{str(error)[:300]}") from error
        papers: list[PaperRecord] = []
        for row in rows:
            year = _year(getattr(row, "year", None))
            if start_year and year and year < start_year:
                continue
            if end_year and year and year > end_year:
                continue
            evidence = EvidenceLevel.parse(getattr(row, "evidence_level", ""))
            # An attachment count is only a full-text candidate.  It is not
            # FULL_TEXT until the later acquisition stage validates the file.
            if evidence is EvidenceLevel.FULL_TEXT:
                evidence = EvidenceLevel.METADATA_ONLY
            papers.append(
                PaperRecord(
                    title=str(getattr(row, "title", "") or ""),
                    authors=list(getattr(row, "creators", []) or []),
                    year=year,
                    doi=str(getattr(row, "doi", "") or ""),
                    url=str(getattr(row, "url", "") or ""),
                    source=self.name,
                    source_id=str(getattr(row, "internal_id", "") or ""),
                    queries=[query],
                    evidence_level=evidence,
                    access_status=str(getattr(row, "access_status", "") or ""),
                    extra={"citation_key": str(getattr(row, "citation_key", "") or "")},
                )
            )
        return papers


class ImaSearchProvider:
    """Read-only adapter for IMA official OpenAPI search previews."""

    name = "IMA"
    api_version = "official-openapi-v1"

    def __init__(self, connector: _PreviewConnector) -> None:
        self.connector = connector

    def search(
        self,
        query: str,
        *,
        start_year: int | None = None,
        end_year: int | None = None,
        limit: int = 20,
    ) -> list[PaperRecord]:
        query, limit = _validate_search(query, limit)
        try:
            rows = self.connector.search_preview(query, limit=limit)
        except Exception as error:
            raise SearchProviderError(f"IMA 检索失败：{str(error)[:300]}") from error
        papers: list[PaperRecord] = []
        for row in rows:
            snippet = str(getattr(row, "snippet", "") or "").strip()
            papers.append(
                PaperRecord(
                    title=str(getattr(row, "title", "") or ""),
                    url=str(getattr(row, "url", "") or ""),
                    source=self.name,
                    abstract=snippet,
                    source_id=str(getattr(row, "internal_id", "") or ""),
                    queries=[query],
                    evidence_level=(
                        EvidenceLevel.KNOWLEDGE_SNIPPET
                        if snippet
                        else EvidenceLevel.METADATA_ONLY
                    ),
                    access_status=str(getattr(row, "access_status", "") or ""),
                )
            )
        return papers


# Short aliases are convenient for dependency-injection code.
ZoteroProvider = ZoteroSearchProvider
ImaProvider = ImaSearchProvider


_TITLE_STOPWORDS = {
    "a", "an", "the", "in", "of", "for", "on", "to", "and", "with", "by", "et", "al",
}


def normalize_title_tokens(title: str) -> set[str]:
    """Tokenize a title for the documented title+author dedup fallback."""

    text = str(title or "").casefold()
    latin = [
        token for token in re.findall(r"[a-z0-9]+", text) if token not in _TITLE_STOPWORDS
    ]
    cjk_runs = re.findall(r"[\u3400-\u9fff]+", text)
    cjk_tokens: list[str] = []
    for run in cjk_runs:
        # Character bigrams make the same >=0.90 threshold useful for Chinese
        # titles while remaining deterministic.
        cjk_tokens.extend(run[index : index + 2] for index in range(max(1, len(run) - 1)))
    return set([*latin, *cjk_tokens])


def title_jaccard(left: str, right: str) -> float:
    first = normalize_title_tokens(left)
    second = normalize_title_tokens(right)
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def first_author_surname(author: str) -> str:
    text = re.sub(r"[^\w\u3400-\u9fff, -]", "", str(author or "")).strip().casefold()
    if not text:
        return ""
    if "," in text:
        return text.split(",", 1)[0].strip()
    parts = text.split()
    if len(parts) == 1:
        return parts[0]
    if len(parts[-1]) <= 2 and len(parts[0]) > 2:
        return parts[0]
    return parts[-1]


def papers_match(left: PaperRecord, right: PaperRecord) -> bool:
    """DOI-first match, falling back to title+first author when DOI is absent."""

    left_doi, right_doi = normalize_doi(left.doi), normalize_doi(right.doi)
    if left_doi and right_doi:
        return left_doi == right_doi
    left_author = first_author_surname(left.first_author)
    right_author = first_author_surname(right.first_author)
    return bool(
        left_author
        and left_author == right_author
        and title_jaccard(left.title, right.title) >= 0.90
    )


def _metadata_score(paper: PaperRecord) -> tuple[int, int, int]:
    complete = sum(bool(value) for value in (paper.doi, paper.volume, paper.pages))
    source = paper.source.casefold()
    publisher_preference = 0 if "preprint" in source or "arxiv" in source else 1
    return complete, publisher_preference, paper.citation_count or 0


def _merge_records(left: PaperRecord, right: PaperRecord) -> PaperRecord:
    preferred, secondary = (
        (left, right) if _metadata_score(left) >= _metadata_score(right) else (right, left)
    )
    values: dict[str, Any] = {}
    for name in (
        "title", "year", "doi", "url", "source", "journal", "volume", "issue", "pages", "source_id",
    ):
        values[name] = getattr(preferred, name) or getattr(secondary, name)
    values["abstract"] = max((left.abstract, right.abstract), key=len)
    values["authors"] = preferred.authors or secondary.authors
    evidence = max((left.evidence_level, right.evidence_level), key=lambda item: item.rank)
    access = "；".join(dict.fromkeys(item for item in (left.access_status, right.access_status) if item))
    field_provenance: dict[str, list[dict[str, Any]]] = {}
    for paper in (left, right):
        for name, candidates in paper.field_provenance.items():
            bucket = field_provenance.setdefault(name, [])
            for candidate in candidates:
                if candidate not in bucket:
                    bucket.append(dict(candidate))
    field_conflicts: list[dict[str, Any]] = []
    for name, candidates in field_provenance.items():
        distinct = {json.dumps(item.get("value"), ensure_ascii=False, sort_keys=True) for item in candidates}
        if len(distinct) > 1:
            field_conflicts.append(
                {"field_name": name, "chosen_value": values.get(name, getattr(preferred, name, None)), "candidates": candidates, "resolution": "metadata_completeness_then_source"}
            )
    if field_conflicts:
        access = (access + "；" if access else "") + f"{len(field_conflicts)} 个元数据字段存在来源冲突"
    return PaperRecord(
        **values,
        record_id=left.record_id,
        sources=list(dict.fromkeys([*left.sources, *right.sources])),
        queries=list(dict.fromkeys([*left.queries, *right.queries])),
        evidence_level=evidence,
        access_status=access,
        citation_count=max(left.citation_count or 0, right.citation_count or 0) or None,
        relevance_score=max(left.relevance_score, right.relevance_score),
        keywords=list(dict.fromkeys([*left.keywords, *right.keywords])),
        retrieved_at=min(left.retrieved_at, right.retrieved_at),
        field_provenance=field_provenance,
        field_conflicts=field_conflicts,
        document_asset_ids=list(dict.fromkeys([*left.document_asset_ids, *right.document_asset_ids])),
        extra={
            **secondary.extra,
            **preferred.extra,
            "source_ids": {
                **dict(secondary.extra.get("source_ids") or {}),
                **dict(preferred.extra.get("source_ids") or {}),
            },
        },
    )


def deduplicate_papers(papers: Iterable[PaperRecord]) -> list[PaperRecord]:
    """Collapse duplicates and merge their metadata/provenance."""

    unique: list[PaperRecord] = []
    for paper in papers:
        candidate = paper if isinstance(paper, PaperRecord) else PaperRecord.from_dict(paper)
        for index, existing in enumerate(unique):
            if papers_match(existing, candidate):
                unique[index] = _merge_records(existing, candidate)
                break
        else:
            unique.append(candidate)
    return unique


def _lexical_relevance(paper: PaperRecord, query: str) -> float:
    query_tokens = normalize_title_tokens(re.sub(r"\b(?:AND|OR|NOT)\b", " ", query, flags=re.I))
    if not query_tokens:
        return 0.0
    paper_tokens = normalize_title_tokens(f"{paper.title} {paper.abstract}")
    return len(query_tokens & paper_tokens) / len(query_tokens)


def rank_papers(
    papers: Iterable[PaperRecord],
    *,
    query: str = "",
    mode: str = "combined",
    current_year: int | None = None,
) -> list[PaperRecord]:
    """Rank by relevance, date, citations, or the documented combined score."""

    values = list(papers)
    if not values:
        return []
    mode = mode.casefold()
    if mode not in {"combined", "relevance", "date", "citations"}:
        raise ValueError(f"未知排序模式：{mode}")
    today = current_year or date.today().year
    maximum_citations = max((paper.citation_count or 0 for paper in values), default=0)
    provider_scores = [
        max(0.0, float(paper.extra.get("provider_relevance_score", paper.relevance_score) or 0.0))
        for paper in values
    ]
    maximum_provider = max(provider_scores, default=0.0)
    scored: list[tuple[float, PaperRecord]] = []
    for paper in values:
        lexical = _lexical_relevance(paper, query)
        raw_provider = max(
            0.0,
            float(paper.extra.get("provider_relevance_score", paper.relevance_score) or 0.0),
        )
        paper.extra.setdefault("provider_relevance_score", raw_provider)
        provider = raw_provider / maximum_provider if maximum_provider else 0.0
        relevance = max(lexical, provider)
        recency = max(0.0, min(1.0, 1.0 - (today - (paper.year or today - 20)) / 20.0))
        citations = (
            math.log1p(paper.citation_count or 0) / math.log1p(maximum_citations)
            if maximum_citations
            else 0.0
        )
        paper.extra["topic_match_score"] = round(relevance, 6)
        score = {
            "combined": relevance * 0.5 + recency * 0.3 + citations * 0.2,
            "relevance": relevance,
            "date": float(paper.year or 0),
            "citations": float(paper.citation_count or 0),
        }[mode]
        paper.relevance_score = round(score, 6)
        scored.append((score, paper))
    if mode == "combined":
        from .core_ranking import apply_core_scores

        return apply_core_scores([paper for _, paper in scored])
    return [
        paper
        for _, paper in sorted(
            scored,
            key=lambda item: (item[0], item[1].year or 0, item[1].citation_count or 0, item[1].title),
            reverse=True,
        )
    ]


def search_all(
    providers: Sequence[SearchProvider],
    queries: Sequence[str],
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    limit_per_query: int = 20,
    ranking: str = "combined",
    max_workers: int = 4,
    response_cache: Any | None = None,
    cache_ttl_seconds: int = 86400,
) -> SearchRunResult:
    """Explicitly run provider/query pairs, continuing after per-source errors."""

    clean_queries = list(dict.fromkeys(str(query).strip() for query in queries if str(query).strip()))
    if not clean_queries:
        raise ValueError("至少需要一条非空检索式。")
    if not providers:
        raise ValueError("至少需要一个已配置的数据源。")
    started = _now_iso()
    papers: list[PaperRecord] = []
    failures: list[ProviderFailure] = []
    source_requests: list[dict[str, Any]] = []
    jobs = [(provider, query) for provider in providers for query in clean_queries]
    from .source_adapter import RateLimiter, cache_key as make_cache_key

    limiters = {
        id(provider): RateLimiter(float(getattr(provider, "requests_per_second", 2.0)))
        for provider in providers
    }

    def run(provider: SearchProvider, query: str) -> list[PaperRecord]:
        request = {
            "source": str(getattr(provider, "name", provider.__class__.__name__)),
            "database_version": str(getattr(provider, "api_version", "unknown")),
            "query": query,
            "requested_at": _now_iso(),
            "filters": {"start_year": start_year, "end_year": end_year, "limit": limit_per_query},
            "status": "running",
        }
        key = make_cache_key(request["source"], query, "", limit_per_query, request["filters"])
        if response_cache is not None:
            cached = response_cache.get(key)
            if isinstance(cached, dict) and isinstance(cached.get("papers"), list):
                rows = [PaperRecord.from_dict(item) for item in cached["papers"] if isinstance(item, Mapping)]
                import hashlib

                response_hash = hashlib.sha256(json.dumps(cached["papers"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                request.update({"finished_at": _now_iso(), "status": "cached", "result_count": len(rows), "cache_hit": True, "response_hash": response_hash})
                source_requests.append(request)
                return rows
        try:
            limiters[id(provider)].wait()
            rows = provider.search(
                query,
                start_year=start_year,
                end_year=end_year,
                limit=limit_per_query,
            )
        except Exception as error:
            request.update({"finished_at": _now_iso(), "status": "failed", "error_code": error.__class__.__name__, "error_message": str(error)[:500]})
            source_requests.append(request)
            raise
        import hashlib

        normalized_payload = [paper.to_dict() for paper in rows]
        request.update({"finished_at": _now_iso(), "status": "succeeded", "result_count": len(rows), "response_hash": hashlib.sha256(json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()})
        if response_cache is not None:
            response_cache.put(key, request["source"], {"papers": normalized_payload}, ttl_seconds=cache_ttl_seconds)
        source_requests.append(request)
        return rows

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(jobs)))) as executor:
        future_jobs = {executor.submit(run, provider, query): (provider, query) for provider, query in jobs}
        for future in as_completed(future_jobs):
            provider, query = future_jobs[future]
            try:
                rows = future.result()
            except Exception as error:
                failures.append(
                    ProviderFailure(
                        provider=str(getattr(provider, "name", provider.__class__.__name__)),
                        query=query,
                        message=str(error)[:500] or error.__class__.__name__,
                    )
                )
                continue
            for paper in rows:
                if query not in paper.queries:
                    paper.queries.append(query)
                papers.append(paper)
    unique = deduplicate_papers(papers)
    ranked = rank_papers(unique, query=" ".join(clean_queries), mode=ranking)
    failures.sort(key=lambda item: (item.provider.casefold(), item.query.casefold()))
    return SearchRunResult(
        papers=ranked,
        failures=failures,
        executed_queries=clean_queries,
        providers=list(dict.fromkeys(str(getattr(provider, "name", provider.__class__.__name__)) for provider in providers)),
        started_at=started,
        finished_at=_now_iso(),
        source_requests=sorted(source_requests, key=lambda item: (item["source"], item["query"])),
        filters={"start_year": start_year, "end_year": end_year, "limit_per_query": limit_per_query, "ranking": ranking},
    )


def _md(value: Any) -> str:
    return _clean_text(value).replace("|", "\\|").replace("\n", " ")


def render_literature_catalog(
    papers: Iterable[PaperRecord],
    *,
    topic: str = "",
    failures: Iterable[ProviderFailure] = (),
) -> str:
    """Render the required DOI/title/original-link local Markdown catalog."""

    values = list(papers)
    title = f"{topic}：文献目录" if topic else "文献目录"
    lines = [
        f"# {title}",
        "",
        f"> 共 {len(values)} 条去重记录。证据等级表示应用实际获得的内容，不代表论文质量。",
        "",
        "## 证据等级",
        "",
        "- **全文证据**：已在后续获取阶段下载并校验全文。",
        "- **仅摘要证据**：当前只有摘要，不得据此推断摘要未呈现的细节。",
        "- **知识库片段证据**：仅有 IMA 等知识库返回的片段。",
        "- **仅元数据**：只有题录信息。",
        "",
        "## 文献记录",
        "",
        "| ID | 标题 | 作者 | 年份 | DOI | 来源 | 原文链接 | 证据等级 | 获取状态 |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for paper in values:
        doi = f"[{_md(paper.doi)}](https://doi.org/{paper.doi})" if paper.doi else "—"
        url = f"[打开]({_md(paper.url)})" if paper.url else "—"
        authors = ", ".join(paper.authors[:3]) + (" 等" if len(paper.authors) > 3 else "")
        lines.append(
            "| " + " | ".join(
                (
                    _md(paper.record_id),
                    _md(paper.title),
                    _md(authors) or "—",
                    str(paper.year or "—"),
                    doi,
                    _md("; ".join(paper.sources) or paper.source) or "—",
                    url,
                    paper.evidence_level.label,
                    _md(paper.access_status) or "—",
                )
            ) + " |"
        )
    failure_values = list(failures)
    if failure_values:
        lines.extend(["", "## 数据源失败记录", ""])
        for failure in failure_values:
            lines.append(f"- **{_md(failure.provider)}** / `{_md(failure.query)}`：{_md(failure.message)}")
    return "\n".join(lines).rstrip() + "\n"


def _bib_escape(value: str) -> str:
    text = _clean_text(value)
    for old, new in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("#", r"\#")):
        text = text.replace(old, new)
    return text


def _citation_base(paper: PaperRecord, index: int) -> str:
    surname = first_author_surname(paper.first_author)
    surname = "".join(character for character in surname if character.isalnum())
    if surname and paper.year:
        return f"{surname.casefold()}{paper.year}"
    if paper.doi:
        return "doi" + re.sub(r"[^a-z0-9]", "", paper.doi.casefold())[-12:]
    return f"paper{index}"


def _alphabetic_suffix(index: int) -> str:
    """Return a, b, ... z, aa, ab for duplicate citation keys."""

    result = ""
    value = index
    while value >= 0:
        result = chr(ord("a") + value % 26) + result
        value = value // 26 - 1
    return result


def render_bibtex(papers: Iterable[PaperRecord]) -> str:
    """Render validated, deterministic BibTeX entries for available metadata."""

    entries: list[tuple[str, str]] = []
    used: dict[str, int] = {}
    for index, paper in enumerate(papers, 1):
        base = _citation_base(paper, index)
        count = used.get(base, 0)
        used[base] = count + 1
        key = base if count == 0 else f"{base}{_alphabetic_suffix(count - 1)}"
        entry_type = "article" if paper.journal else "misc"
        fields: list[tuple[str, str]] = []
        if paper.authors:
            fields.append(("author", " and ".join(_bib_escape(author) for author in paper.authors)))
        fields.append(("title", "{" + _bib_escape(paper.title) + "}"))
        if paper.journal:
            fields.append(("journal", _bib_escape(paper.journal)))
        if paper.year:
            fields.append(("year", str(paper.year)))
        for name, value in (
            ("volume", paper.volume),
            ("number", paper.issue),
            ("pages", re.sub(r"-+", "--", paper.pages)),
            ("doi", paper.doi),
            ("url", paper.url),
        ):
            if value:
                fields.append((name, _bib_escape(value)))
        lines = [f"@{entry_type}{{{key},"]
        for field_index, (name, value) in enumerate(fields):
            comma = "," if field_index < len(fields) - 1 else ""
            lines.append(f"  {name:<7} = {{{value}}}{comma}")
        lines.append("}")
        entries.append((key, "\n".join(lines)))
    return "\n\n".join(value for _, value in sorted(entries, key=lambda item: item[0])) + ("\n" if entries else "")
