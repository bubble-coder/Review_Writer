"""Document asset registry for PDF, HTML, supplements, and optional OCR."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
import mimetypes
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable
from uuid import uuid4

from .fulltext import verify_pdf
from .workflow_models import EvidenceBlock, EvidenceLevel, PaperRecord


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class DocumentAsset:
    paper_id: str
    path: str
    media_type: str
    role: str = "main"
    asset_id: str = field(default_factory=lambda: f"asset-{uuid4().hex}")
    checksum_sha256: str = ""
    byte_size: int = 0
    parser: str = ""
    parser_version: str = ""
    extraction_status: str = "pending"
    verification_status: str = "unverified"
    ocr_status: str = "not_needed"
    warnings: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    extracted_at: str = ""
    page_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DocumentIngestResult:
    asset: DocumentAsset
    evidence_blocks: list[EvidenceBlock]
    extracted_text: str = ""


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "noscript"}:
            self._ignored += 1
        elif tag in {"p", "div", "section", "article", "h1", "h2", "h3", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)


def _html_text(raw: str) -> str:
    parser = _VisibleHTML()
    parser.feed(raw)
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _text_blocks(paper_id: str, asset_id: str, text: str, *, level: EvidenceLevel, prefix: str) -> list[EvidenceBlock]:
    paragraphs = [value.strip() for value in re.split(r"\n\s*\n|(?<=\.)\s+(?=[A-Z])", text) if value.strip()]
    blocks: list[EvidenceBlock] = []
    for index, paragraph in enumerate(paragraphs, 1):
        if len(paragraph) < 30:
            continue
        figure_match = re.search(r"(?:\bFig(?:ure)?\.?|图)[ \u00a0]*(S?\d+[A-Za-z]?)", paragraph, flags=re.I)
        table_match = re.search(r"(?:\bTable|表)[ \u00a0]*(S?\d+[A-Za-z]?)", paragraph, flags=re.I)
        first_line = paragraph.splitlines()[0].strip()
        section = first_line if len(first_line) <= 100 and not re.search(r"[。.!?]$", first_line) else ""
        blocks.append(
            EvidenceBlock(
                paper_id=paper_id,
                text=paragraph[:4000],
                evidence_level=level,
                locator=f"{prefix} paragraph {index}",
                asset_id=asset_id,
                paragraph_number=index,
                section=section,
                figure=(f"Figure {figure_match.group(1)}" if figure_match else ""),
                table=(f"Table {table_match.group(1)}" if table_match else ""),
                page_number=(int(match.group(1)) if (match := re.match(r"page (\d+)", prefix)) else None),
                extraction_method="local-parser",
                verification_status="verified",
                source_hash=sha256(paragraph[:4000].encode("utf-8")).hexdigest(),
            )
        )
    return blocks


class DocumentIngestor:
    def ingest(self, paper: PaperRecord, path: Path, *, role: str = "main") -> DocumentIngestResult:
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        asset = DocumentAsset(
            paper_id=paper.record_id, path=str(path), media_type=media_type, role=role,
            checksum_sha256=file_sha256(path), byte_size=path.stat().st_size,
        )
        if path.suffix.casefold() == ".pdf":
            return self._pdf(paper, path, asset)
        if path.suffix.casefold() in {".html", ".htm"}:
            return self._html(paper, path, asset)
        if path.suffix.casefold() in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            asset.parser = "plain-text"
            asset.extraction_status = "success"
            asset.verification_status = "manual_needed" if role == "main" else "verified_supplement"
            asset.extracted_at = _now_iso()
            level = EvidenceLevel.FULL_TEXT if role == "main" and paper.evidence_level is EvidenceLevel.FULL_TEXT else paper.evidence_level
            return DocumentIngestResult(asset, _text_blocks(paper.record_id, asset.asset_id, text, level=level, prefix=role), text)
        asset.extraction_status = "unsupported"
        asset.verification_status = "manual_needed"
        asset.warnings.append("不支持的文档格式；需人工核对，未创建证据块。")
        return DocumentIngestResult(asset, [])

    def _pdf(self, paper: PaperRecord, path: Path, asset: DocumentAsset) -> DocumentIngestResult:
        verification = verify_pdf(path, expected_title=paper.title)
        asset.parser = "pdfplumber+pypdf"
        asset.page_count = verification.page_count
        asset.extracted_at = _now_iso()
        if not verification.valid_pdf:
            asset.extraction_status = "failed"
            asset.verification_status = "rejected"
            asset.warnings.extend(verification.warnings or [verification.error])
            return DocumentIngestResult(asset, [])
        if verification.ocr_needed:
            asset.extraction_status = "needs_ocr"
            asset.verification_status = "manual_needed"
            asset.ocr_status = "needed"
            asset.warnings.append("扫描版或文本层不足；OCR/人工核对完成前不得作为全文证据。")
            return DocumentIngestResult(asset, [])
        if verification.title_match is False:
            asset.extraction_status = "success"
            asset.verification_status = "manual_needed"
            asset.warnings.append("PDF 标题与论文题名不匹配；人工确认前不得作为全文证据。")
            return DocumentIngestResult(asset, [])
        asset.extraction_status = "success"
        asset.verification_status = "verified"
        asset.ocr_status = "not_needed"
        blocks: list[EvidenceBlock] = []
        text_parts: list[str] = []
        for page in verification.pages:
            text_parts.append(page.text)
            blocks.extend(_text_blocks(paper.record_id, asset.asset_id, page.text, level=EvidenceLevel.FULL_TEXT, prefix=f"page {page.page_number}"))
        return DocumentIngestResult(asset, blocks, "\n\n".join(text_parts))

    def _html(self, paper: PaperRecord, path: Path, asset: DocumentAsset) -> DocumentIngestResult:
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = _html_text(raw)
        asset.parser = "stdlib-html.parser"
        asset.extracted_at = _now_iso()
        if len(text) < 500:
            asset.extraction_status = "failed"
            asset.verification_status = "manual_needed"
            asset.warnings.append("HTML 正文过短或解析失败；需人工核对。")
            return DocumentIngestResult(asset, [], text)
        title_tokens = {value for value in re.findall(r"\w+", paper.title.casefold()) if len(value) > 2}
        head_tokens = set(re.findall(r"\w+", text[:3000].casefold()))
        title_match = bool(title_tokens and len(title_tokens & head_tokens) / len(title_tokens) >= 0.4)
        asset.extraction_status = "success"
        asset.verification_status = "verified" if title_match else "manual_needed"
        if not title_match:
            asset.warnings.append("HTML 页面题名匹配不足；人工确认前不得作为全文证据。")
            return DocumentIngestResult(asset, [], text)
        return DocumentIngestResult(asset, _text_blocks(paper.record_id, asset.asset_id, text, level=EvidenceLevel.FULL_TEXT, prefix="HTML"), text)


class OCRRunner:
    """Explicit optional OCR command; never silently promotes OCR output."""

    def __init__(self, executable: str = "ocrmypdf") -> None:
        self.executable = executable

    def run(self, source: Path, target: Path, *, timeout: int = 900) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        command = [self.executable, "--skip-text", "--deskew", str(source), str(target)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "OCR 失败")[:1000])
        return target
