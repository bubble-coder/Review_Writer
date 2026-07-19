"""Safe full-text acquisition and PDF verification.

The downloader is a deliberately small adapter around the project's bundled
``nature-downloader`` scripts.  It accepts only explicit paper identifiers,
uses an application-owned non-secret school profile, and never handles login
credentials, cookies, CAPTCHA, OTP, or publisher verification challenges.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .app_paths import resource_path
from .integrations.library import infer_access_route
from .integrations.runtime import find_node, hidden_subprocess_kwargs
from .settings import LibrarySettings
from .workflow_models import EvidenceLevel, PaperRecord, normalize_doi


SKILL_ROOT = resource_path("vendor", "nature-downloader")
HARD_MAX_BATCH = 20
DEFAULT_MAX_BATCH = 10

CANONICAL_DOWNLOAD_STATUSES = frozenset(
    {
        "downloaded",
        "downloaded_with_si",
        "open_access_downloaded",
        "full_text_html_available",
        "available_not_downloaded",
        "carsi_waiting_user",
        "carsi_resolved_retry_needed",
        "publisher_verification_waiting_user",
        "sciencedirect_robot_check",
        "retry_after_user_verification",
        "do_not_auto_retry",
        "url_needs_repair",
        "library_no_permission",
        "no_full_text_link",
        "publisher_blocked_waiting_user",
        "no_authorized_pdf_found",
        "pdf_fetch_failed",
        "pdf_corrupt",
        "pdf_too_short",
        "pdf_too_large",
        "si_fetch_failed",
        "failed_after_retry",
    }
)

LEGACY_STATUS_MAP = {
    "needs_user_login": "carsi_waiting_user",
    "needs_user_verify": "publisher_verification_waiting_user",
    "publisher_blocked": "publisher_blocked_waiting_user",
    "no_pdf_link": "no_full_text_link",
    "error": "failed_after_retry",
}

USER_HANDOFF_STATUSES = frozenset(
    {
        "carsi_waiting_user",
        "carsi_resolved_retry_needed",
        "publisher_verification_waiting_user",
        "sciencedirect_robot_check",
        "retry_after_user_verification",
        "publisher_blocked_waiting_user",
    }
)

SUCCESS_STATUSES = frozenset(
    {
        "downloaded",
        "downloaded_with_si",
        "open_access_downloaded",
        "full_text_html_available",
    }
)

_DOI_RE = re.compile(r"^10\.\d{4,9}/[^\s,]+$", re.I)
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth|authorization|code|cookie|credential|jwt|otp|password|saml|secret|session|sig|signature|ticket)",
    re.I,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|authorization|cookie|password|secret|session)"
    r"(\s*[:=]\s*)([^\s&;,]+)"
)


class FullTextError(RuntimeError):
    """Raised when a safe local full-text operation cannot be completed."""


@dataclass(slots=True)
class DownloadRequest:
    """One user-confirmed paper to retrieve.

    A DOI, exact title, or lawful HTTP(S) PDF URL is required.  ``title`` may
    accompany a PDF URL solely to produce a readable filename.
    """

    paper_id: str = ""
    doi: str = ""
    title: str = ""
    pdf_url: str = ""
    open_access: bool = False

    @classmethod
    def from_paper(cls, paper: PaperRecord, *, pdf_url: str = "") -> "DownloadRequest":
        return cls(
            paper_id=paper.record_id,
            doi=paper.doi,
            title=paper.title,
            pdf_url=pdf_url,
        )

    def validated(self) -> "DownloadRequest":
        paper_id = str(self.paper_id or "").strip()
        doi = normalize_doi(self.doi)
        title = str(self.title or "").strip()
        pdf_url = _safe_public_url(self.pdf_url) if self.pdf_url else ""

        if any(ord(char) < 32 and char not in "\t\n" for char in title):
            raise ValueError("题名包含不可用的控制字符")
        if len(title) > 1000:
            raise ValueError("题名过长")
        if doi and not _DOI_RE.fullmatch(doi):
            raise ValueError(f"DOI 格式无效：{doi[:120]}")
        if not (doi or title or pdf_url):
            raise ValueError("每条全文请求必须包含 DOI、精确题名或 PDF URL")
        if pdf_url and doi:
            raise ValueError("同一条请求不能同时指定 DOI 与 PDF URL")
        if doi and self.open_access:
            raise ValueError("open_access 仅用于无 DOI 的精确题名检索")
        return DownloadRequest(
            paper_id=paper_id,
            doi=doi,
            title=title,
            pdf_url=pdf_url,
            open_access=bool(self.open_access),
        )


@dataclass(slots=True)
class ExtractedPage:
    page_number: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PDFVerification:
    path: str
    status: str
    valid_pdf: bool
    page_count: int = 0
    file_size: int = 0
    extracted_characters: int = 0
    pages_with_text: int = 0
    ocr_needed: bool = False
    title_match: bool | None = None
    pages: list[ExtractedPage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def extractable_full_text(self) -> bool:
        return (
            self.valid_pdf
            and not self.ocr_needed
            and self.extracted_characters > 0
            and self.title_match is not False
        )

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        value = {
            "path": self.path,
            "status": self.status,
            "valid_pdf": self.valid_pdf,
            "page_count": self.page_count,
            "file_size": self.file_size,
            "extracted_characters": self.extracted_characters,
            "pages_with_text": self.pages_with_text,
            "ocr_needed": self.ocr_needed,
            "title_match": self.title_match,
            "warnings": list(self.warnings),
            "error": self.error,
        }
        if include_text:
            value["pages"] = [page.to_dict() for page in self.pages]
        return value


@dataclass(slots=True)
class DownloadItemResult:
    paper_id: str
    doi: str
    title: str
    status: str
    url: str = ""
    file: str = ""
    bytes: int = 0
    reason: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.METADATA_ONLY
    verification: PDFVerification | None = None

    @property
    def needs_user_action(self) -> bool:
        return self.status in USER_HANDOFF_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "doi": self.doi,
            "title": self.title,
            "status": self.status,
            "url": self.url,
            "file": self.file,
            "bytes": self.bytes,
            "reason": self.reason,
            "evidence_level": self.evidence_level.value,
            "evidence_label": self.evidence_level.label,
            "needs_user_action": self.needs_user_action,
            "verification": self.verification.to_dict() if self.verification else None,
        }


@dataclass(slots=True)
class BatchDownloadResult:
    total: int
    downloaded: int
    seconds: float
    results: list[DownloadItemResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "total": self.total,
                "downloaded": self.downloaded,
                "seconds": self.seconds,
            },
            "results": [item.to_dict() for item in self.results],
        }


def canonical_status(value: Any) -> str:
    """Map old downloader statuses and reject unknown status vocabulary."""

    status = str(value or "").strip().casefold()
    status = LEGACY_STATUS_MAP.get(status, status)
    return status if status in CANONICAL_DOWNLOAD_STATUSES else "failed_after_retry"


def _safe_public_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 4096:
        raise ValueError("URL 过长")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("必须提供完整的 HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("URL 不能包含用户名或密码")
    safe_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _SENSITIVE_QUERY_KEY.search(key)
    ]
    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"
    fragment = "" if _SENSITIVE_QUERY_KEY.search(parsed.fragment) else parsed.fragment
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(safe_query), fragment))


def _sanitize_message(value: Any) -> str:
    text = str(value or "").strip()
    text = _SENSITIVE_TEXT.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    return text[:800]


def _auth_type(route: str) -> str:
    route_lower = route.casefold()
    for token, value in (
        ("carsi", "carsi"),
        ("shibboleth", "shibboleth"),
        ("cas", "cas"),
        ("sso", "sso"),
        ("ezproxy", "ezproxy"),
        ("webvpn", "webvpn"),
    ):
        if token in route_lower:
            return value
    return "browser_session"


def export_school_profile(settings: LibrarySettings, directory: Path) -> Path:
    """Write a nature-downloader profile containing only public route data."""

    portal = _safe_public_url(settings.portal_url)
    if not portal:
        raise ValueError("请先配置实际使用的图书馆电子资源入口 URL")
    wos = _safe_public_url(settings.web_of_science_url) if settings.web_of_science_url else ""
    cnki = _safe_public_url(settings.cnki_url) if settings.cnki_url else ""
    route = infer_access_route(portal)
    host = urlsplit(portal).hostname or "configured-in-browser"
    payload = {
        "version": 1,
        "source": "review-writer",
        "school": {"name": "用户配置的机构"},
        "auth": {"type": _auth_type(route), "sso_domain": host},
        "libraries": [
            {
                "name": "电子资源入口",
                "url": portal,
                "route_type": route,
            }
        ],
        "discovery": {
            "web_of_science_url": wos,
            "cnki_url": cnki,
        },
    }
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "school.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def parse_download_output(stdout: str) -> dict[str, Any]:
    """Extract one compact downloader JSON object from otherwise noisy output."""

    text = str(stdout or "").strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("results"), list):
            summary = value.get("summary")
            if not isinstance(summary, dict):
                raise FullTextError("下载器 JSON 缺少 summary 对象")
            return value
    raise FullTextError("下载器没有返回可解析的结果 JSON")


def _clean_page_text(text: Any) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _normalized_title(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value.casefold())


def verify_pdf(
    path: Path,
    *,
    expected_title: str = "",
    minimum_bytes: int = 64,
    maximum_bytes: int = 250 * 1024 * 1024,
) -> PDFVerification:
    """Verify signature/pages and extract page text without applying OCR."""

    pdf_path = Path(path).resolve()
    if not pdf_path.is_file():
        return PDFVerification(
            path=str(pdf_path),
            status="failed_after_retry",
            valid_pdf=False,
            error="PDF 文件不存在",
        )
    try:
        file_size = pdf_path.stat().st_size
        with pdf_path.open("rb") as handle:
            signature = handle.read(5)
    except OSError as error:
        return PDFVerification(
            path=str(pdf_path),
            status="failed_after_retry",
            valid_pdf=False,
            error=_sanitize_message(error),
        )
    if signature != b"%PDF-":
        return PDFVerification(
            path=str(pdf_path),
            status="pdf_corrupt",
            valid_pdf=False,
            file_size=file_size,
            error="文件不是有效 PDF（缺少 %PDF- 签名）",
        )
    if file_size < minimum_bytes:
        return PDFVerification(
            path=str(pdf_path),
            status="pdf_too_short",
            valid_pdf=False,
            file_size=file_size,
            error="PDF 文件尺寸异常，可能是截断响应",
        )
    if file_size > maximum_bytes:
        return PDFVerification(
            path=str(pdf_path),
            status="pdf_too_large",
            valid_pdf=False,
            file_size=file_size,
            error="PDF 超过应用的安全解析上限",
        )

    try:
        from pypdf import PdfReader
    except ImportError:
        return PDFVerification(
            path=str(pdf_path),
            status="failed_after_retry",
            valid_pdf=False,
            file_size=file_size,
            error="缺少 pypdf，无法验证 PDF 页数",
        )

    try:
        reader = PdfReader(str(pdf_path), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            return PDFVerification(
                path=str(pdf_path),
                status="do_not_auto_retry",
                valid_pdf=False,
                file_size=file_size,
                error="PDF 已加密，需要用户提供可读取版本",
            )
        page_count = len(reader.pages)
    except Exception as error:  # pypdf exposes several backend-specific exceptions
        return PDFVerification(
            path=str(pdf_path),
            status="pdf_corrupt",
            valid_pdf=False,
            file_size=file_size,
            error=f"PDF 结构无法解析：{_sanitize_message(error)}",
        )
    if page_count <= 0:
        return PDFVerification(
            path=str(pdf_path),
            status="pdf_corrupt",
            valid_pdf=False,
            file_size=file_size,
            error="PDF 不包含可读取页面",
        )

    texts: list[str] = []
    warnings: list[str] = []
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as document:
            texts = [_clean_page_text(page.extract_text() or "") for page in document.pages]
    except Exception as error:  # fall back to pypdf's text layer
        warnings.append(f"pdfplumber 提取失败，已使用 pypdf 回退：{_sanitize_message(error)}")
        try:
            texts = [_clean_page_text(page.extract_text() or "") for page in reader.pages]
        except Exception as fallback_error:
            warnings.append(f"文本层提取失败：{_sanitize_message(fallback_error)}")
            texts = [""] * page_count
    if len(texts) < page_count:
        texts.extend([""] * (page_count - len(texts)))
    elif len(texts) > page_count:
        texts = texts[:page_count]

    pages = [ExtractedPage(index, text) for index, text in enumerate(texts, 1)]
    extracted_characters = sum(len(re.sub(r"\s+", "", text)) for text in texts)
    pages_with_text = sum(bool(re.search(r"\w", text)) for text in texts)
    minimum_text = max(200, page_count * 80)
    ocr_needed = extracted_characters < minimum_text or pages_with_text / page_count < 0.5
    if ocr_needed:
        warnings.append("PDF 有效，但文本层不足；需 OCR 或人工补充后才能作为全文证据精读")

    title_match: bool | None = None
    normalized_expected = _normalized_title(expected_title)
    if normalized_expected:
        opening = _normalized_title(" ".join(texts[: min(3, len(texts))]))
        title_match = normalized_expected in opening if opening else False
        if not title_match:
            words = [part for part in re.split(r"\W+", expected_title.casefold()) if len(part) >= 4]
            title_match = bool(words) and sum(word in " ".join(texts[:3]).casefold() for word in words) >= min(3, len(words))
        if not title_match:
            warnings.append("首页文本未匹配预期题名，请人工确认文件与文献记录是否一致")

    return PDFVerification(
        path=str(pdf_path),
        status="downloaded",
        valid_pdf=True,
        page_count=page_count,
        file_size=file_size,
        extracted_characters=extracted_characters,
        pages_with_text=pages_with_text,
        ocr_needed=ocr_needed,
        title_match=title_match,
        pages=pages,
        warnings=warnings,
    )


def classify_evidence(
    *,
    verification: PDFVerification | None = None,
    abstract: str = "",
    knowledge_snippet: str = "",
) -> EvidenceLevel:
    """Return the strongest evidence that is currently usable by the app."""

    if verification and verification.extractable_full_text:
        return EvidenceLevel.FULL_TEXT
    if str(abstract or "").strip():
        return EvidenceLevel.ABSTRACT_ONLY
    if str(knowledge_snippet or "").strip():
        return EvidenceLevel.KNOWLEDGE_SNIPPET
    return EvidenceLevel.METADATA_ONLY


def apply_fulltext_result(paper: PaperRecord, result: DownloadItemResult) -> PaperRecord:
    """Update one record's public evidence state and app-owned local path."""

    paper.access_status = result.status
    paper.evidence_level = result.evidence_level
    if result.file:
        paper.extra["local_file"] = result.file
    if result.verification:
        paper.extra["pdf_verification"] = result.verification.to_dict()
        paper.extra["ocr_needed"] = result.verification.ocr_needed
    return paper


