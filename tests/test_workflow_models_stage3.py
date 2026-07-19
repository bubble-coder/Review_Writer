import json
import unittest

from review_writer.workflow_models import (
    ClaimAuditItem,
    EvidenceBlock,
    EvidenceLevel,
    KeywordNode,
    PaperRecord,
    ReadingNote,
    SearchStrategyBundle,
)


class WorkflowModelRoundTripTests(unittest.TestCase):
    def test_strategy_and_nested_tree_round_trip_through_json(self) -> None:
        strategy = SearchStrategyBundle(
            topic="Explainable AI",
            core_questions=["Which methods are validated?"],
            keyword_tree=KeywordNode(
                "Explainable AI",
                synonyms=["XAI"],
                children=[KeywordNode("validation")],
            ),
            broad_queries=['"Explainable AI" OR XAI'],
            precision_queries=['"Explainable AI" AND validation'],
            start_year=2020,
            end_year=2026,
        )

        restored = SearchStrategyBundle.from_dict(json.loads(json.dumps(strategy.to_dict())))

        self.assertEqual(restored.core_questions, strategy.core_questions)
        self.assertEqual(restored.keyword_tree.children[0].term, "validation")
        self.assertEqual(restored.start_year, 2020)

    def test_paper_normalizes_doi_and_keeps_provenance(self) -> None:
        paper = PaperRecord(
            title="A paper",
            authors=["Ada Lovelace"],
            doi="https://doi.org/10.1000/ABC.",
            source="Crossref",
            queries=["query one", "query two"],
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        )

        restored = PaperRecord.from_dict(paper.to_dict())

        self.assertEqual(restored.doi, "10.1000/abc")
        self.assertTrue(restored.record_id.startswith("paper-"))
        self.assertEqual(restored.query, "query one")
        self.assertEqual(restored.evidence_level.label, "仅摘要证据")
        self.assertIn("Crossref", restored.sources)

    def test_evidence_block_has_stable_id_for_claim_audit(self) -> None:
        block = EvidenceBlock(
            paper_id="paper-1",
            text="The intervention improved the outcome.",
            locator="p. 4",
            evidence_level=EvidenceLevel.FULL_TEXT,
        )
        same = EvidenceBlock.from_dict(block.to_dict())
        note = ReadingNote(
            paper_id="paper-1",
            evidence_blocks=[same],
            evidence_level=EvidenceLevel.FULL_TEXT,
        )
        audit = ClaimAuditItem(
            claim_id="C1",
            claim="The intervention improved the outcome.",
            evidence_block_ids=[block.block_id],
            evidence_level=EvidenceLevel.FULL_TEXT,
        )

        self.assertEqual(same.block_id, block.block_id)
        self.assertEqual(note.to_dict()["evidence_blocks"][0]["block_id"], block.block_id)
        self.assertEqual(audit.to_dict()["evidence_block_ids"], [block.block_id])


if __name__ == "__main__":
    unittest.main()

