import json
import unittest

from review_writer.generators.llm_client import LLMRequestError
from review_writer.models import ResearchBrief
from review_writer.reader import read_paper_deterministically
from review_writer.reporting import (
    ABSTRACT_ONLY,
    FULL_TEXT,
    KNOWLEDGE_SNIPPET,
    METADATA_ONLY,
    ClaimLedgerEntry,
    audit_claim_ledger,
    build_deterministic_claim_ledger,
    generate_llm_claim_ledger,
    generate_report_bundle,
    is_valid_doi,
)
from review_writer.workflow_models import (
    EvidenceBlock,
    EvidenceLevel,
    KeywordNode,
    PaperRecord,
    ReadingNote,
    SearchStrategyBundle,
)


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = ResearchBrief(
            topic="生成式人工智能与学习成效",
            objectives="综合现有研究并识别证据限制",
            core_questions=["生成式人工智能如何影响学习成效？", "研究有哪些局限？"],
            start_year=2020,
            end_year=2026,
            delivery_format="Markdown 综合报告",
            delivery_requirements="中文；逐条核验引用",
        )
        self.full_paper = PaperRecord(
            record_id="P001",
            title="A controlled study",
            authors=["Alice Smith", "Bo Chen"],
            year=2025,
            doi="10.1234/example.1",
            url="https://example.test/paper",
            source="Crossref",
            evidence_level=EvidenceLevel.FULL_TEXT,
        )
        self.abstract_paper = PaperRecord(
            record_id="P002",
            title="An abstract-only study",
            authors=["Chris Lee"],
            year=2024,
            source="OpenAlex",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        )
        self.full_note = ReadingNote(
            paper_id="P001",
            findings=["干预组在期末测验中的表现高于对照组。"],
            limitations=["样本来自单一院校。"],
            related_core_questions=["Q1: 学习成效"],
            evidence_blocks=[
                EvidenceBlock(
                    paper_id="P001",
                    block_id="E001",
                    text="The intervention group scored higher than the control group.",
                    evidence_level=EvidenceLevel.FULL_TEXT,
                    locator="p. 6#results",
                    supports=["findings", "Q1"],
                )
            ],
            evidence_level=EvidenceLevel.FULL_TEXT,
        )
        self.abstract_note = ReadingNote(
            paper_id="P002",
            findings=["摘要报告了积极的学习结果。"],
            related_core_questions=["Q2: 局限"],
            evidence_blocks=[
                EvidenceBlock(
                    paper_id="P002",
                    block_id="E002",
                    text="The abstract reports positive learning outcomes.",
                    evidence_level=EvidenceLevel.ABSTRACT_ONLY,
                    locator="abstract#S001",
                )
            ],
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        )
        self.strategies = SearchStrategyBundle(
            topic=self.brief.topic,
            core_questions=self.brief.core_questions,
            keyword_tree=KeywordNode("generative AI", synonyms=["GenAI"]),
            broad_queries=['("generative AI" OR GenAI) AND education'],
            precision_queries=['"generative AI" AND "learning outcome"'],
            source_queries={
                "OpenAlex": {"broad": ["generative AI education"], "precision": []}
            },
            start_year=2020,
            end_year=2026,
        )

    def test_generates_report_audit_and_bibtex_from_shared_models(self) -> None:
        bundle = generate_report_bundle(
            self.brief,
            "# 已确认计划\n按核心问题执行。",
            self.strategies,
            [self.full_paper, self.abstract_paper],
            [self.full_note, self.abstract_note],
        )

        self.assertIn("### Q1. 生成式人工智能如何影响学习成效？", bundle.research_report)
        self.assertIn("**C001**", bundle.research_report)
        self.assertIn("〔全文证据〕 [P001]", bundle.research_report)
        self.assertIn("〔仅摘要证据〕 [P002]", bundle.research_report)
        self.assertIn("### 宽检索", bundle.research_report)
        self.assertIn("10.1234/example.1", bundle.references_bib)
        self.assertIn("Review Writer citation ID: P001", bundle.references_bib)

        by_id = {item.claim_id: item for item in bundle.audit.results}
        self.assertEqual(by_id["C001"].status, "pass")
        self.assertEqual(by_id["C002"].status, "warning")
        self.assertEqual(bundle.audit.overall_status, "warning")
        self.assertEqual(bundle.claim_audit_items[0].claim, bundle.claim_ledger[0].claim_text)
        json.dumps([item.to_dict() for item in bundle.claim_ledger], ensure_ascii=False)
        json.dumps(bundle.audit.to_dict(), ensure_ascii=False)

    def test_multiple_questions_require_explicit_q_mapping(self) -> None:
        note = ReadingNote(
            paper_id="P001",
            findings=["这条发现没有核心问题编号。"],
            evidence_blocks=self.full_note.evidence_blocks,
            evidence_level=EvidenceLevel.FULL_TEXT,
        )
        claims = build_deterministic_claim_ledger(self.brief, [self.full_paper], [note])
        self.assertEqual(claims[0].core_question_index, 0)

    def test_cross_paper_block_id_collisions_are_qualified(self) -> None:
        first_note = ReadingNote(
            paper_id="P001",
            findings=["First result."],
            related_core_questions=["Q1"],
            evidence_blocks=[
                EvidenceBlock(
                    paper_id="P001",
                    block_id="S0001",
                    text="First result.",
                    locator="p.1#S0001",
                    evidence_level=EvidenceLevel.FULL_TEXT,
                )
            ],
            evidence_level=EvidenceLevel.FULL_TEXT,
        )
        second_note = ReadingNote(
            paper_id="P002",
            findings=["Second result."],
            related_core_questions=["Q2"],
            evidence_blocks=[
                EvidenceBlock(
                    paper_id="P002",
                    block_id="S0001",
                    text="Second result.",
                    locator="p.2#S0001",
                    evidence_level=EvidenceLevel.ABSTRACT_ONLY,
                )
            ],
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        )

        claims = build_deterministic_claim_ledger(
            self.brief,
            [self.full_paper, self.abstract_paper],
            [first_note, second_note],
        )

        self.assertEqual(claims[0].evidence_block_ids, ("P001:S0001",))
        self.assertEqual(claims[1].evidence_block_ids, ("P002:S0001",))
        audit = audit_claim_ledger(
            claims,
            [self.full_paper, self.abstract_paper],
            [first_note, second_note],
        )
        self.assertNotEqual(audit.overall_status, "fail")

    def test_reader_output_flows_directly_into_report_bundle(self) -> None:
        brief = ResearchBrief(
            topic="Feedback and learning",
            objectives="Summarize reported outcomes",
            core_questions=["What learning outcomes were reported?"],
            start_year=2020,
            end_year=2026,
            delivery_format="Markdown",
            delivery_requirements="Evidence labels",
        )
        paper = PaperRecord(
            record_id="P005",
            title="Feedback outcomes",
            authors=["Dana Wu"],
            year=2025,
            doi="10.5555/feedback.2025",
            abstract="The study found that feedback improved the reported learning outcome.",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        )
        note = read_paper_deterministically(
            paper, core_questions=brief.core_questions
        )

        bundle = generate_report_bundle(
            brief,
            "confirmed plan",
            {"broad": ["feedback AND learning"]},
            [paper],
            [note],
        )

        self.assertTrue(note.evidence_blocks[0].block_id.startswith("P005:A"))
        self.assertIn(note.evidence_blocks[0].block_id, bundle.research_report)
        self.assertEqual(bundle.audit.overall_status, "warning")

    def test_metadata_only_substantive_claim_fails(self) -> None:
        paper = PaperRecord(record_id="P003", title="Metadata record")
        note = ReadingNote(
            paper_id="P003",
            findings=["不应由元数据支持的结论。"],
            evidence_level=EvidenceLevel.METADATA_ONLY,
        )
        claim = ClaimLedgerEntry(
            claim_id="C001",
            core_question_index=1,
            claim_text="不应由元数据支持的结论。",
            citation_ids=("P003",),
            evidence_block_ids=(),
            evidence_level=METADATA_ONLY,
        )

        audit = audit_claim_ledger([claim], [paper], [note])

        self.assertEqual(audit.overall_status, "fail")
        self.assertTrue(
            any(check.code == "evidence_scope" and check.status == "fail" for check in audit.results[0].checks)
        )

    def test_knowledge_snippet_requires_manual_review(self) -> None:
        paper = PaperRecord(record_id="P004", title="IMA result")
        note = ReadingNote(
            paper_id="P004",
            findings=["知识库片段中的结果。"],
            related_core_questions=["Q1"],
            evidence_blocks=[
                EvidenceBlock(
                    paper_id="P004",
                    block_id="E004",
                    text="A returned IMA knowledge-base snippet.",
                    evidence_level=EvidenceLevel.KNOWLEDGE_SNIPPET,
                    locator="ima#result-1",
                )
            ],
            evidence_level=EvidenceLevel.KNOWLEDGE_SNIPPET,
        )
        claim = build_deterministic_claim_ledger(self.brief, [paper], [note])[0]
        audit = audit_claim_ledger([claim], [paper], [note])
        self.assertEqual(claim.evidence_level, KNOWLEDGE_SNIPPET)
        self.assertEqual(audit.results[0].status, "manual_needed")

    def test_abstract_block_cannot_be_labeled_as_full_text(self) -> None:
        claim = ClaimLedgerEntry(
            claim_id="C099",
            core_question_index=2,
            claim_text="摘要报告了积极的学习结果。",
            citation_ids=("P002",),
            evidence_block_ids=("E002",),
            evidence_level=FULL_TEXT,
        )

        audit = audit_claim_ledger(
            [claim], [self.abstract_paper], [self.abstract_note]
        )

        self.assertEqual(audit.overall_status, "fail")
        self.assertTrue(
            any(
                check.code == "evidence_label" and check.status == "fail"
                for check in audit.results[0].checks
            )
        )

    def test_llm_synthesis_rejects_dangling_evidence_without_fallback(self) -> None:
        response = {
            "claims": [
                {
                    "core_question_index": 1,
                    "claim_text": "模型论断",
                    "citation_ids": ["P001"],
                    "evidence_block_ids": ["DOES-NOT-EXIST"],
                }
            ]
        }
        client = FakeLLMClient([json.dumps(response, ensure_ascii=False)])

        with self.assertRaisesRegex(LLMRequestError, "未知证据块"):
            generate_llm_claim_ledger(
                self.brief,
                [self.full_paper],
                [self.full_note],
                llm_client=client,
            )

    def test_llm_request_error_is_propagated_without_local_fallback(self) -> None:
        class FailingClient:
            def request_text(self, **kwargs):
                raise LLMRequestError("provider unavailable")

        with self.assertRaisesRegex(LLMRequestError, "provider unavailable"):
            generate_report_bundle(
                self.brief,
                "confirmed plan",
                self.strategies,
                [self.full_paper],
                [self.full_note],
                llm_client=FailingClient(),
                use_llm_synthesis=True,
            )

    def test_unknown_citation_id_fails(self) -> None:
        claim = ClaimLedgerEntry(
            claim_id="C404",
            core_question_index=1,
            claim_text="不存在的文献不能支撑该论断。",
            citation_ids=("P404",),
            evidence_block_ids=("E001",),
            evidence_level=FULL_TEXT,
        )

        audit = audit_claim_ledger(
            [claim], [self.full_paper], [self.full_note]
        )

        self.assertEqual(audit.overall_status, "fail")
        self.assertTrue(
            any(
                check.code == "citation_exists" and check.status == "fail"
                for check in audit.results[0].checks
            )
        )

    def test_llm_partial_semantic_verdict_fails_claim(self) -> None:
        synthesis = {
            "claims": [
                {
                    "core_question_index": 1,
                    "claim_text": "干预组表现更高，并且效果适用于所有院校。",
                    "citation_ids": ["P001"],
                    "evidence_block_ids": ["E001"],
                }
            ]
        }
        semantic = {
            "results": [
                {
                    "claim_id": "C001",
                    "verdict": "partial",
                    "rationale": "摘录支持组间差异，但不支持推广到所有院校。",
                }
            ]
        }
        client = FakeLLMClient(
            [json.dumps(synthesis, ensure_ascii=False), json.dumps(semantic, ensure_ascii=False)]
        )

        bundle = generate_report_bundle(
            self.brief,
            "confirmed plan",
            self.strategies,
            [self.full_paper],
            [self.full_note],
            llm_client=client,
            use_llm_synthesis=True,
            use_llm_semantic_audit=True,
        )

        self.assertEqual(bundle.audit.overall_status, "fail")
        self.assertIn("partial", bundle.claim_citation_audit)
        semantic_prompt = client.calls[1]["user_prompt"]
        self.assertIn("The intervention group scored higher", semantic_prompt)
        self.assertNotIn("样本来自单一院校", semantic_prompt)

    def test_doi_validation_is_syntactic_and_local(self) -> None:
        self.assertTrue(is_valid_doi("https://doi.org/10.1000/xyz-123"))
        self.assertFalse(is_valid_doi("not-a-doi"))
        self.assertFalse(is_valid_doi("10.123/a b"))


if __name__ == "__main__":
    unittest.main()