def _safe_environment(profile_directory: Path) -> dict[str, str]:
    allowed = {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["LIT_DL_CONFIG_DIR"] = str(profile_directory)
    return environment


class FullTextDownloader:
    """Run explicit small-batch retrievals through nature-downloader."""

    def __init__(
        self,
        project_directory: Path,
        settings: LibrarySettings,
        *,
        skill_root: Path = SKILL_ROOT,
        node_path: Path | None = None,
        timeout_seconds: int = 600,
    ) -> None:
        self.project_directory = Path(project_directory).resolve()
        self.settings = settings
        self.skill_root = Path(skill_root).resolve()
        self.node_path = Path(node_path).resolve() if node_path else find_node()
        self.timeout_seconds = max(10, int(timeout_seconds))
        configured_limit = int(settings.max_batch_size or DEFAULT_MAX_BATCH)
        self.max_batch_size = min(max(configured_limit, 1), HARD_MAX_BATCH)
        self.output_directory = self.project_directory / "fulltext"
        self.profile_directory = self.output_directory / "downloader_profile"

    def prepare(self, *, require_library_profile: bool = True) -> Path | None:
        script = self.skill_root / "scripts" / "batch_download.mjs"
        if not script.is_file():
            raise FullTextError("项目内未找到 nature-downloader 批处理脚本")
        if not self.node_path or not self.node_path.is_file():
            raise FullTextError("未找到可用的 Node.js 运行时")
        self.output_directory.mkdir(parents=True, exist_ok=True)
        (self.output_directory / "PDFs").mkdir(parents=True, exist_ok=True)
        if self.settings.portal_url.strip():
            return export_school_profile(self.settings, self.profile_directory)
        if require_library_profile:
            raise FullTextError("机构授权检索前，请先配置实际使用的图书馆电子资源入口 URL")
        self.profile_directory.mkdir(parents=True, exist_ok=True)
        return None

    def build_commands(
        self,
        requests: Sequence[DownloadRequest],
        *,
        include_supporting_information: bool = False,
    ) -> list[tuple[list[str], list[DownloadRequest]]]:
        validated = [request.validated() for request in requests]
        if not validated:
            raise ValueError("请至少选择一篇文献")
        if len(validated) > self.max_batch_size:
            raise ValueError(f"单批最多处理 {self.max_batch_size} 篇文献")
        if len(validated) > HARD_MAX_BATCH:
            raise ValueError(f"安全上限为 {HARD_MAX_BATCH} 篇文献")

        script = self.skill_root / "scripts" / "batch_download.mjs"
        base = [
            str(self.node_path or "node"),
            str(script),
            "--out",
            str(self.output_directory),
            "--proxy",
            _safe_public_url(self.settings.cdp_proxy_url),
        ]
        commands: list[tuple[list[str], list[DownloadRequest]]] = []
        doi_requests = [request for request in validated if request.doi]
        if doi_requests:
            command = [*base, "--dois", ",".join(item.doi for item in doi_requests)]
            if include_supporting_information:
                command.append("--si")
            commands.append((command, doi_requests))

        for request in validated:
            if request.doi:
                continue
            command = list(base)
            if request.pdf_url:
                command.extend(["--pdf-url", request.pdf_url])
            else:
                command.extend(["--title", request.title])
                if request.open_access:
                    command.append("--open-access")
                if self.settings.pdf_only and re.search(r"[\u3400-\u9fff]", request.title):
                    command.extend(["--cnki-format", "pdf"])
            commands.append((command, [request]))
        return commands

    def download(
        self,
        requests: Sequence[DownloadRequest],
        *,
        include_supporting_information: bool = False,
        abstracts: Mapping[str, str] | None = None,
        verify_downloaded_pdfs: bool = True,
    ) -> BatchDownloadResult:
        """Execute user-confirmed requests; never perform broad topic downloads."""

        validated_requests = [request.validated() for request in requests]
        requires_library = any(
            request.doi or (request.title and not request.open_access and not request.pdf_url)
            for request in validated_requests
        )
        self.prepare(require_library_profile=requires_library)
        commands = self.build_commands(
            validated_requests,
            include_supporting_information=include_supporting_information,
        )
        abstract_lookup = dict(abstracts or {})
        all_results: list[DownloadItemResult] = []
        elapsed = 0.0
        for command, associated in commands:
            payload = self._run_command(command)
            summary = payload.get("summary", {})
            try:
                elapsed += float(summary.get("seconds") or 0)
            except (TypeError, ValueError):
                pass
            raw_results = [item for item in payload.get("results", []) if isinstance(item, dict)]
            all_results.extend(
                self._normalize_results(
                    associated,
                    raw_results,
                    abstract_lookup=abstract_lookup,
                    verify_downloaded_pdfs=verify_downloaded_pdfs,
                )
            )
        remaining = list(all_results)
        ordered_results: list[DownloadItemResult] = []
        for request in validated_requests:
            match_index = next(
                (
                    index
                    for index, result in enumerate(remaining)
                    if (request.paper_id and result.paper_id == request.paper_id)
                    or (request.doi and result.doi == request.doi)
                    or (
                        not request.paper_id
                        and not request.doi
                        and result.title == request.title
                    )
                ),
                None,
            )
            if match_index is not None:
                ordered_results.append(remaining.pop(match_index))
        ordered_results.extend(remaining)
        return BatchDownloadResult(
            total=len(ordered_results),
            downloaded=sum(item.status in SUCCESS_STATUSES for item in ordered_results),
            seconds=round(elapsed, 3),
            results=ordered_results,
        )

    def _run_command(self, command: Sequence[str]) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                env=_safe_environment(self.profile_directory),
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as error:
            raise FullTextError("全文获取超时；未自动重试，以避免重复触发出版商检查") from error
        except OSError as error:
            raise FullTextError(f"全文获取脚本无法运行：{_sanitize_message(error)}") from error
        if completed.returncode != 0:
            message = _sanitize_message(completed.stderr or completed.stdout or "全文获取失败")
            raise FullTextError(message)
        return parse_download_output(completed.stdout)

    def _normalize_results(
        self,
        requests: Sequence[DownloadRequest],
        raw_results: Sequence[Mapping[str, Any]],
        *,
        abstract_lookup: Mapping[str, str],
        verify_downloaded_pdfs: bool,
    ) -> list[DownloadItemResult]:
        raw_by_doi = {normalize_doi(item.get("doi")): item for item in raw_results if item.get("doi")}
        results: list[DownloadItemResult] = []
        for index, request in enumerate(requests):
            raw = raw_by_doi.get(request.doi) if request.doi else None
            if raw is None and len(requests) == 1 and raw_results:
                raw = raw_results[0]
            if raw is None and index < len(raw_results):
                raw = raw_results[index]
            raw = raw or {}
            raw_status = str(raw.get("status") or "")
            status = canonical_status(raw_status)
            reason = _sanitize_message(raw.get("reason") or raw.get("err") or "")
            if raw_status and status == "failed_after_retry" and raw_status not in CANONICAL_DOWNLOAD_STATUSES:
                reason = f"未知下载状态 {raw_status!r}；已按失败处理。{reason}".strip()
            file_path = self._safe_result_file(raw.get("file"))
            verification: PDFVerification | None = None
            if status in {"downloaded", "downloaded_with_si", "open_access_downloaded"}:
                if not file_path:
                    status = "pdf_fetch_failed"
                    reason = reason or "下载器报告成功，但未返回项目目录内的 PDF 文件"
                elif verify_downloaded_pdfs:
                    verification = verify_pdf(Path(file_path), expected_title=request.title)
                    if not verification.valid_pdf:
                        status = verification.status
                        reason = verification.error
                    elif verification.ocr_needed:
                        reason = "PDF 已下载并验证，但文本层不足，需 OCR/人工补充后再精读"
                    elif verification.title_match is False:
                        reason = "PDF 已下载且结构有效，但题名未匹配；人工确认前不作为全文证据"
            abstract = abstract_lookup.get(request.paper_id) or abstract_lookup.get(request.doi) or ""
            evidence_level = classify_evidence(verification=verification, abstract=abstract)
            results.append(
                DownloadItemResult(
                    paper_id=request.paper_id,
                    doi=request.doi,
                    title=request.title or str(raw.get("title") or ""),
                    status=status,
                    url=_safe_result_url(raw.get("via") or raw.get("url") or request.pdf_url),
                    file=file_path,
                    bytes=_safe_int(raw.get("bytes")),
                    reason=reason,
                    evidence_level=evidence_level,
                    verification=verification,
                )
            )
        return results

    def _safe_result_file(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = self.output_directory / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.output_directory.resolve())
        except ValueError:
            return ""
        return str(candidate)


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_result_url(value: Any) -> str:
    try:
        return _safe_public_url(str(value or ""))
    except ValueError:
        return ""


def requests_from_papers(papers: Iterable[PaperRecord]) -> list[DownloadRequest]:
    """Build explicit requests, preferring DOI and then the exact title."""

    return [DownloadRequest.from_paper(paper) for paper in papers]
