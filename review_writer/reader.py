"""Source-grounded structured reading for core papers.

The deterministic path extracts only statements that occur in supplied PDF
pages, abstracts, or knowledge-base snippets.  The optional LLM path is a
separate, explicit call and receives only caller-provided evidence blocks; its
JSON response must cite valid block IDs for every populated field.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .fulltext import ExtractedPage, PDFVerification, classify_evidence, verify_pdf
from .generators.llm_client import LLMClient, LLMRequestError
from .workflow_models import EvidenceBlock, EvidenceLevel, PaperRecord, ReadingNote


EVIDENCE_LABELS = {
    EvidenceLevel.FULL_TEXT: "全文证据",
    EvidenceLevel.ABSTRACT_ONLY: "仅摘要证据",
    EvidenceLevel.KNOWLEDGE_SNIPPET: "知识库片段证据",
    EvidenceLevel.METADATA_ONLY: "仅元数据",
}

_FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "research_question": (
        "objective",
        "aim",
        "purpose",
        "hypothesis",
        "research question",
        "we investigate",
        "we examined",
        "本研究旨在",
        "研究目的",
        "研究问题",
        "探讨",
        "考察",
    ),
    "study_design": (
        "randomized",
        "randomised",
        "controlled trial",
        "cohort",
        "case-control",
        "cross-sectional",
        "longitudinal",
        "systematic review",
        "meta-analysis",
        "experiment",
        "simulation",
        "qualitative",
        "retrospective",
        "prospective",
        "随机",
        "队列",
        "病例对照",
        "横断面",
        "纵向",
        "系统综述",
        "荟萃分析",
        "实验设计",
        "回顾性",
        "前瞻性",
    ),
    "population_or_data": (
        "participants",
        "patients",
        "subjects",
        "sample",
        "dataset",
        "database",
        "corpus",
        "observations",
        "respondents",
        "n =",
        "n=",
        "参与者",
        "患者",
        "受试者",
        "样本",
        "数据集",
        "数据库",
        "语料库",
        "观测",
    ),
    "methods": (
        "method",
        "model",
        "algorithm",
        "analysis",
        "regression",
        "assay",
        "sequencing",
        "measurement",
        "intervention",
        "protocol",
        "方法",
        "模型",
        "算法",
        "分析",
        "回归",
        "测序",
        "测量",
        "干预",
        "流程",
    ),
    "findings": (
        "result",
        "found that",
        "we found",
        "showed",
        "demonstrated",
        "associated with",
        "significantly",
        "conclude",
        "suggest that",
        "结果",
        "发现",
        "表明",
        "显示",
        "显著",
        "相关",
        "结论",
    ),
    "limitations": (
        "limitation",
        "limited by",
        "caution",
        "cannot rule out",
        "future work",
        "further research",
        "bias",
        "uncertainty",
        "局限",
        "限制",
        "偏倚",
        "不确定",
        "未来研究",
        "尚需",
    ),
}

_UNAVAILABLE_MARKERS = (
    "未提供",
    "未识别",
    "未定位",
    "需人工复核",
    "not provided",
    "not identified",
    "not available",
)


def _paper_file(paper: PaperRecord) -> str:
    direct = getattr(paper, "local_file", "")
    if direct:
        return str(direct)
    return str((paper.extra or {}).get("local_file") or "")


def _coerce_pages(pages: Iterable[ExtractedPage | Mapping[str, Any] | tuple[int, str]]) -> list[ExtractedPage]:
    result: list[ExtractedPage] = []
    for fallback_index, page in enumerate(pages, 1):
        if isinstance(page, ExtractedPage):
            number, text = page.page_number, page.text
        elif isinstance(page, Mapping):
            number = page.get("page_number", page.get("page", fallback_index))
            text = page.get("text", "")
        else:
            try:
                number, text = page
            except (TypeError, ValueError) as error:
                raise ValueError("页面必须包含 page_number 与 text") from error
        try:
            page_number = max(1, int(number))
        except (TypeError, ValueError):
            page_number = fallback_index
        result.append(ExtractedPage(page_number, str(text or "").strip()))
    return result


def _split_source_blocks(text: str, *, maximum_characters: int = 1800) -> list[str]:
    """Split source text without rewriting it or changing statement order."""

    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
    result: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= maximum_characters:
            result.append(paragraph)
            continue
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[。！？.!?])\s+|(?<=[。！？])", paragraph)
            if part.strip()
        ]
        current = ""
        for sentence in sentences or [paragraph]:
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > maximum_characters:
                result.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            result.append(current)
    return result


def build_page_evidence_blocks(
    paper_id: str,
    pages: Iterable[ExtractedPage | Mapping[str, Any] | tuple[int, str]],
) -> list[EvidenceBlock]:
    """Build stable reading-order IDs and page anchors for extracted PDF text."""

    blocks: list[EvidenceBlock] = []
    for page in _coerce_pages(pages):
        for paragraph in _split_source_blocks(page.text):
            local_id = f"S{len(blocks) + 1:04d}"
            block_id = f"{paper_id}:{local_id}"
            figure_match = re.search(r"(?:\bFig(?:ure)?\.?|图)[ \u00a0]*(S?\d+[A-Za-z]?)", paragraph, flags=re.I)
            table_match = re.search(r"(?:\bTable|表)[ \u00a0]*(S?\d+[A-Za-z]?)", paragraph, flags=re.I)
            blocks.append(
                EvidenceBlock(
                    paper_id=paper_id,
                    text=paragraph,
                    evidence_level=EvidenceLevel.FULL_TEXT,
                    block_id=block_id,
                    locator=f"p.{page.page_number}#{local_id}",
                    page_number=page.page_number,
                    paragraph_number=len(blocks) + 1,
                    figure=(f"Figure {figure_match.group(1)}" if figure_match else ""),
                    table=(f"Table {table_match.group(1)}" if table_match else ""),
                    extraction_method="verified-pdf-text-layer",
                    verification_status="verified",
                    source_hash=hashlib.sha256(paragraph.encode("utf-8")).hexdigest(),
                    note="从已验证 PDF 文本层提取；版式与图表仍需人工核对",
                )
            )
    return blocks


def build_text_evidence_blocks(
    paper_id: str,
    text: str,
    *,
    evidence_level: EvidenceLevel,
) -> list[EvidenceBlock]:
    """Build anchors for an abstract or a supplied knowledge-base snippet."""

    if evidence_level not in {EvidenceLevel.ABSTRACT_ONLY, EvidenceLevel.KNOWLEDGE_SNIPPET}:
        raise ValueError("文本证据块只接受摘要或知识库片段等级")
    source_name = "abstract" if evidence_level is EvidenceLevel.ABSTRACT_ONLY else "knowledge-snippet"
    prefix = "A" if evidence_level is EvidenceLevel.ABSTRACT_ONLY else "K"
    blocks: list[EvidenceBlock] = []
    for paragraph in _split_source_blocks(text):
        local_id = f"{prefix}{len(blocks) + 1:04d}"
        block_id = f"{paper_id}:{local_id}"
        blocks.append(
            EvidenceBlock(
                paper_id=paper_id,
                text=paragraph,
                evidence_level=evidence_level,
                block_id=block_id,
                locator=f"{source_name}#{local_id}",
                paragraph_number=len(blocks) + 1,
                extraction_method=source_name,
                verification_status="partial" if evidence_level is EvidenceLevel.ABSTRACT_ONLY else "manual_needed",
                source_hash=hashlib.sha256(paragraph.encode("utf-8")).hexdigest(),
                note=(
                    "仅摘要证据；不可据此推断摘要未呈现的全文细节"
                    if evidence_level is EvidenceLevel.ABSTRACT_ONLY
                    else "知识库片段证据；不能替代原始论文全文"
                ),
            )
        )
    return blocks


def _sentences(text: str) -> list[str]:
    values = re.split(r"(?<=[。！？.!?])\s+|(?<=[。！？])", text)
    return [value.strip() for value in values if value.strip()]


def _matching_excerpt(block: EvidenceBlock, keywords: Sequence[str], maximum: int = 600) -> str:
    lowered_keywords = tuple(item.casefold() for item in keywords)
    for sentence in _sentences(block.text):
        lowered = sentence.casefold()
        if any(keyword in lowered for keyword in lowered_keywords):
            excerpt = sentence
            break
    else:
        excerpt = block.text
    excerpt = re.sub(r"\s+", " ", excerpt).strip()
    if len(excerpt) > maximum:
        excerpt = excerpt[: maximum - 1].rstrip() + "…"
    return f"[{block.locator}] {excerpt}"


def _select_blocks(
    blocks: Sequence[EvidenceBlock],
    field_name: str,
    *,
    maximum: int,
) -> list[EvidenceBlock]:
    keywords = tuple(keyword.casefold() for keyword in _FIELD_KEYWORDS[field_name])
    matches = [block for block in blocks if any(keyword in block.text.casefold() for keyword in keywords)]
    selected = matches[:maximum]
    for block in selected:
        if field_name not in block.supports:
            block.supports.append(field_name)
    return selected


def _unavailable(field_label: str, evidence_level: EvidenceLevel) -> str:
    if evidence_level is EvidenceLevel.ABSTRACT_ONLY:
        return f"仅摘要证据：摘要未提供可确认的{field_label}。"
    if evidence_level is EvidenceLevel.KNOWLEDGE_SNIPPET:
        return f"知识库片段证据：片段未提供可确认的{field_label}。"
    if evidence_level is EvidenceLevel.METADATA_ONLY:
        return f"仅元数据：无法确认{field_label}。"
    return f"全文中未自动定位到可确认的{field_label}，需人工复核。"


def _tokens(value: str) -> set[str]:
    lowered = value.casefold()
    tokens = {token for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", lowered)}
    for run in re.findall(r"[\u3400-\u9fff]{2,}", lowered):
        tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _map_core_questions(
    blocks: Sequence[EvidenceBlock],
    core_questions: Sequence[str],
    warnings: list[str],
) -> list[str]:
    mappings: list[str] = []
    block_tokens = {block.block_id: _tokens(block.text) for block in blocks}
    for index, question in enumerate(core_questions, 1):
        question = str(question or "").strip()
        if not question:
            continue
        target = _tokens(question)
        minimum_overlap = 1 if len(target) <= 2 else 2
        scored: list[tuple[int, EvidenceBlock]] = []
        for block in blocks:
            overlap = len(target & block_tokens[block.block_id])
            if overlap >= minimum_overlap:
                scored.append((overlap, block))
        scored.sort(key=lambda item: (-item[0], item[1].block_id))
        matched = [block for _score, block in scored[:3]]
        qid = f"Q{index}"
        if not matched:
            warnings.append(f"{qid}: 未在已提供证据中定位到与“{question}”直接匹配的片段")
            continue
        for block in matched:
            if qid not in block.supports:
                block.supports.append(qid)
        locators = "、".join(block.locator for block in matched)
        mappings.append(f"{qid}: {question} — 相关证据：{locators}")
    return mappings


def _build_deterministic_note(
    paper: PaperRecord,
    blocks: list[EvidenceBlock],
    *,
    evidence_level: EvidenceLevel,
    core_questions: Sequence[str],
    warnings: list[str],
) -> ReadingNote:
    question_blocks = _select_blocks(blocks, "research_question", maximum=1)
    design_blocks = _select_blocks(blocks, "study_design", maximum=1)
    data_blocks = _select_blocks(blocks, "population_or_data", maximum=1)
    method_blocks = _select_blocks(blocks, "methods", maximum=2)
    finding_blocks = _select_blocks(blocks, "findings", maximum=5)
    limitation_blocks = _select_blocks(blocks, "limitations", maximum=3)

    def one(selected: Sequence[EvidenceBlock], field: str, label: str) -> str:
        return (
            _matching_excerpt(selected[0], _FIELD_KEYWORDS[field])
            if selected
            else _unavailable(label, evidence_level)
        )

    findings = [
        _matching_excerpt(block, _FIELD_KEYWORDS["findings"])
        for block in finding_blocks
    ] or [_unavailable("主要发现", evidence_level)]
    limitations = [
        _matching_excerpt(block, _FIELD_KEYWORDS["limitations"])
        for block in limitation_blocks
    ] or [_unavailable("局限性", evidence_level)]
    related = _map_core_questions(blocks, core_questions, warnings)

    return ReadingNote(
        paper_id=paper.record_id,
        research_question=one(question_blocks, "research_question", "研究问题"),
        study_design=one(design_blocks, "study_design", "研究设计"),
        population_or_data=one(data_blocks, "population_or_data", "研究对象或数据"),
        methods=(
            "\n".join(_matching_excerpt(block, _FIELD_KEYWORDS["methods"]) for block in method_blocks)
            if method_blocks
            else _unavailable("研究方法", evidence_level)
        ),
        findings=findings,
        limitations=limitations,
        related_core_questions=related,
        evidence_blocks=blocks,
        evidence_level=evidence_level,
        warnings=warnings,
    )


def read_paper_deterministically(
    paper: PaperRecord,
    *,
    pages: Iterable[ExtractedPage | Mapping[str, Any] | tuple[int, str]] | None = None,
    verification: PDFVerification | None = None,
    core_questions: Sequence[str] = (),
) -> ReadingNote:
    """Create a structured reading card without model calls or inference.

    If a verified, extractable PDF is unavailable, this function automatically
    degrades to the supplied abstract/snippet and marks that evidence boundary.
    """

    warnings: list[str] = []
    supplied_pages = _coerce_pages(pages) if pages is not None else []
    local_verification = verification
    if pages is None and local_verification is None:
        local_file = _paper_file(paper)
        if local_file:
            local_path = Path(local_file)
            if local_path.suffix.casefold() != ".pdf":
                from .document_ingest import DocumentIngestor

                ingest = DocumentIngestor().ingest(paper, local_path)
                if ingest.asset.verification_status == "verified" and ingest.evidence_blocks:
                    return _build_deterministic_note(
                        paper,
                        ingest.evidence_blocks,
                        evidence_level=EvidenceLevel.FULL_TEXT,
                        core_questions=core_questions,
                        warnings=list(ingest.asset.warnings),
                    )
                warnings.extend(ingest.asset.warnings)
            else:
                local_verification = verify_pdf(local_path, expected_title=paper.title)

    if supplied_pages:
        blocks = build_page_evidence_blocks(paper.record_id, supplied_pages)
        if blocks:
            return _build_deterministic_note(
                paper,
                blocks,
                evidence_level=EvidenceLevel.FULL_TEXT,
                core_questions=core_questions,
                warnings=warnings,
            )
    if local_verification and local_verification.extractable_full_text:
        blocks = build_page_evidence_blocks(paper.record_id, local_verification.pages)
        if blocks:
            warnings.extend(local_verification.warnings)
            return _build_deterministic_note(
                paper,
                blocks,
                evidence_level=EvidenceLevel.FULL_TEXT,
                core_questions=core_questions,
                warnings=warnings,
            )
    if local_verification:
        warnings.extend(local_verification.warnings)
        if local_verification.ocr_needed:
            warnings.append("已发现扫描版或无可靠文本层 PDF：需 OCR/人工补充；本次未把它当作全文证据")
        elif not local_verification.valid_pdf:
            warnings.append(f"本地 PDF 未通过验证：{local_verification.error}")

    source_text = str(paper.abstract or "").strip()
    if source_text and paper.evidence_level is EvidenceLevel.KNOWLEDGE_SNIPPET:
        level = EvidenceLevel.KNOWLEDGE_SNIPPET
    elif source_text:
        level = EvidenceLevel.ABSTRACT_ONLY
    else:
        level = classify_evidence(verification=local_verification)
    if level is EvidenceLevel.ABSTRACT_ONLY:
        warnings.append("仅摘要证据：未获得或未验证全文；不得推断摘要未呈现的方法、结果或局限细节")
        blocks = build_text_evidence_blocks(
            paper.record_id,
            source_text,
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        )
    elif level is EvidenceLevel.KNOWLEDGE_SNIPPET:
        warnings.append("知识库片段证据：该片段不能替代原始论文全文")
        blocks = build_text_evidence_blocks(
            paper.record_id,
            source_text,
            evidence_level=EvidenceLevel.KNOWLEDGE_SNIPPET,
        )
    else:
        warnings.append("仅元数据：没有可用于结构化精读的正文、摘要或知识库片段")
        blocks = []
    return _build_deterministic_note(
        paper,
        blocks,
        evidence_level=level,
        core_questions=core_questions,
        warnings=warnings,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:] if lines else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        raise LLMRequestError("结构化精读模型没有返回单一 JSON 对象")
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise LLMRequestError(f"结构化精读 JSON 无效：{error.msg}") from error
    if not isinstance(value, dict):
        raise LLMRequestError("结构化精读结果必须是 JSON 对象")
    return value


def _validate_evidence_ids(value: Any, valid_ids: set[str], field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LLMRequestError(f"字段 {field_name}.evidence_ids 必须是字符串数组")
    ids = [item.strip() for item in value if item.strip()]
    unknown = [item for item in ids if item not in valid_ids]
    if unknown:
        raise LLMRequestError(f"字段 {field_name} 引用了不存在的证据块：{', '.join(unknown)}")
    return ids


def _validate_statement(
    value: Any,
    *,
    field_name: str,
    valid_ids: set[str],
    blocks_by_id: Mapping[str, EvidenceBlock],
) -> tuple[str, list[str]]:
    if not isinstance(value, Mapping):
        raise LLMRequestError(f"字段 {field_name} 必须包含 text 与 evidence_ids")
    text = str(value.get("text") or "").strip()
    if not text:
        raise LLMRequestError(f"字段 {field_name}.text 不能为空")
    ids = _validate_evidence_ids(value.get("evidence_ids"), valid_ids, field_name)
    unavailable = any(marker in text.casefold() for marker in _UNAVAILABLE_MARKERS)
    if not unavailable and not ids:
        raise LLMRequestError(f"字段 {field_name} 的实质性内容缺少证据块引用")
    if ids and not unavailable:
        evidence_tokens: set[str] = set()
        for block_id in ids:
            evidence_tokens.update(_tokens(blocks_by_id[block_id].text))
        statement_tokens = _tokens(text)
        if statement_tokens and evidence_tokens and not statement_tokens & evidence_tokens:
            raise LLMRequestError(f"字段 {field_name} 与其引用证据缺少基本词汇重合，需人工复核")
    return text, ids


def _strongest_level(blocks: Sequence[EvidenceBlock]) -> EvidenceLevel:
    if not blocks:
        return EvidenceLevel.METADATA_ONLY
    return max((EvidenceLevel.parse(block.evidence_level) for block in blocks), key=lambda level: level.rank)


def read_paper_with_llm(
    paper: PaperRecord,
    *,
    evidence_blocks: Sequence[EvidenceBlock],
    client: LLMClient,
    core_questions: Sequence[str] = (),
    maximum_evidence_characters: int = 180_000,
) -> ReadingNote:
    """Explicitly call an LLM using only the evidence blocks supplied here."""

    blocks = list(evidence_blocks)
    if not blocks:
        raise ValueError("调用大模型精读前必须显式提供至少一个证据块")
    if any(block.paper_id != paper.record_id for block in blocks):
        raise ValueError("证据块与目标论文的 paper_id 不一致")
    total_characters = sum(len(block.text) for block in blocks)
    if total_characters > maximum_evidence_characters:
        raise ValueError(
            f"证据文本共 {total_characters} 字符，超过单次精读上限；请按章节分批并保留块 ID"
        )
    valid_ids = {block.block_id for block in blocks}
    if len(valid_ids) != len(blocks) or "" in valid_ids:
        raise ValueError("证据块 ID 必须非空且唯一")
    evidence_level = _strongest_level(blocks)
    evidence_payload = [
        {
            "block_id": block.block_id,
            "locator": block.locator,
            "evidence_level": EvidenceLevel.parse(block.evidence_level).value,
            "text": block.text,
        }
        for block in blocks
    ]
    example_id = blocks[0].block_id
    prompt = json.dumps(
        {
            "paper": {"paper_id": paper.record_id, "title": paper.title},
            "core_questions": [f"Q{index}: {question}" for index, question in enumerate(core_questions, 1)],
            "evidence_blocks": evidence_payload,
            "required_output_shape": {
                "research_question": {"text": "...", "evidence_ids": [example_id]},
                "study_design": {"text": "...", "evidence_ids": [example_id]},
                "population_or_data": {"text": "...", "evidence_ids": [example_id]},
                "methods": {"text": "...", "evidence_ids": [example_id]},
                "findings": [{"text": "...", "evidence_ids": [example_id]}],
                "limitations": [{"text": "...", "evidence_ids": [example_id]}],
                "related_core_questions": [
                    {"question_id": "Q1", "text": "...", "evidence_ids": [example_id]}
                ],
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    raw = client.request_text(
        system_prompt=(
            "你是证据约束的论文精读 Agent。只能使用用户消息中的 evidence_blocks；"
            "不得使用记忆、外部知识或补全缺失细节。每个实质性陈述必须引用真实 block_id。"
            "摘要证据与知识库片段证据不得用于推断其文本没有呈现的方法、结果或局限。"
            "若证据未提供某字段，text 必须明确写‘未提供’，evidence_ids 为空。"
            "仅输出符合 required_output_shape 的单一 JSON 对象，不输出 Markdown。"
        ),
        user_prompt=prompt,
        json_mode=True,
    )
    payload = _extract_json_object(raw)
    required = {
        "research_question",
        "study_design",
        "population_or_data",
        "methods",
        "findings",
        "limitations",
        "related_core_questions",
    }
    if set(payload) != required:
        missing = sorted(required - set(payload))
        extra = sorted(set(payload) - required)
        raise LLMRequestError(
            f"结构化精读字段不匹配；缺少：{', '.join(missing) or '无'}；多余：{', '.join(extra) or '无'}"
        )
    blocks_by_id = {block.block_id: block for block in blocks}
    scalar_values: dict[str, str] = {}
    for field_name in ("research_question", "study_design", "population_or_data", "methods"):
        text, ids = _validate_statement(
            payload[field_name],
            field_name=field_name,
            valid_ids=valid_ids,
            blocks_by_id=blocks_by_id,
        )
        scalar_values[field_name] = text
        for block_id in ids:
            if field_name not in blocks_by_id[block_id].supports:
                blocks_by_id[block_id].supports.append(field_name)

    list_values: dict[str, list[str]] = {}
    for field_name in ("findings", "limitations"):
        raw_items = payload[field_name]
        if not isinstance(raw_items, list):
            raise LLMRequestError(f"字段 {field_name} 必须是数组")
        values: list[str] = []
        for index, item in enumerate(raw_items, 1):
            text, ids = _validate_statement(
                item,
                field_name=f"{field_name}[{index}]",
                valid_ids=valid_ids,
                blocks_by_id=blocks_by_id,
            )
            values.append(text)
            for block_id in ids:
                if field_name not in blocks_by_id[block_id].supports:
                    blocks_by_id[block_id].supports.append(field_name)
        if not values:
            values = [_unavailable("主要发现" if field_name == "findings" else "局限性", evidence_level)]
        list_values[field_name] = values

    related_raw = payload["related_core_questions"]
    if not isinstance(related_raw, list):
        raise LLMRequestError("字段 related_core_questions 必须是数组")
    allowed_questions = {f"Q{index}" for index, _question in enumerate(core_questions, 1)}
    related: list[str] = []
    for index, item in enumerate(related_raw, 1):
        if not isinstance(item, Mapping):
            raise LLMRequestError(f"related_core_questions[{index}] 必须是对象")
        qid = str(item.get("question_id") or "").strip()
        if qid not in allowed_questions:
            raise LLMRequestError(f"related_core_questions[{index}] 使用了未知问题编号：{qid}")
        text, ids = _validate_statement(
            {"text": item.get("text"), "evidence_ids": item.get("evidence_ids")},
            field_name=f"related_core_questions[{index}]",
            valid_ids=valid_ids,
            blocks_by_id=blocks_by_id,
        )
        if not ids:
            continue
        related.append(f"{qid}: {text} — 相关证据：{'、'.join(blocks_by_id[item_id].locator for item_id in ids)}")
        for block_id in ids:
            if qid not in blocks_by_id[block_id].supports:
                blocks_by_id[block_id].supports.append(qid)

    warnings = ["由大模型基于显式证据块生成；所有字段已通过块 ID 与结构校验，仍建议人工复核语义匹配"]
    if evidence_level is EvidenceLevel.ABSTRACT_ONLY:
        warnings.append("仅摘要证据：不得把模型整理结果当作摘要未呈现的全文细节")
    if evidence_level is EvidenceLevel.KNOWLEDGE_SNIPPET:
        warnings.append("知识库片段证据：不能替代原始论文全文")
    return ReadingNote(
        paper_id=paper.record_id,
        research_question=scalar_values["research_question"],
        study_design=scalar_values["study_design"],
        population_or_data=scalar_values["population_or_data"],
        methods=scalar_values["methods"],
        findings=list_values["findings"],
        limitations=list_values["limitations"],
        related_core_questions=related,
        evidence_blocks=blocks,
        evidence_level=evidence_level,
        warnings=warnings,
    )


def render_reading_note_markdown(paper: PaperRecord, note: ReadingNote) -> str:
    """Render one traceable reading card for local project storage."""

    level = EvidenceLevel.parse(note.evidence_level)
    findings = "\n".join(f"- {item}" for item in note.findings) or "- 未提供"
    limitations = "\n".join(f"- {item}" for item in note.limitations) or "- 未提供"
    relevance = "\n".join(f"- {item}" for item in note.related_core_questions) or "- 未映射到核心问题"
    warnings = "\n".join(f"- {item}" for item in note.warnings) or "- 无"
    def block_location(block: EvidenceBlock) -> str:
        details = [block.locator]
        if block.section:
            details.append(f"章节：{block.section}")
        if block.figure:
            details.append(block.figure)
        if block.table:
            details.append(block.table)
        if block.verification_status:
            details.append(f"核验：{block.verification_status}")
        return " · ".join(value for value in details if value)

    evidence = "\n\n".join(
        f"<a id=\"{block.block_id}\"></a>\n"
        f"**{block.block_id} · {block_location(block)} · {EVIDENCE_LABELS[EvidenceLevel.parse(block.evidence_level)]}**\n\n"
        f"> {block.text.replace(chr(10), chr(10) + '> ')}"
        for block in note.evidence_blocks
    ) or "无可用证据块。"
    return f"""# {paper.title}：结构化精读

> 文献 ID：{paper.record_id}  
> DOI：{paper.doi or '未提供'}  
> 证据等级：**{EVIDENCE_LABELS[level]}**

## 研究问题

{note.research_question}

## 研究设计

{note.study_design}

## 研究对象或数据

{note.population_or_data}

## 方法

{note.methods}

## 主要发现

{findings}

## 局限性

{limitations}

## 与核心问题的关系

{relevance}

## 证据边界与警告

{warnings}

## 可追溯证据块

{evidence}
"""
