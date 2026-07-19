"""Shared integration result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class IntegrationError(RuntimeError):
    pass


@dataclass(slots=True)
class ConnectionResult:
    ok: bool
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchPreviewItem:
    source: str
    title: str
    creators: list[str] = field(default_factory=list)
    year: str = ""
    doi: str = ""
    url: str = ""
    evidence_level: str = "元数据"
    access_status: str = ""
    snippet: str = ""
    citation_key: str = ""
    internal_id: str = ""

    def display_text(self) -> str:
        meta = " · ".join(value for value in (self.year, self.doi, self.evidence_level) if value)
        creators = ", ".join(self.creators[:3])
        lines = [self.title]
        if creators:
            lines.append(creators)
        if meta:
            lines.append(meta)
        if self.access_status:
            lines.append(self.access_status)
        if self.snippet:
            lines.append(self.snippet.replace("\n", " ")[:240])
        return "\n".join(lines)
