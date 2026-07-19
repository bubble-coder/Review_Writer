"""Persistent non-secret application settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
from pathlib import Path
from typing import Any, TypeVar

from .app_paths import user_data_root


T = TypeVar("T")


def default_settings_path() -> Path:
    return user_data_root() / "settings.json"


def _from_mapping(cls: type[T], raw: Any) -> T:
    values = raw if isinstance(raw, dict) else {}
    allowed = {item.name for item in fields(cls)}
    return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass(slots=True)
class ModelSettings:
    provider_id: str = "openai"
    provider_name: str = "OpenAI"
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-5.6"
    protocol: str = "openai_responses"
    timeout_seconds: int = 60
    temperature: float = 0.2
    max_output_tokens: int = 6000
    persist_api_key: bool = True
    context_window_tokens: int = 128000
    maximum_data_class: str = "abstract"
    require_external_confirmation: bool = True
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    price_currency: str = "USD"
    price_tiers: list[dict[str, Any]] = field(default_factory=list)
    pricing_mode: str = "catalog"
    pricing_catalog_key: str = "openai:gpt-5.6"
    pricing_updated_at: str = ""
    pricing_source: str = ""

    def is_configured(self, api_key: str | None) -> bool:
        key_optional = self.protocol == "ollama"
        return bool(self.api_base.strip() and self.model.strip() and (key_optional or api_key))


@dataclass(slots=True)
class LibrarySettings:
    enabled: bool = False
    portal_url: str = ""
    web_of_science_url: str = ""
    cnki_url: str = "https://kns.cnki.net/kns8s/defaultresult/index"
    cdp_proxy_url: str = "http://127.0.0.1:3456"
    download_directory: str = ""
    max_batch_size: int = 10
    pdf_only: bool = True
    include_supporting_information: bool = False


@dataclass(slots=True)
class DiscoverySettings:
    """Public metadata discovery sources that require no private credentials."""

    openalex_enabled: bool = True
    crossref_enabled: bool = True
    polite_email: str = ""
    default_limit: int = 20
    timeout_seconds: int = 20


@dataclass(slots=True)
class ZoteroSettings:
    enabled: bool = True
    base_url: str = "http://127.0.0.1:23119"
    collection_filter: str = ""
    tag_filter: str = ""
    search_indexed_fulltext: bool = False
    inspect_attachments: bool = True
    allow_confirmed_writes: bool = False


@dataclass(slots=True)
class ImaSettings:
    enabled: bool = False
    api_base: str = "https://ima.qq.com"
    knowledge_base_id: str = ""
    knowledge_base_name: str = ""
    persist_credentials: bool = True


@dataclass(slots=True)
class AppearanceSettings:
    """Non-secret visual preferences for the desktop interface."""

    theme_id: str = "ocean"
    custom_accent: str = "#2563eb"
    ui_font: str = ""
    mono_font: str = ""

    def __post_init__(self) -> None:
        self.ui_font = self._safe_font_preference(self.ui_font)
        self.mono_font = self._safe_font_preference(self.mono_font)

    @staticmethod
    def _safe_font_preference(value: Any) -> str:
        candidate = str(value or "").strip()
        return "" if candidate.startswith("@") else candidate


@dataclass(slots=True)
class ModelCatalogSettings:
    """Update policy for the non-secret recommendation catalog."""

    update_url: str = ""
    auto_check: bool = True
    update_interval_days: int = 7


@dataclass(slots=True)
class AppSettings:
    schema_version: int = 5
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    model_catalog: ModelCatalogSettings = field(default_factory=ModelCatalogSettings)
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    library: LibrarySettings = field(default_factory=LibrarySettings)
    zotero: ZoteroSettings = field(default_factory=ZoteroSettings)
    ima: ImaSettings = field(default_factory=ImaSettings)

    @classmethod
    def from_dict(cls, raw: Any) -> "AppSettings":
        values = raw if isinstance(raw, dict) else {}
        return cls(
            schema_version=5,
            appearance=_from_mapping(AppearanceSettings, values.get("appearance")),
            model=_from_mapping(ModelSettings, values.get("model")),
            model_catalog=_from_mapping(ModelCatalogSettings, values.get("model_catalog")),
            discovery=_from_mapping(DiscoverySettings, values.get("discovery")),
            library=_from_mapping(LibrarySettings, values.get("library")),
            zotero=_from_mapping(ZoteroSettings, values.get("zotero")),
            ima=_from_mapping(ImaSettings, values.get("ima")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SettingsStore:
    """UTF-8 JSON store for settings that never include credentials."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return AppSettings()
        return AppSettings.from_dict(raw)

    def save(self, settings: AppSettings) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
        return self.path
