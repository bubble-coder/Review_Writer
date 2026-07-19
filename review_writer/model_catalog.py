"""Versioned, updateable model recommendation catalog.

The packaged JSON is always available as an offline fallback.  A validated
last-known-good document may be imported from disk or downloaded from a
user-configured HTTPS URL without changing application code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CATALOG_SCHEMA_VERSION = 2
MAX_CATALOG_BYTES = 2 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    number = float(value)
    if number < 0:
        raise ValueError("模型价格不能为负数。")
    return number


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    number = int(value)
    if number <= 0:
        raise ValueError("Token 限额必须为正整数。")
    return number


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    provider_id: str
    provider_name: str
    model: str
    protocol: str
    api_base: str
    pricing_summary: str
    recommendation: str
    model_url: str
    pricing_url: str
    last_verified: str
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    price_currency: str = "USD"
    price_effective_at: str = ""
    pricing_notes: str = ""
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    capability_tags: tuple[str, ...] = ()
    price_tiers: tuple[dict[str, Any], ...] = ()

    @property
    def catalog_key(self) -> str:
        return f"{self.provider_id}:{self.model}"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModelCatalogEntry":
        required = (
            "provider_id", "provider_name", "model", "protocol", "api_base",
            "pricing_summary", "recommendation", "model_url", "pricing_url", "last_verified",
        )
        missing = [name for name in required if not str(raw.get(name, "")).strip()]
        if missing:
            raise ValueError(f"模型目录条目缺少字段：{', '.join(missing)}")
        api_base = str(raw["api_base"]).strip()
        if urlparse(api_base).scheme not in {"http", "https"}:
            raise ValueError(f"API Base URL 无效：{api_base}")
        for key in ("model_url", "pricing_url"):
            if urlparse(str(raw[key])).scheme != "https":
                raise ValueError(f"{key} 必须使用 HTTPS。")
        tiers = raw.get("price_tiers") or []
        if not isinstance(tiers, list) or not all(isinstance(item, dict) for item in tiers):
            raise ValueError("price_tiers 必须是对象数组。")
        normalized_tiers: list[dict[str, Any]] = []
        for tier in tiers:
            item = dict(tier)
            for key in ("min_input_tokens", "max_input_tokens"):
                if item.get(key) not in (None, ""):
                    item[key] = int(item[key])
                    if item[key] < 0:
                        raise ValueError("价格分档 Token 边界不能为负数。")
            for key in ("input_price_per_million", "output_price_per_million", "cached_input_price_per_million"):
                item[key] = _optional_float(item.get(key))
            normalized_tiers.append(item)
        tags = raw.get("capability_tags") or []
        if not isinstance(tags, list):
            raise ValueError("capability_tags 必须是字符串数组。")
        return cls(
            provider_id=str(raw["provider_id"]).strip(),
            provider_name=str(raw["provider_name"]).strip(),
            model=str(raw["model"]).strip(),
            protocol=str(raw["protocol"]).strip(),
            api_base=api_base,
            pricing_summary=str(raw["pricing_summary"]).strip(),
            recommendation=str(raw["recommendation"]).strip(),
            model_url=str(raw["model_url"]).strip(),
            pricing_url=str(raw["pricing_url"]).strip(),
            last_verified=str(raw["last_verified"]).strip(),
            input_price_per_million=_optional_float(raw.get("input_price_per_million")),
            output_price_per_million=_optional_float(raw.get("output_price_per_million")),
            cached_input_price_per_million=_optional_float(raw.get("cached_input_price_per_million")),
            price_currency=str(raw.get("price_currency") or "USD").strip().upper(),
            price_effective_at=str(raw.get("price_effective_at") or raw["last_verified"]).strip(),
            pricing_notes=str(raw.get("pricing_notes") or "").strip(),
            context_window_tokens=_optional_int(raw.get("context_window_tokens")),
            max_output_tokens=_optional_int(raw.get("max_output_tokens")),
            capability_tags=tuple(str(item).strip() for item in tags if str(item).strip()),
            price_tiers=tuple(normalized_tiers),
        )


@dataclass(frozen=True, slots=True)
class ModelCatalogDocument:
    schema_version: int
    catalog_version: str
    updated_at: str
    currency_note: str
    models: tuple[ModelCatalogEntry, ...]
    source: str = "内置目录"
    content_hash: str = ""

    def find(self, provider_id: str, model: str) -> ModelCatalogEntry | None:
        provider_id = provider_id.strip().lower()
        model = model.strip().lower()
        return next(
            (item for item in self.models if item.provider_id.lower() == provider_id and item.model.lower() == model),
            None,
        )


@dataclass(frozen=True, slots=True)
class CatalogUpdateResult:
    status: str
    message: str
    document: ModelCatalogDocument
    changed_models: int = 0
    added_models: int = 0


def catalog_path() -> Path:
    return Path(__file__).resolve().with_name("model_catalog.json")


def _parse_document(payload: bytes, *, source: str) -> ModelCatalogDocument:
    if len(payload) > MAX_CATALOG_BYTES:
        raise ValueError("模型目录超过 2 MiB 安全限制。")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("模型目录不是有效的 UTF-8 JSON。") from error
    if not isinstance(raw, dict):
        raise ValueError("模型目录根节点必须是 JSON 对象。")
    schema = int(raw.get("schema_version", 1))
    if schema not in {1, CATALOG_SCHEMA_VERSION}:
        raise ValueError(f"不支持的模型目录 schema_version：{schema}")
    values = raw.get("models")
    if not isinstance(values, list) or not values:
        raise ValueError("模型目录必须包含非空 models 数组。")
    entries = tuple(ModelCatalogEntry.from_dict(item) for item in values if isinstance(item, dict))
    if len(entries) != len(values):
        raise ValueError("模型目录包含非对象条目。")
    keys = [item.catalog_key.lower() for item in entries]
    if len(keys) != len(set(keys)):
        raise ValueError("模型目录包含重复的服务商/模型组合。")
    digest = hashlib.sha256(payload).hexdigest()
    return ModelCatalogDocument(
        schema_version=schema,
        catalog_version=str(raw.get("catalog_version") or f"schema-{schema}"),
        updated_at=str(raw.get("updated_at") or max(item.last_verified for item in entries)),
        currency_note=str(raw.get("currency_note") or "价格以官方账单为准。"),
        models=entries,
        source=source,
        content_hash=digest,
    )


def load_model_catalog_document(path: Path | None = None, *, source: str = "内置目录") -> ModelCatalogDocument:
    target = path or catalog_path()
    return _parse_document(target.read_bytes(), source=source)


def load_model_catalog(path: Path | None = None) -> list[ModelCatalogEntry]:
    """Compatibility API returning only entries."""

    return list(load_model_catalog_document(path).models)


class ModelCatalogService:
    """Load and safely refresh a last-known-good catalog."""

    def __init__(
        self,
        state_directory: Path,
        *,
        builtin: Path | None = None,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.state_directory = Path(state_directory)
        self.builtin = builtin or catalog_path()
        self.cache_path = self.state_directory / "model_catalog_cache.json"
        self.state_path = self.state_directory / "model_catalog_state.json"
        self.opener = opener

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    def _write_state(self, **updates: Any) -> None:
        state = self._read_state()
        state.update(updates)
        self.state_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def state(self) -> dict[str, Any]:
        return self._read_state()

    def load_effective(self) -> ModelCatalogDocument:
        if self.cache_path.is_file():
            try:
                source = str(self._read_state().get("source") or "最近有效缓存")
                return load_model_catalog_document(self.cache_path, source=source)
            except (OSError, ValueError):
                pass
        return load_model_catalog_document(self.builtin, source="内置目录")

    def should_check(self, interval_days: int) -> bool:
        last_checked = str(self._read_state().get("last_checked_at") or "")
        if not last_checked:
            return True
        try:
            checked = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - checked >= timedelta(days=max(1, interval_days))

    @staticmethod
    def _diff(old: ModelCatalogDocument, new: ModelCatalogDocument) -> tuple[int, int]:
        old_by_key = {item.catalog_key: item for item in old.models}
        added = sum(item.catalog_key not in old_by_key for item in new.models)
        changed = sum(
            item.catalog_key in old_by_key and item != old_by_key[item.catalog_key]
            for item in new.models
        )
        return changed, added

    def _install(self, payload: bytes, *, source: str, metadata: dict[str, Any] | None = None) -> CatalogUpdateResult:
        old = self.load_effective()
        document = _parse_document(payload, source=source)
        self.state_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(self.cache_path)
        changed, added = self._diff(old, document)
        now = _now_iso()
        updates = {
            "last_checked_at": now,
            "last_success_at": now,
            "last_error": "",
            "source": source,
            "catalog_version": document.catalog_version,
            "content_hash": document.content_hash,
        }
        updates.update(metadata or {})
        self._write_state(**updates)
        effective = load_model_catalog_document(self.cache_path, source=source)
        return CatalogUpdateResult(
            "updated", f"目录更新成功：{changed} 个模型变化，{added} 个新增。",
            effective, changed_models=changed, added_models=added,
        )

    def import_file(self, path: Path) -> CatalogUpdateResult:
        try:
            return self._install(Path(path).read_bytes(), source=f"本地文件：{Path(path).name}")
        except (OSError, ValueError) as error:
            self._write_state(last_checked_at=_now_iso(), last_error=str(error))
            raise

    def update_from_url(self, url: str, *, timeout: int = 15) -> CatalogUpdateResult:
        url = url.strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("远程目录地址必须是有效的 HTTPS URL。")
        state = self._read_state()
        headers = {"Accept": "application/json", "User-Agent": "ReviewWriter/0.7 model-catalog"}
        if state.get("etag"):
            headers["If-None-Match"] = str(state["etag"])
        if state.get("last_modified"):
            headers["If-Modified-Since"] = str(state["last_modified"])
        request = Request(url, headers=headers)
        try:
            response = self.opener(request, timeout=max(5, timeout))
            with response:
                final_url = response.geturl() if hasattr(response, "geturl") else url
                if urlparse(final_url).scheme != "https":
                    raise ValueError("模型目录重定向到了非 HTTPS 地址，已拒绝载入。")
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        oversized = int(length) > MAX_CATALOG_BYTES
                    except (TypeError, ValueError):
                        oversized = False
                    if oversized:
                        raise ValueError("远程模型目录超过 2 MiB 安全限制。")
                payload = response.read(MAX_CATALOG_BYTES + 1)
                metadata = {
                    "etag": response.headers.get("ETag", ""),
                    "last_modified": response.headers.get("Last-Modified", ""),
                    "remote_url": url,
                }
        except HTTPError as error:
            if error.code == 304:
                error.close()
                document = self.load_effective()
                self._write_state(last_checked_at=_now_iso(), last_error="", remote_url=url)
                return CatalogUpdateResult("not_modified", "远程目录没有变化。", document)
            self._write_state(last_checked_at=_now_iso(), last_error=f"HTTP {error.code}")
            raise ValueError(f"下载模型目录失败：HTTP {error.code}") from error
        except (URLError, OSError) as error:
            self._write_state(last_checked_at=_now_iso(), last_error=str(error))
            raise ValueError(f"无法连接模型目录：{error}") from error
        try:
            return self._install(payload, source=f"远程目录：{parsed.netloc}", metadata=metadata)
        except (OSError, ValueError) as error:
            self._write_state(last_checked_at=_now_iso(), last_error=str(error), remote_url=url)
            raise


def catalog_entry_price(entry: ModelCatalogEntry, input_tokens: int) -> tuple[float | None, float | None, float | None]:
    """Resolve the applicable tier for an estimated input size."""

    for tier in entry.price_tiers:
        minimum = int(tier.get("min_input_tokens") or 0)
        maximum = tier.get("max_input_tokens")
        if input_tokens >= minimum and (maximum in (None, "") or input_tokens <= int(maximum)):
            return (
                _optional_float(tier.get("input_price_per_million")),
                _optional_float(tier.get("output_price_per_million")),
                _optional_float(tier.get("cached_input_price_per_million")),
            )
    return entry.input_price_per_million, entry.output_price_per_million, entry.cached_input_price_per_million
