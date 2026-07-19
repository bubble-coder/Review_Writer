"""Lifecycle helpers for independently generated summaries and audits."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Iterable, Sequence


CLAIM_LINE = re.compile(r"^\s*-\s+\*\*(?P<claim_id>C[A-Za-z0-9_-]+)\*\*\s+(?P<body>.+?)\s*$")
CITATION = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9:._-]*)\]")
TRAILING_CITATION = re.compile(r"\s+\[[A-Za-z0-9][A-Za-z0-9:._-]*\]\s*$")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text_hash(value: str) -> str:
    # WorkflowStore canonicalizes Markdown to one trailing newline.  Hash the
    # same logical text so Tk's ``end-1c`` representation does not make a valid
    # audit look stale merely because it omits that final newline.
    normalized = value.rstrip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def nonclaim_text_hash(value: str) -> str:
    """Hash report content after removing the canonical claim bullet rows."""

    remaining = "\n".join(line for line in value.splitlines() if not CLAIM_LINE.match(line))
    return text_hash(remaining)


def ledger_hash(items: Iterable[Any]) -> str:
    payload = [item.to_dict() if hasattr(item, "to_dict") else asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item) for item in items]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text_hash(encoded)


@dataclass(frozen=True, slots=True)
class ReportState:
    report_hash: str
    ledger_hash: str
    generation_mode: str
    template: str
    nonclaim_hash: str = ""
    generated_at: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.generated_at:
            object.__setattr__(self, "generated_at", _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "ReportState | None":
        if not isinstance(value, dict) or not value.get("report_hash") or not value.get("ledger_hash"):
            return None
        return cls(
            report_hash=str(value["report_hash"]), ledger_hash=str(value["ledger_hash"]),
            generation_mode=str(value.get("generation_mode") or "unknown"),
            template=str(value.get("template") or "academic_review"),
            nonclaim_hash=str(value.get("nonclaim_hash") or ""),
            generated_at=str(value.get("generated_at") or ""),
            schema_version=int(value.get("schema_version") or 1),
        )


@dataclass(frozen=True, slots=True)
class AuditState:
    report_hash: str
    ledger_hash: str
    audit_mode: str
    overall_status: str
    generated_at: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.generated_at:
            object.__setattr__(self, "generated_at", _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "AuditState | None":
        if not isinstance(value, dict) or not value.get("report_hash") or not value.get("ledger_hash"):
            return None
        return cls(
            report_hash=str(value["report_hash"]), ledger_hash=str(value["ledger_hash"]),
            audit_mode=str(value.get("audit_mode") or "local"),
            overall_status=str(value.get("overall_status") or "warning"),
            generated_at=str(value.get("generated_at") or ""),
            schema_version=int(value.get("schema_version") or 1),
        )


@dataclass(frozen=True, slots=True)
class ClaimSyncResult:
    claims: tuple[Any, ...]
    changed_claim_ids: tuple[str, ...]
    errors: tuple[str, ...]


def synchronize_claims_from_report(report_text: str, claims: Sequence[Any]) -> ClaimSyncResult:
    """Synchronize visible claim wording/citations while retaining evidence bindings.

    Only bullet lines beginning with a stable bold claim ID are considered.  The
    evidence-block binding remains sourced from the generated ledger; unknown,
    missing, or duplicate IDs are returned as blocking errors.
    """

    original = {str(item.claim_id): item for item in claims}
    visible: dict[str, tuple[str, tuple[str, ...]]] = {}
    duplicates: set[str] = set()
    for line in report_text.splitlines():
        match = CLAIM_LINE.match(line)
        if not match:
            continue
        claim_id = match.group("claim_id")
        if claim_id in visible:
            duplicates.add(claim_id)
            continue
        body = match.group("body")
        citation_ids = tuple(dict.fromkeys(CITATION.findall(body)))
        wording = body.split("〔", 1)[0].strip()
        while TRAILING_CITATION.search(wording):
            wording = TRAILING_CITATION.sub("", wording).strip()
        visible[claim_id] = (wording, citation_ids)

    errors: list[str] = []
    if duplicates:
        errors.append("报告包含重复论断 ID：" + "、".join(sorted(duplicates)))
    unknown = sorted(set(visible) - set(original))
    missing = sorted(set(original) - set(visible))
    if unknown:
        errors.append("报告包含论断台账中不存在的 ID：" + "、".join(unknown))
    if missing:
        errors.append("报告缺少论断台账中的 ID：" + "、".join(missing))

    synchronized: list[Any] = []
    changed: list[str] = []
    for item in claims:
        current = visible.get(str(item.claim_id))
        if current is None:
            synchronized.append(item)
            continue
        wording, citations = current
        if wording != item.claim_text or citations != tuple(item.citation_ids):
            changed.append(str(item.claim_id))
            synchronized.append(replace(item, claim_text=wording, citation_ids=citations, source="manual_edit"))
        else:
            synchronized.append(item)
    return ClaimSyncResult(tuple(synchronized), tuple(changed), tuple(errors))


def audit_matches_current(audit_state: AuditState | None, report_text: str, claims: Iterable[Any]) -> bool:
    return bool(
        audit_state
        and audit_state.report_hash == text_hash(report_text)
        and audit_state.ledger_hash == ledger_hash(claims)
    )
