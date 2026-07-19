"""Institutional-library configuration diagnostics based on nature-downloader."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..app_paths import resource_path
from ..settings import LibrarySettings
from .base import ConnectionResult
from .runtime import find_node, hidden_subprocess_kwargs


def infer_access_route(url: str) -> str:
    value = url.lower()
    if not value:
        return "未配置"
    if "authserver/login" in value or re.search(r"(^|\.)cas\.", urlparse(value).hostname or ""):
        return "CAS / SSO"
    if any(token in value for token in ("shibboleth", "carsi", "openathens", "/idp/")):
        return "CARSI / Shibboleth"
    if "ezproxy" in value or "libproxy" in value:
        return "EZproxy"
    if "webvpn" in value or "/vpn" in value:
        return "WebVPN"
    if any(token in value for token in ("metaersp", "metaauth", "/uas")):
        return "图书馆资源聚合门户"
    if any(token in value for token in ("webofscience", "clarivate", "cnki", "sciencedirect")):
        return "数据库或出版商入口"
    return "普通电子资源入口"


def _probe_url(url: str, timeout: int = 6) -> tuple[bool, str, int | None]:
    request = Request(url, headers={"User-Agent": "ReviewWriter/0.7"}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return True, response.geturl(), getattr(response, "status", 200)
    except HTTPError as error:
        if error.code in {401, 403}:
            return True, error.geturl(), error.code
        return False, error.geturl(), error.code
    except (URLError, TimeoutError, OSError) as error:
        return False, str(error.reason if isinstance(error, URLError) else error), None


class LibraryConnector:
    def __init__(self, settings: LibrarySettings) -> None:
        self.settings = settings
        self.skill_root = resource_path("vendor", "nature-downloader")

    def _node_version(self) -> str | None:
        node = find_node()
        if not node:
            return None
        try:
            result = subprocess.run(
                [str(node), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    def check(self) -> ConnectionResult:
        portal = self.settings.portal_url.strip()
        if not portal:
            return ConnectionResult(False, "config_required", "请先填写实际使用的图书馆电子资源入口 URL。")
        parsed = urlparse(portal)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ConnectionResult(False, "invalid_url", "图书馆入口必须是完整的 http/https URL。")

        scripts_ok = (self.skill_root / "scripts" / "configure_school.py").is_file()
        node_version = self._node_version()
        portal_ok, final_url, status_code = _probe_url(portal)
        cdp_endpoint = f"{self.settings.cdp_proxy_url.rstrip('/')}/targets"
        cdp_ok, _cdp_detail, _cdp_status = _probe_url(cdp_endpoint, timeout=2)
        route = infer_access_route(final_url if portal_ok else portal)

        details = {
            "route": route,
            "portal_status": status_code,
            "final_url": final_url,
            "node_version": node_version,
            "nature_downloader_found": scripts_ok,
            "browser_control_ready": cdp_ok,
        }
        if not scripts_ok or not node_version:
            return ConnectionResult(
                False,
                "runtime_missing",
                "机构资源入口已记录，但 nature-downloader 或 Node.js 运行环境不完整。",
                details,
            )
        if not portal_ok:
            return ConnectionResult(False, "portal_unreachable", f"无法访问资源入口：{final_url}", details)
        if not cdp_ok:
            return ConnectionResult(
                True,
                "login_handoff_required",
                f"资源入口可访问，识别为“{route}”；浏览器控制尚未连接，后续下载时需在同一 Chrome 会话中登录。",
                details,
            )
        return ConnectionResult(
            True,
            "ready",
            f"资源入口与浏览器控制均可用，识别为“{route}”。",
            details,
        )

    def export_nature_profile(self, directory: Path) -> Path:
        """Export a compatible, non-secret school profile for later download work."""

        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": "review-writer",
            "resource_entry_url": self.settings.portal_url,
            "route_type": infer_access_route(self.settings.portal_url),
            "discovery": {
                "web_of_science_url": self.settings.web_of_science_url,
                "cnki_url": self.settings.cnki_url,
            },
        }
        path = directory / "school.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
