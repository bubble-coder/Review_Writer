import json
import unittest

from review_writer.generators.llm_client import LLMRequestError
from review_writer.models import ResearchBrief
from review_writer.reporting import (
    LiteratureSummaryBundle,
    VerificationBundle,
    generate_literature_summary_bundle,
    generate_report_bundle,
    generate_verification_bundle,
)
from review_writer.workflow_models import (
    EvidenceBlock,
    EvidenceLevel,
    PaperRecord,
    ReadingNote,
)


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_text(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ReportingSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = ResearchBrief(
            topic="AI feedback",
            objectives="Summarize evidence",
            core_questions=["Does AI feedback improve learning?"],
            start_year=2020,
            end_year=2026,
            delivery_format="Markdown",
            delivery_requirements="Evidence-bound claims",
        )
        self.paper = PaperRecord(
            record_id="P001",
            title="AI feedback trial",
            authors=["A. Researcher"],
            year=2025,
            doi="10.1234/feedback.1",
            evidence_level=EvidenceLevel.FULL_TEXT,
        )
        self.note = ReadingNote(
            paper_id="P001",
            findings=["AI feedback improved the reported learning score."],
            related_core_questions=["Q1"],
            evidence_blocks=[
                EvidenceBlock(
                    paper_id="P001",
                    block_id="E001",
                    text="AI feedback improved the reported learning score.",
                    locator="p. 6#results",
                    evidence_level=EvidenceLevel.FULL_TEXT,
                )
            ],
            evidence_level=EvidenceLevel.FULL_TEXT,
        )

    def test_summary_is_generated_without_audit_claim_or_artifact(self) -> None:
        summary = generate_literature_summary_bundle(
            self.brief,
            "confirmed plan",
            {"broad": ["AI feedback"]},
            [self.paper],
            [self.note],
        )

        self.assertIsInstance(summary, LiteratureSummaryBundle)
        self.assertFalse(hasattr(summary, "audit"))
        self.assertFalse(hasattr(summary, "claim_citation_audit"))
        self.assertIn("本文件是文献总结草稿", summary.research_report)
        self.assertIn("尚不代表论断已经通过核验", summary.research_report)
        self.assertNotIn("claim_citation_audit.md", summary.research_report)
        self.assertEqual(len(summary.research_report_hash), 64)
        self.assertEqual(len(summary.claim_ledger_hash), 64)

    def test_verification_failure_does_not_regenerate_or_mutate_summary(self) -> None:
        synthesis = json.dumps(
            {
                "claims": [
                    {
                        "core_question_index": 1,
                        "claim_text": "AI feedback improved learning scores.",
                        "citation_ids": ["P001"],
                        "evidence_block_ids": ["E001"],
                    }
                ]
            }
        )
        client = FakeLLMClient([synthesis, LLMRequestError("audit unavailable")])
        summary = generate_literature_summary_bundle(
            self.brief,
            "confirmed plan",
            {},
            [self.paper],
            [self.note],
            llm_client=client,
            use_llm_synthesis=True,
        )
        original_report = summary.research_report
        original_hash = summary.claim_ledger_hash

        with self.assertRaisesRegex(LLMRequestError, "audit unavailable"):
            generate_verification_bundle(
                summary.claim_ledger,
                [self.paper],
                [self.note],
                llm_client=client,
                use_llm_semantic_audit=True,
            )

        self.assertEqual(len(client.calls), 2)
        self.assertIn("文献综合", client.calls[0]["system_prompt"])
        self.assertIn("语义核验员", client.calls[1]["system_prompt"])
        self.assertEqual(summary.research_report, original_report)
        self.assertEqual(summary.claim_ledger_hash, original_hash)

    def test_verification_bundle_covers_exact_ledger_revision(self) -> None:
        summary = generate_literature_summary_bundle(
            self.brief,
            "confirmed plan",
            {},
            [self.paper],
            [self.note],
        )
        verification = generate_verification_bundle(
            summary.claim_ledger,
            [self.paper],
            [self.note],
        )

        self.assertIsInstance(verification, VerificationBundle)
        self.assertFalse(hasattr(verification, "research_report"))
        self.assertEqual(verification.claim_ledger_hash, summary.claim_ledger_hash)
        self.assertEqual(verification.verification_data, verification.audit.to_dict())
        self.assertEqual(verification.audit.overall_status, "pass")
        self.assertEqual(verification.claim_audit_items[0].claim_id, "C001")

    def test_legacy_bundle_remains_available(self) -> None:
        bundle = generate_report_bundle(
            self.brief,
            "confirmed plan",
            {},
            [self.paper],
            [self.note],
        )

        self.assertIn("AI feedback trial", bundle.research_report)
        self.assertIn("论断—引文核验报告", bundle.claim_citation_audit)
        self.assertEqual(
            bundle.literature_summary_bundle.claim_ledger_hash,
            bundle.verification_bundle.claim_ledger_hash,
        )


if __name__ == "__main__":
    unittest.main()
