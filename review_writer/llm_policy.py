"""Preflight policy for controlled external model calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import math
from typing import Any, Iterable, Mapping

from .provenance import ModelInvocationRecord, content_hash
from .settings import ModelSettings


class DataClass(IntEnum):
    PUBLIC_METADATA = 0
    ABSTRACT = 1
    OPEN_FULLTEXT = 2
    LICENSED_FULLTEXT = 3
    PRIVATE_NOTES = 4
    SENSITIVE = 5

    @property
    def label(self) -> str:
        return {
            self.PUBLIC_METADATA: "公开元数据", self.ABSTRACT: "摘要",
            self.OPEN_FULLTEXT: "开放获取全文", self.LICENSED_FULLTEXT: "授权全文",
            self.PRIVATE_NOTES: "私人笔记", self.SENSITIVE: "敏感材料",
        }[self]


@dataclass(frozen=True, slots=True)
class MaterialDescriptor:
    material_id: str
    data_class: DataClass
    characters: int
    description: str = ""


@dataclass(slots=True)
class ModelCallPolicy:
    maximum_data_class: DataClass = DataClass.ABSTRACT
    require_confirmation: bool = True
    context_window_tokens: int = 128000
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    price_tiers: list[dict[str, Any]] = field(default_factory=list)
    currency: str = "USD"
    allowed_purposes: list[str] = field(default_factory=lambda: ["planning", "search_strategy", "reading", "synthesis", "audit"])


@dataclass(frozen=True, slots=True)
class ModelCallPreflight:
    allowed: bool
    requires_confirmation: bool
    estimated_input_tokens: int
    output_token_limit: int
    estimated_cost: float | None
    currency: str
    blocked_material_ids: tuple[str, ...]
    warnings: tuple[str, ...]


def estimate_tokens(text_or_characters: str | int) -> int:
    characters = len(text_or_characters) if isinstance(text_or_characters, str) else max(0, int(text_or_characters))
    # Conservative mixed Chinese/English estimate used only for preflight.
    return math.ceil(characters / 2.5)


def preflight_model_call(
    settings: ModelSettings,
    policy: ModelCallPolicy,
    materials: Iterable[MaterialDescriptor],
    *,
    purpose: str,
    output_token_limit: int | None = None,
) -> ModelCallPreflight:
    materials = list(materials)
    blocked = tuple(item.material_id for item in materials if item.data_class > policy.maximum_data_class)
    input_tokens = sum(estimate_tokens(item.characters) for item in materials)
    output_tokens = min(output_token_limit or settings.max_output_tokens, settings.max_output_tokens)
    warnings: list[str] = []
    if input_tokens + output_tokens > policy.context_window_tokens:
        warnings.append(f"预计上下文 {input_tokens + output_tokens:,} tokens 超过配置上限 {policy.context_window_tokens:,}。")
    if purpose not in policy.allowed_purposes:
        warnings.append(f"调用目的 {purpose!r} 不在允许列表中。")
    input_price = policy.input_price_per_million
    output_price = policy.output_price_per_million
    if policy.price_tiers:
        matched_tier = next(
            (
                tier for tier in policy.price_tiers
                if input_tokens >= int(tier.get("min_input_tokens") or 0)
                and (
                    tier.get("max_input_tokens") in (None, "")
                    or input_tokens <= int(tier["max_input_tokens"])
                )
            ),
            None,
        )
        if matched_tier is None:
            input_price = output_price = None
        else:
            input_price = matched_tier.get("input_price_per_million")
            output_price = matched_tier.get("output_price_per_million")
    estimated_cost = None
    if input_price is not None and output_price is not None:
        estimated_cost = round(
            input_tokens / 1_000_000 * float(input_price)
            + output_tokens / 1_000_000 * float(output_price),
            6,
        )
    allowed = not blocked and not warnings
    return ModelCallPreflight(
        allowed, policy.require_confirmation, input_tokens, output_tokens, estimated_cost,
        policy.currency, blocked, tuple(warnings),
    )


def invocation_record(
    settings: ModelSettings,
    *,
    purpose: str,
    prompt_version: str,
    system_prompt: str,
    user_payload: str,
    materials: Iterable[MaterialDescriptor],
    preflight: ModelCallPreflight,
) -> ModelInvocationRecord:
    return ModelInvocationRecord(
        purpose=purpose, provider=settings.provider_name, model=settings.model,
        protocol=settings.protocol, prompt_version=prompt_version,
        system_prompt_hash=content_hash(system_prompt), user_payload_hash=content_hash(user_payload),
        sent_material_classes=sorted({item.data_class.label for item in materials}),
        input_tokens_estimated=preflight.estimated_input_tokens,
        output_tokens_limit=preflight.output_token_limit,
        estimated_cost=preflight.estimated_cost, currency=preflight.currency,
        pricing_source=settings.pricing_source,
        pricing_updated_at=settings.pricing_updated_at,
    )
