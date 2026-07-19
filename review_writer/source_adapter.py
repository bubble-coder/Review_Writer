"""Reusable source-adapter reliability primitives.

Existing OpenAlex/Crossref/Zotero/IMA providers can be wrapped by these
components without changing their parsing logic.  The wrapper centralizes
pagination, cache identity, rate limiting, retry classification, and health
telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    pagination: bool = False
    citation_counts: bool = False
    abstracts: bool = False
    fulltext_links: bool = False
    write_operations: bool = False
    maximum_page_size: int = 100
    requests_per_second: float = 1.0


@dataclass(slots=True)
class SourcePage(Generic[T]):
    items: list[T]
    next_cursor: str = ""
    total: int | None = None
    raw_response_hash: str = ""
    database_version: str = ""


class PagedSourceAdapter(Protocol[T]):
    name: str
    capabilities: SourceCapabilities

    def fetch_page(self, query: str, *, cursor: str = "", limit: int = 20, filters: Mapping[str, Any] | None = None) -> SourcePage[T]: ...
    def check(self) -> Any: ...


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1.0 / max(0.01, requests_per_second)
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            remaining = self.interval - (time.monotonic() - self._last)
            if remaining > 0:
                time.sleep(remaining)
            self._last = time.monotonic()


class ResponseCache:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS source_cache(
                    cache_key TEXT PRIMARY KEY, source TEXT NOT NULL,
                    stored_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )"""
            )
            db.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT payload_json FROM source_cache WHERE cache_key=? AND expires_at>=?", (key, now)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, source: str, payload: Mapping[str, Any], *, ttl_seconds: int = 86400) -> None:
        now = datetime.now().astimezone()
        expires = now + timedelta(seconds=max(1, ttl_seconds))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "INSERT OR REPLACE INTO source_cache VALUES (?, ?, ?, ?, ?)",
                (key, source, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"), json.dumps(dict(payload), ensure_ascii=False)),
            )
            db.commit()

    def purge_expired(self) -> int:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with closing(sqlite3.connect(self.path)) as db:
            cursor = db.execute("DELETE FROM source_cache WHERE expires_at < ?", (now,))
            db.commit()
            return cursor.rowcount


@dataclass(slots=True)
class FieldConflict:
    field_name: str
    chosen_value: Any
    candidates: list[dict[str, Any]] = field(default_factory=list)
    resolution: str = "source_priority"


def merge_with_field_provenance(
    records: list[tuple[str, Mapping[str, Any]]],
    *,
    source_priority: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[FieldConflict]]:
    """Merge normalized records without discarding disagreement."""

    priority = {name: index for index, name in enumerate(source_priority or [])}
    provenance: dict[str, list[dict[str, Any]]] = {}
    for source, record in records:
        for field_name, value in record.items():
            if value in (None, "", [], {}):
                continue
            provenance.setdefault(field_name, []).append({"source": source, "value": value})
    merged: dict[str, Any] = {}
    conflicts: list[FieldConflict] = []
    for field_name, candidates in provenance.items():
        ranked = sorted(candidates, key=lambda item: priority.get(item["source"], len(priority)))
        chosen = ranked[0]["value"]
        merged[field_name] = chosen
        distinct = {json.dumps(item["value"], ensure_ascii=False, sort_keys=True) for item in candidates}
        if len(distinct) > 1:
            conflicts.append(FieldConflict(field_name, chosen, candidates))
    return merged, provenance, conflicts


def cache_key(source: str, query: str, cursor: str, limit: int, filters: Mapping[str, Any] | None) -> str:
    import hashlib
    raw = json.dumps([source, query, cursor, limit, dict(filters or {})], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def collect_pages(
    adapter: PagedSourceAdapter[T],
    query: str,
    *,
    maximum: int,
    filters: Mapping[str, Any] | None = None,
    start_cursor: str = "",
    checkpoint: Callable[[str, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[T]:
    """Collect pages with cursor-loop protection, rate limiting, and checkpoints."""

    if maximum <= 0:
        return []
    limiter = RateLimiter(adapter.capabilities.requests_per_second)
    cursor = start_cursor
    seen_cursors: set[str] = set()
    items: list[T] = []
    while len(items) < maximum:
        if cancelled and cancelled():
            break
        if cursor in seen_cursors and cursor:
            raise RuntimeError(f"{adapter.name} 返回了重复分页游标，已停止以避免死循环。")
        if cursor:
            seen_cursors.add(cursor)
        limiter.wait()
        page = adapter.fetch_page(
            query,
            cursor=cursor,
            limit=min(adapter.capabilities.maximum_page_size, maximum - len(items)),
            filters=filters,
        )
        items.extend(page.items[: maximum - len(items)])
        if checkpoint:
            checkpoint(page.next_cursor, len(items))
        if not page.next_cursor or not page.items:
            break
        cursor = page.next_cursor
    return items
