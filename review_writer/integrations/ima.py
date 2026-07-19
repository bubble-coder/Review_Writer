"""Read-only IMA knowledge-base adapter using the official OpenAPI."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..settings import ImaSettings
from .base import ConnectionResult, IntegrationError, SearchPreviewItem


class ImaConnector:
    def __init__(
        self,
        settings: ImaSettings,
        *,
        client_id: str | None,
        api_key: str | None,
        opener: Any = urlopen,
    ) -> None:
        self.settings = settings
        self.client_id = (client_id or "").strip()
        self.api_key = (api_key or "").strip()
        self.opener = opener

    def _call(self, api_path: str, body: dict[str, Any], *, timeout: int = 20) -> dict[str, Any]:
        if not self.client_id or not self.api_key:
            raise IntegrationError("请先填写 IMA Client ID 和 API Key。")
        base_url = self.settings.api_base.strip().rstrip("/")
        parsed = urlparse(base_url)
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if not parsed.netloc or (parsed.scheme != "https" and parsed.hostname not in local_hosts):
            raise IntegrationError("IMA API Base 必须使用 HTTPS。")
        request = Request(
            f"{base_url}/{api_path.lstrip('/')}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "ima-openapi-clientid": self.client_id,
                "ima-openapi-apikey": self.api_key,
                "ima-openapi-ctx": "skill_version=review-writer-0.7.0",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "ReviewWriter/0.7",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=max(5, timeout)) as opened:
                raw = opened.read().decode("utf-8")
        except HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace")
            finally:
                error.close()
            try:
                payload = json.loads(detail)
                message = str(payload.get("msg") or payload.get("message") or "")
            except (AttributeError, json.JSONDecodeError):
                message = ""
            raise IntegrationError(message or f"IMA API 返回 HTTP {error.code}。") from error
        except (URLError, TimeoutError, OSError) as error:
            raise IntegrationError(f"无法连接 IMA OpenAPI：{error}") from error
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as error:
            raise IntegrationError("IMA OpenAPI 没有返回有效 JSON。") from error
        if not isinstance(response, dict):
            raise IntegrationError("IMA 响应结构无效。")
        if str(response.get("code")) != "0":
            raise IntegrationError(str(response.get("msg") or "IMA API 调用失败"))
        data = response.get("data", {})
        return data if isinstance(data, dict) else {}

    def list_knowledge_bases(self, *, maximum: int = 100) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        cursor = ""
        while len(results) < maximum:
            data = self._call(
                "openapi/wiki/v1/search_knowledge_base",
                {"query": "", "cursor": cursor, "limit": min(20, maximum - len(results))},
            )
            for item in data.get("info_list", []):
                if isinstance(item, dict) and item.get("id") and item.get("name"):
                    results.append({"id": str(item["id"]), "name": str(item["name"])})
            if data.get("is_end", True):
                break
            next_cursor = str(data.get("next_cursor") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return results

    def check(self) -> ConnectionResult:
        try:
            knowledge_bases = self.list_knowledge_bases()
        except IntegrationError as error:
            return ConnectionResult(False, "api_error", str(error))
        selected_visible = not self.settings.knowledge_base_id or any(
            item["id"] == self.settings.knowledge_base_id for item in knowledge_bases
        )
        if self.settings.knowledge_base_id and not selected_visible:
            return ConnectionResult(
                False,
                "shared_kb_not_visible",
                "已保存的共享知识库没有出现在官方 OpenAPI 返回结果中；不会尝试抓取 IMA 客户端。",
                {"knowledge_bases": knowledge_bases},
            )
        return ConnectionResult(
            True,
            "ready",
            f"IMA OpenAPI 可用，共发现 {len(knowledge_bases)} 个可见知识库。",
            {"knowledge_bases": knowledge_bases},
        )

    def search_preview(self, query: str, *, limit: int = 5) -> list[SearchPreviewItem]:
        query = query.strip()
        if not query:
            raise IntegrationError("请输入 IMA 知识库预览检索词。")
        if not self.settings.knowledge_base_id:
            raise IntegrationError("请先从官方 OpenAPI 返回的列表中选择一个 IMA 知识库。")
        data = self._call(
            "openapi/wiki/v1/search_knowledge",
            {
                "query": query,
                "knowledge_base_id": self.settings.knowledge_base_id,
                "cursor": "",
            },
        )
        rows = [item for item in data.get("info_list", []) if isinstance(item, dict)][:limit]
        results: list[SearchPreviewItem] = []
        for index, item in enumerate(rows):
            media_id = str(item.get("media_id") or "")
            evidence = "IMA 知识库片段证据"
            access_status = "未探测原文"
            url = ""
            if index == 0 and media_id:
                try:
                    media = self._call(
                        "openapi/wiki/v1/get_media_info",
                        {"media_id": media_id},
                    )
                except IntegrationError:
                    access_status = "官方接口未返回原文"
                else:
                    url_info = media.get("url_info") or {}
                    if isinstance(url_info, dict) and url_info.get("url"):
                        evidence = "IMA 全文候选"
                        access_status = "官方接口返回可访问原文"
                        url = str(url_info["url"])
                    elif media.get("media_type") == 11:
                        access_status = "IMA 笔记条目，本阶段不读取笔记正文"
                    else:
                        access_status = "仅可在 IMA 客户端查看原文"
            results.append(
                SearchPreviewItem(
                    source="IMA",
                    title=str(item.get("title") or "未命名知识条目"),
                    url=url,
                    evidence_level=evidence,
                    access_status=access_status,
                    snippet=str(item.get("highlight_content") or ""),
                    internal_id=media_id,
                )
            )
        return results
