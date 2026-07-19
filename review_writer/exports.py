"""Reusable report and reference exports with evidence-gate enforcement."""

from __future__ import annotations

from datetime import datetime
from html import escape
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

from .provenance import DeliveryPolicy, evaluate_delivery_gate
from .workflow_models import PaperRecord


def render_ris(papers: Iterable[PaperRecord]) -> str:
    lines: list[str] = []
    for paper in papers:
        lines.append("TY  - JOUR" if paper.journal else "TY  - GEN")
        lines.append(f"ID  - {paper.record_id}")
        if paper.title:
            lines.append(f"TI  - {paper.title}")
        for author in paper.authors:
            lines.append(f"AU  - {author}")
        if paper.year:
            lines.append(f"PY  - {paper.year}")
        if paper.journal:
            lines.append(f"JO  - {paper.journal}")
        if paper.doi:
            lines.append(f"DO  - {paper.doi}")
        if paper.url:
            lines.append(f"UR  - {paper.url}")
        if paper.abstract:
            lines.append(f"AB  - {paper.abstract.replace(chr(10), ' ')}")
        lines.append(f"N1  - Review Writer evidence level: {paper.evidence_level.value}")
        lines.extend(("ER  -", ""))
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _markdown_paragraphs(markdown: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            result.append((f"Heading{min(len(heading.group(1)), 3)}", heading.group(2)))
        elif line.startswith("- "):
            result.append(("ListParagraph", "• " + line[2:]))
        elif line.startswith("|"):
            result.append(("Normal", line))
        else:
            result.append(("Normal", re.sub(r"[*_`]", "", line)))
    return result


def write_docx(markdown: str, path: Path, *, title: str = "文献调研报告") -> Path:
    """Write a dependency-free, standards-compliant basic DOCX."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    paragraphs: list[str] = []
    for style, text in _markdown_paragraphs(markdown):
        paragraphs.append(
            '<w:p><w:pPr><w:pStyle w:val="%s"/></w:pPr><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (style, escape(text))
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + "".join(paragraphs) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>'
    )
    content_types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>'
    rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>'
    document_rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Aptos" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="180"/></w:pPr><w:rPr><w:b/><w:sz w:val="34"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="280" w:after="140"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="420" w:hanging="210"/></w:pPr></w:style>
</w:styles>'''
    core = f'<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"><dc:title>{escape(title)}</dc:title><dc:creator>Review Writer</dc:creator><dcterms:created>{datetime.now().astimezone().isoformat()}</dcterms:created></cp:coreProperties>'
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("docProps/core.xml", core)
    return path


def write_pdf(markdown: str, path: Path, *, title: str = "文献调研报告", browser_executable: str = "") -> Path:
    """Render UTF-8/CJK-safe PDF through an installed Chromium/Edge browser."""

    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    candidates = [
        browser_executable,
        shutil.which("msedge") or "",
        shutil.which("chrome") or "",
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ]
    executable = next((value for value in candidates if value and Path(value).is_file()), "")
    if not executable:
        raise RuntimeError("未找到 Edge/Chrome PDF 渲染后端；Markdown、DOCX 和 RIS 仍可导出。")
    body = []
    for style, text in _markdown_paragraphs(markdown):
        tag = {"Heading1": "h1", "Heading2": "h2", "Heading3": "h3"}.get(style, "p")
        body.append(f"<{tag}>{escape(text)}</{tag}>")
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>" + escape(title) + "</title>"
        "<style>@page{size:A4;margin:18mm}body{font-family:'Microsoft YaHei','Noto Sans CJK SC',sans-serif;line-height:1.6;color:#172033}h1{font-size:24px}h2{font-size:18px;margin-top:1.4em}p{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
        "</head><body>" + "".join(body) + "</body></html>"
    )
    with tempfile.TemporaryDirectory(prefix="review-writer-export-") as directory:
        html_path = Path(directory) / "report.html"
        html_path.write_text(html, encoding="utf-8")
        completed = subprocess.run(
            [executable, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={path}", html_path.as_uri()],
            capture_output=True, text=True, timeout=120, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not path.is_file() or path.stat().st_size < 100:
            raise RuntimeError((completed.stderr or completed.stdout or "PDF 渲染失败")[:1000])
    return path


def export_markdown_document(
    markdown: str,
    path: Path,
    *,
    format: str,
    title: str = "文献调研报告",
) -> Path:
    """Export Markdown content without applying an evidence delivery gate.

    This is the common renderer for standalone artifacts such as verification
    reports.  Delivery-gated literature reports must continue to use
    :func:`export_verified_report`.
    """

    output_format = format.casefold().strip()
    path = Path(path)
    if output_format in {"markdown", "md"}:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        return path
    if output_format == "docx":
        return write_docx(markdown, path, title=title)
    if output_format == "pdf":
        return write_pdf(markdown, path, title=title)
    raise ValueError(f"当前无依赖导出器不支持格式：{format}")


def export_verification_report(
    markdown: str,
    path: Path,
    *,
    format: str,
    title: str = "文献核验报告",
) -> Path:
    """Export a standalone verification report without the summary gate.

    A verification report describes the gate outcome itself, so subjecting it
    to that same gate would prevent users from exporting failure details.
    """

    return export_markdown_document(markdown, path, format=format, title=title)


def export_verified_report(
    markdown: str,
    audit_items: Iterable[Any],
    path: Path,
    *,
    format: str,
    policy: DeliveryPolicy | str = DeliveryPolicy.STRICT,
) -> Path:
    gate = evaluate_delivery_gate(audit_items, policy=policy)
    if not gate.allowed:
        raise ValueError(gate.message)
    warning = ""
    if gate.blocking_claim_ids or gate.warning_claim_ids:
        warning = f"> ⚠ {gate.message}\n\n"
    content = warning + markdown
    format = format.casefold()
    path = Path(path)
    if format == "markdown" or format == "md":
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return path
    if format == "docx":
        return write_docx(content, path)
    if format == "pdf":
        return write_pdf(content, path)
    raise ValueError(f"当前无依赖导出器不支持格式：{format}")
