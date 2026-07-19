"""Read-only Zotero Desktop adapter following the bundled Zotero skill."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..settings import ZoteroSettings
from .base import ConnectionResult, IntegrationError, SearchPreviewItem
from .runtime import run_json_command


def discover_zotero_helper() -> Path | None:
    if getattr(sys, "frozen", False):
        return None
    cache = Path.home() / ".codex" / "plugins" / "cache"
    if not cache.exists():
        return None
    candidates = sorted(cache.glob("**/skills/zotero/scripts/zotero.py"))
    return candidates[0] if candidates else None


class ZoteroConnector:
    def __init__(self, settings: ZoteroSettings, helper_path: Path | None = None) -> None:
        self.settings = settings
        self.helper_path = helper_path or discover_zotero_helper()

    def _run_helper(self, *arguments: str, timeout: int = 15) -> Any:
        if not self.helper_path or not self.helper_path.is_file():
            raise IntegrationError("未找到 Zotero 本地连接器 helper。")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        raw = run_json_command(
            [sys.executable, str(self.helper_path), *arguments],
            timeout=timeout,
            env=env,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise IntegrationError("Zotero helper 没有返回有效 JSON。") from error

    def check(self) -> ConnectionResult:
        try:
            status = self._run_helper("status", "--json")
        except IntegrationError:
            pass
        else:
            api_running = bool(status.get("api_running"))
            connector_running = bool(status.get("connector_running"))
            if not api_running:
                return ConnectionResult(
                    False,
                    "api_disabled",
                    "Zotero 已检测到，但本地 API 未运行；请在 Zotero 设置中启用本地 API 后重启。",
                    status,
                )
            return ConnectionResult(
                True,
                "ready",
                f"Zotero {status.get('zotero_version') or ''} 本地 API 可用"
                + ("，Connector 可用。" if connector_running else "，Connector 未运行。"),
                status,
            )
        try:
            self._api_get("users/0/items?limit=1")
        except IntegrationError as error:
            return ConnectionResult(False, "api_unavailable", str(error))
        return ConnectionResult(
            True,
            "ready",
            "Zotero Desktop 本地 API 可用。",
            {"api_running": True, "connector_running": False},
        )

    def _api_get(self, path: str) -> Any:
        url = f"{self.settings.base_url.rstrip('/')}/api/{path.lstrip('/')}"
        request = Request(url, headers={"Zotero-API-Version": "3"}, method="GET")
        try:
            with urlopen(request, timeout=8) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            raise IntegrationError(f"Zotero 本地 API 返回 HTTP {error.code}。") from error
        except (URLError, TimeoutError, OSError) as error:
            raise IntegrationError(f"无法连接 Zotero 本地 API：{error}") from error
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise IntegrationError("Zotero 本地 API 响应不是有效 JSON。") from error

    def _api_write(self, path: str, payload: Any, *, method: str, expected_version: int | None = None) -> Any:
        url = f"{self.settings.base_url.rstrip('/')}/api/{path.lstrip('/')}"
        headers = {"Zotero-API-Version": "3", "Content-Type": "application/json"}
        if expected_version is not None:
            headers["If-Unmodified-Since-Version"] = str(expected_version)
        request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method=method)
        try:
            with urlopen(request, timeout=12) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            raise IntegrationError(f"Zotero 写入返回 HTTP {error.code}；未继续后续变更。") from error
        except (URLError, TimeoutError, OSError) as error:
            raise IntegrationError(f"Zotero 写入连接失败：{error}") from error
        if not raw.strip():
            return {"ok": True}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": True, "response": raw[:300]}

    def _matches_filters(self, data: dict[str, Any], collection_keys: set[str]) -> bool:
        tag_filter = self.settings.tag_filter.strip().casefold()
        if tag_filter:
            tags = {str(item.get("tag", "")).casefold() for item in data.get("tags", [])}
            if tag_filter not in tags:
                return False
        if collection_keys:
            if not collection_keys.intersection(set(data.get("collections", []))):
                return False
        return True

    def _collection_keys(self) -> set[str]:
        name_filter = self.settings.collection_filter.strip().casefold()
        if not name_filter:
            return set()
        collections = self._api_get("users/0/collections")
        return {
            str(item.get("key", ""))
            for item in collections
            if name_filter in str((item.get("data") or {}).get("name", "")).casefold()
        }

    def _direct_search_rows(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        parameters = urlencode(
            {
                "q": query,
                "qmode": "everything" if self.settings.search_indexed_fulltext else "titleCreatorYear",
                "limit": min(max(limit * 5, 20), 100),
            }
        )
        payload = self._api_get(f"users/0/items?{parameters}")
        if not isinstance(payload, list):
            raise IntegrationError("Zotero 本地 API 搜索结果结构无效。")
        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            data = item.get("data", item)
            if not isinstance(data, dict) or data.get("itemType") in {"annotation", "attachment", "note"}:
                continue
            creators: list[str] = []
            for creator in data.get("creators", []):
                if not isinstance(creator, dict):
                    continue
                name = str(creator.get("name") or "").strip()
                if not name:
                    name = " ".join(
                        part
                        for part in (
                            str(creator.get("firstName") or "").strip(),
                            str(creator.get("lastName") or "").strip(),
                        )
                        if part
                    )
                if name:
                    creators.append(name)
            date_match = re.search(r"\b(?:19|20)\d{2}\b", str(data.get("date") or ""))
            rows.append(
                {
                    "key": str(item.get("key") or data.get("key") or ""),
                    "title": str(data.get("title") or ""),
                    "creators": creators,
                    "year": date_match.group(0) if date_match else "",
                    "bibtexKey": str(data.get("citationKey") or ""),
                }
            )
        return rows

    def search_preview(self, query: str, *, limit: int = 5) -> list[SearchPreviewItem]:
        query = query.strip()
        if not query:
            raise IntegrationError("请输入 Zotero 预览检索词。")
        try:
            rows = self._run_helper("search", query, "--with-bibtex-keys", "--json", timeout=30)
        except IntegrationError:
            rows = self._direct_search_rows(query, limit=limit)
        if not isinstance(rows, list):
            raise IntegrationError("Zotero 搜索结果结构无效。")
        collection_keys = self._collection_keys()
        results: list[SearchPreviewItem] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("key"):
                continue
            key = str(row["key"])
            item = self._api_get(f"users/0/items/{quote(key)}")
            data = item.get("data", item) if isinstance(item, dict) else {}
            if not isinstance(data, dict) or not self._matches_filters(data, collection_keys):
                continue
            attachments = 0
            if self.settings.inspect_attachments:
                children = self._api_get(f"users/0/items/{quote(key)}/children")
                attachments = sum(
                    1
                    for child in children
                    if isinstance(child, dict)
                    and (child.get("data") or {}).get("itemType") == "attachment"
                )
            results.append(
                SearchPreviewItem(
                    source="Zotero",
                    title=str(data.get("title") or row.get("title") or "未命名条目"),
                    creators=[str(value) for value in row.get("creators", [])],
                    year=str(row.get("year") or ""),
                    doi=str(data.get("DOI") or ""),
                    url=str(data.get("url") or ""),
                    evidence_level="本地全文候选" if attachments else "Zotero 元数据",
                    access_status=f"检测到 {attachments} 个附件" if attachments else "未检测附件",
                    citation_key=str(row.get("bibtexKey") or ""),
                    internal_id=key,
                )
            )
            if len(results) >= limit:
                break
        return results

    def propose_enrichment(
        self,
        item_key: str,
        *,
        add_tags: list[str] | None = None,
        note_html: str = "",
    ) -> "ZoteroWriteProposal":
        item = self._api_get(f"users/0/items/{quote(item_key)}")
        data = item.get("data", item) if isinstance(item, dict) else {}
        if not isinstance(data, dict):
            raise IntegrationError("Zotero 条目结构无效。")
        existing = {str(value.get("tag") or "") for value in data.get("tags", []) if isinstance(value, dict)}
        requested = [str(value).strip() for value in (add_tags or []) if str(value).strip()]
        return ZoteroWriteProposal(
            item_key=item_key,
            title=str(data.get("title") or "未命名条目"),
            expected_version=int(item.get("version") or data.get("version") or 0),
            add_tags=[value for value in requested if value not in existing],
            note_html=note_html.strip(),
        )

    def apply_enrichment(self, proposals: list["ZoteroWriteProposal"], *, user_confirmed: bool = False) -> list[dict[str, Any]]:
        """Apply only a previously previewed, explicitly confirmed change set."""

        if not self.settings.allow_confirmed_writes:
            raise IntegrationError("Zotero 写入开关未启用；当前保持只读。")
        if not user_confirmed:
            raise IntegrationError("必须在界面预览变更并由用户明确确认后才能写入 Zotero。")
        receipts: list[dict[str, Any]] = []
        for proposal in proposals:
            item = self._api_get(f"users/0/items/{quote(proposal.item_key)}")
            current_version = int(item.get("version") or (item.get("data") or {}).get("version") or 0)
            if proposal.expected_version and current_version != proposal.expected_version:
                raise IntegrationError(f"Zotero 条目“{proposal.title}”已被修改；为避免覆盖，已停止写入。")
            data = dict(item.get("data", item))
            if proposal.add_tags:
                tags = [dict(value) for value in data.get("tags", []) if isinstance(value, dict)]
                tags.extend({"tag": value} for value in proposal.add_tags)
                data["tags"] = tags
                self._api_write(f"users/0/items/{quote(proposal.item_key)}", data, method="PUT", expected_version=current_version or None)
            if proposal.note_html:
                self._api_write(
                    "users/0/items",
                    [{"itemType": "note", "parentItem": proposal.item_key, "note": proposal.note_html, "tags": []}],
                    method="POST",
                )
            receipts.append({"item_key": proposal.item_key, "title": proposal.title, "tags_added": list(proposal.add_tags), "note_added": bool(proposal.note_html)})
        return receipts


@dataclass(slots=True)
class ZoteroWriteProposal:
    item_key: str
    title: str
    expected_version: int
    add_tags: list[str] = field(default_factory=list)
    note_html: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
