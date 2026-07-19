"""Immutable provenance records and strict report-delivery gates.

The project keeps human-readable JSON/Markdown artifacts.  These models add a
stable contract around those artifacts so every result can be traced back to a
run, a source request, a document asset, and (for generated text) an exact
prompt/model fingerprint.  Secrets and full prompt bodies are deliberately not
stored here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping
from uuid import uuid4


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def content_hash(value: Any) -> str:
    """Return a deterministic SHA-256 for text or JSON-compatible content."""

    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EvidenceVerification(str, Enum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    MANUAL_NEEDED = "manual_needed"
    REJECTED = "rejected"


class DeliveryPolicy(str, Enum):
    STRICT = "strict"
    WARN = "warn"


@dataclass(slots=True)
class SourceRequestRecord:
    source: str
    query: str
    database_version: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    requested_at: str = field(default_factory=now_iso)
    finished_at: str = ""
    page_or_cursor: str = ""
    result_count: int = 0
    response_hash: str = ""
    status: str = "pending"
    error_code: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModelInvocationRecord:
    purpose: str
    provider: str
    model: str
    protocol: str
    prompt_version: str
    system_prompt_hash: str
    user_payload_hash: str
    sent_material_classes: list[str] = field(default_factory=list)
    invocation_id: str = field(default_factory=lambda: f"llm-{uuid4().hex}")
    started_at: str = field(default_factory=now_iso)
    finished_at: str = ""
    input_tokens_estimated: int = 0
    output_tokens_limit: int = 0
    estimated_cost: float | None = None
    currency: str = ""
    pricing_source: str = ""
    pricing_updated_at: str = ""
    status: str = "pending"
    response_hash: str = ""
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ResearchRun:
    project_id: str
    run_type: str
    review_mode: str = "ordinary"
    run_id: str = field(default_factory=lambda: f"run-{uuid4().hex}")
    app_version: str = ""
    schema_version: int = 3
    started_at: str = field(default_factory=now_iso)
    finished_at: str = ""
    status: str = "running"
    inclusion_rules: list[str] = field(default_factory=list)
    exclusion_rules: list[str] = field(default_factory=list)
    source_requests: list[SourceRequestRecord] = field(default_factory=list)
    model_invocations: list[ModelInvocationRecord] = field(default_factory=list)
    input_artifact_hashes: dict[str, str] = field(default_factory=dict)
    output_artifact_hashes: dict[str, str] = field(default_factory=dict)
    parent_run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_requests"] = [item.to_dict() for item in self.source_requests]
        result["model_invocations"] = [item.to_dict() for item in self.model_invocations]
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchRun":
        allowed = {
            "project_id", "run_type", "review_mode", "run_id", "app_version",
            "schema_version", "started_at", "finished_at", "status",
            "inclusion_rules", "exclusion_rules", "input_artifact_hashes",
            "output_artifact_hashes", "parent_run_id",
        }
        kwargs = {key: value[key] for key in allowed if key in value}
        kwargs["source_requests"] = [
            SourceRequestRecord(**item)
            for item in value.get("source_requests", [])
            if isinstance(item, Mapping)
        ]
        kwargs["model_invocations"] = [
            ModelInvocationRecord(**item)
            for item in value.get("model_invocations", [])
            if isinstance(item, Mapping)
        ]
        return cls(**kwargs)


@dataclass(frozen=True, slots=True)
class ClaimGateResult:
    allowed: bool
    policy: str
    blocking_claim_ids: tuple[str, ...]
    warning_claim_ids: tuple[str, ...]
    message: str


def evaluate_delivery_gate(
    audit_items: Iterable[Any],
    *,
    policy: DeliveryPolicy | str = DeliveryPolicy.STRICT,
) -> ClaimGateResult:
    """Decide whether an audited report may be exported as verified.

    ``fail`` always blocks strict delivery.  ``manual_needed`` blocks strict
    delivery as well because the claim has not passed verification.  Warnings
    remain deliverable but must be visibly carried into every output format.
    """

    policy = DeliveryPolicy(policy)
    failures: list[str] = []
    warnings: list[str] = []
    for item in audit_items:
        status = str(
            item.get("status", "") if isinstance(item, Mapping) else getattr(item, "status", "")
        ).casefold()
        claim_id = str(
            item.get("claim_id", "") if isinstance(item, Mapping) else getattr(item, "claim_id", "")
        ) or "unknown-claim"
        if status in {"fail", "manual_needed", "needs_review", "rejected"}:
            failures.append(claim_id)
        elif status in {"warning", "partial"}:
            warnings.append(claim_id)
    blocked = bool(failures) and policy is DeliveryPolicy.STRICT
    if blocked:
        message = f"严格交付已阻断：{len(failures)} 条论断未通过核验。"
    elif failures:
        message = f"警告交付：{len(failures)} 条论断未通过核验，必须保留醒目标记。"
    elif warnings:
        message = f"允许交付，但有 {len(warnings)} 条证据边界警告。"
    else:
        message = "全部论断通过当前本地核验门禁。"
    return ClaimGateResult(
        allowed=not blocked,
        policy=policy.value,
        blocking_claim_ids=tuple(failures),
        warning_claim_ids=tuple(warnings),
        message=message,
    )
