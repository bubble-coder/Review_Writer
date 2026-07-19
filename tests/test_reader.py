import json
import unittest

from review_writer.fulltext import ExtractedPage, PDFVerification
from review_writer.generators.llm_client import LLMRequestError
from review_writer.reader import (
    build_page_evidence_blocks,
    read_paper_deterministically,
    read_paper_with_llm,
    render_reading_note_markdown,
)
from review_writer.workflow_models import EvidenceBlock, EvidenceLevel, PaperRecord


class _FakeLLMClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_user_prompt = ""

    def request_text(self, *, system_prompt, user_prompt, json_mode):
        self.last_user_prompt = user_prompt
        self.system_prompt = system_prompt
        self.json_mode = json_mode
        return json.dumps(self.payload, ensure_ascii=False)


class StructuredReaderTests(unittest.TestCase):
    def test_abstract_only_is_explicit_and_does_not_invent_missing_details(self) -> None:
        paper = PaperRecord(
            title="Abstract-only study",
            abstract="We aimed to assess treatment response. Results showed improved recovery.",
        )

        note = read_paper_deterministically(paper, core_questions=["Does treatment improve recovery?"])

        self.assertEqual(note.evidence_level, EvidenceLevel.ABSTRACT_ONLY)
        self.assertTrue(all(block.locator.startswith("abstract#") for block in note.evidence_blocks))
        self.assertTrue(any("仅摘要证据" in warning for warning in note.warnings))
        self.assertIn("仅摘要证据", note.methods)

    def test_pdf_pages_receive_stable_ids_anchors_and_question_mapping(self) -> None:
        paper = PaperRecord(title="Trial", record_id="paper-trial")
        pages = [
            ExtractedPage(
                1,
                "We aimed to test treatment efficacy.\n\n"
                "This randomized trial included 100 patients and used regression analysis.",
            ),
            ExtractedPage(
                2,
                "Results showed treatment improved recovery significantly.\n\n"
                "A limitation was the small sample.",
            ),
        ]

        note = read_paper_deterministically(
            paper,
            pages=pages,
            core_questions=["Does treatment improve recovery?"],
        )

        self.assertEqual(note.evidence_level, EvidenceLevel.FULL_TEXT)
        self.assertEqual(
            [block.block_id for block in note.evidence_blocks],
            [
                "paper-trial:S0001",
                "paper-trial:S0002",
                "paper-trial:S0003",
                "paper-trial:S0004",
            ],
        )
        self.assertEqual(note.evidence_blocks[2].locator, "p.2#S0003")
        self.assertTrue(note.related_core_questions[0].startswith("Q1:"))
        self.assertIn("findings", note.evidence_blocks[2].supports)

    def test_scanned_pdf_falls_back_to_abstract_and_marks_ocr_needed(self) -> None:
        paper = PaperRecord(title="Scan", abstract="We report an observational study.")
        verification = PDFVerification(
            path="scan.pdf",
            status="downloaded",
            valid_pdf=True,
            page_count=10,
            ocr_needed=True,
            warnings=["text layer absent"],
        )

        note = read_paper_deterministically(paper, verification=verification)

        self.assertEqual(note.evidence_level, EvidenceLevel.ABSTRACT_ONLY)
        self.assertTrue(any("OCR" in warning for warning in note.warnings))
        self.assertTrue(any("仅摘要证据" in warning for warning in note.warnings))

    def test_llm_reader_sends_only_supplied_blocks_and_validates_citations(self) -> None:
        paper = PaperRecord(title="Grounded trial", record_id="paper-grounded")
        blocks = build_page_evidence_blocks(
            paper.record_id,
            [
                ExtractedPage(
                    1,
                    "We aimed to test treatment efficacy in 100 patients. "
                    "This randomized trial used regression analysis. "
                    "Results showed improved recovery. A limitation was the small sample.",
                )
            ],
        )
        payload = {
            "research_question": {"text": "Test treatment efficacy", "evidence_ids": ["paper-grounded:S0001"]},
            "study_design": {"text": "Randomized trial", "evidence_ids": ["paper-grounded:S0001"]},
            "population_or_data": {"text": "100 patients", "evidence_ids": ["paper-grounded:S0001"]},
            "methods": {"text": "Regression analysis", "evidence_ids": ["paper-grounded:S0001"]},
            "findings": [{"text": "Improved recovery", "evidence_ids": ["paper-grounded:S0001"]}],
            "limitations": [{"text": "Small sample", "evidence_ids": ["paper-grounded:S0001"]}],
            "related_core_questions": [
                {"question_id": "Q1", "text": "Treatment recovery", "evidence_ids": ["paper-grounded:S0001"]}
            ],
        }
        client = _FakeLLMClient(payload)

        note = read_paper_with_llm(
            paper,
            evidence_blocks=blocks,
            client=client,
            core_questions=["Does treatment improve recovery?"],
        )

        prompt = json.loads(client.last_user_prompt)
        self.assertEqual(prompt["evidence_blocks"][0]["text"], blocks[0].text)
        self.assertEqual(note.evidence_level, EvidenceLevel.FULL_TEXT)
        self.assertTrue(note.related_core_questions[0].startswith("Q1:"))
        self.assertIn("Q1", blocks[0].supports)

    def test_llm_reader_rejects_unknown_evidence_id(self) -> None:
        paper = PaperRecord(title="Grounded", record_id="paper-grounded")
        block = EvidenceBlock(
            paper_id=paper.record_id,
            text="The study method was regression.",
            evidence_level=EvidenceLevel.FULL_TEXT,
            block_id="paper-grounded:S0001",
            locator="p.1#S0001",
        )
        statement = {"text": "Regression method", "evidence_ids": ["UNKNOWN"]}
        client = _FakeLLMClient(
            {
                "research_question": statement,
                "study_design": statement,
                "population_or_data": statement,
                "methods": statement,
                "findings": [statement],
                "limitations": [statement],
                "related_core_questions": [],
            }
        )

        with self.assertRaises(LLMRequestError):
            read_paper_with_llm(paper, evidence_blocks=[block], client=client)

    def test_markdown_keeps_evidence_level_and_source_anchors(self) -> None:
        paper = PaperRecord(title="Readable", abstract="We found a result.")
        note = read_paper_deterministically(paper)

        markdown = render_reading_note_markdown(paper, note)

        self.assertIn("证据等级：**仅摘要证据**", markdown)
        self.assertIn("abstract#A0001", markdown)
        self.assertIn(f'<a id="{paper.record_id}:A0001"></a>', markdown)


if __name__ == "__main__":
    unittest.main()
