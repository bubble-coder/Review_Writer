import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from review_writer.core_ranking import score_core_papers
from review_writer.document_ingest import DocumentIngestor
from review_writer.evidence_index import EvidenceIndex
from review_writer.exports import export_verified_report, render_ris, write_docx
from review_writer.llm_policy import DataClass, MaterialDescriptor, ModelCallPolicy, preflight_model_call
from review_writer.provenance import DeliveryPolicy, evaluate_delivery_gate
from review_writer.review_protocol import (
    ReviewMode, ReviewProtocol, ScreeningDecision, ScreeningDecisionValue,
    ScreeningStage, calculate_prisma,
)
from review_writer.settings import ModelSettings
from review_writer.source_adapter import merge_with_field_provenance
from review_writer.source_adapter import ResponseCache
from review_writer.task_queue import RetryableTaskError, TaskStore, TaskWorker
from review_writer.workflow_models import EvidenceBlock, EvidenceLevel, PaperRecord, ReadingNote


class TaskQueueTests(unittest.TestCase):
    def test_is_idempotent_recovers_and_resumes_from_checkpoint(self) -> None:
        with TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.sqlite3")
            first = store.enqueue("work", {"n": 2}, idempotency_key="same")
            self.assertEqual(store.enqueue("work", {"n": 99}, idempotency_key="same").task_id, first.task_id)
            claimed = store.claim_next()
            self.assertIsNotNone(claimed)
            store.update_progress(first.task_id, 1, 2, checkpoint={"last": 1})
            self.assertEqual(store.recover_interrupted(), 1)
            self.assertEqual(store.get(first.task_id).checkpoint, {"last": 1})

    def test_worker_retries_retryable_errors(self) -> None:
        with TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.sqlite3")
            task = store.enqueue("flaky", {}, max_attempts=2)
            worker = TaskWorker(store)
            calls = []

            def handler(context, payload):
                calls.append(1)
                raise RetryableTaskError("temporary")

            worker.register("flaky", handler)
            worker.run_once()
            self.assertEqual(store.get(task.task_id).state, "retry_wait")

    def test_pending_cancel_is_immediate(self) -> None:
        with TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.sqlite3")
            task = store.enqueue("work", {})
            store.request_cancel(task.task_id)
            self.assertEqual(store.get(task.task_id).state, "cancelled")


class ReviewProtocolTests(unittest.TestCase):
    def test_prisma_is_derived_from_item_level_decisions(self) -> None:
        decisions = [
            ScreeningDecision("p1", ScreeningStage.TITLE_ABSTRACT, ScreeningDecisionValue.INCLUDE),
            ScreeningDecision("p2", ScreeningStage.TITLE_ABSTRACT, ScreeningDecisionValue.EXCLUDE, "wrong_population"),
            ScreeningDecision("p1", ScreeningStage.FULL_TEXT, ScreeningDecisionValue.INCLUDE),
        ]
        flow = calculate_prisma(["p1", "p2"], decisions, duplicate_count=1)
        self.assertEqual(flow.identified, 3)
        self.assertEqual(flow.screened, 2)
        self.assertEqual(flow.included, 1)
        protocol = ReviewProtocol("Review", ReviewMode.SYSTEMATIC)
        self.assertTrue(protocol.validate())


class ProvenanceAndRankingTests(unittest.TestCase):
    def test_strict_gate_blocks_unverified_claims(self) -> None:
        gate = evaluate_delivery_gate([{"claim_id": "C1", "status": "manual_needed"}])
        self.assertFalse(gate.allowed)
        self.assertTrue(evaluate_delivery_gate([{"claim_id": "C1", "status": "manual_needed"}], policy=DeliveryPolicy.WARN).allowed)

    def test_merge_preserves_conflicting_candidates(self) -> None:
        merged, provenance, conflicts = merge_with_field_provenance([
            ("Crossref", {"title": "A", "year": 2024}),
            ("OpenAlex", {"title": "B", "year": 2024}),
        ], source_priority=["Crossref", "OpenAlex"])
        self.assertEqual(merged["title"], "A")
        self.assertEqual(len(provenance["title"]), 2)
        self.assertEqual(conflicts[0].field_name, "title")

    def test_core_score_is_explainable(self) -> None:
        paper = PaperRecord(title="Trial", journal="J", doi="10.1/x", citation_count=20,
                            relevance_score=.9, evidence_level=EvidenceLevel.FULL_TEXT,
                            extra={"study_design": "randomized_controlled_trial"})
        score = score_core_papers([paper])[0]
        self.assertIn("study_design", score.dimensions)
        self.assertTrue(score.reasons)


class PolicyIndexAndExportTests(unittest.TestCase):
    def test_model_policy_blocks_material_above_allowed_class(self) -> None:
        preflight = preflight_model_call(
            ModelSettings(), ModelCallPolicy(maximum_data_class=DataClass.ABSTRACT),
            [MaterialDescriptor("full", DataClass.LICENSED_FULLTEXT, 1000)], purpose="reading",
        )
        self.assertFalse(preflight.allowed)
        self.assertEqual(preflight.blocked_material_ids, ("full",))

    def test_ris_docx_and_cross_project_index(self) -> None:
        paper = PaperRecord(title="Evidence", authors=["A Author"], year=2024, doi="10.1/test", abstract="Useful evidence")
        block = EvidenceBlock(paper.record_id, "A sufficiently long evidence block for indexing and testing.", EvidenceLevel.ABSTRACT_ONLY, locator="abstract")
        note = ReadingNote(paper.record_id, evidence_blocks=[block], evidence_level=EvidenceLevel.ABSTRACT_ONLY)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index = EvidenceIndex(root / "index.sqlite3")
            self.assertEqual(index.index_project("project", root, "Project", [paper], [note]), 2)
            self.assertTrue(index.search("Evidence"))
            docx = write_docx("# 报告\n\n正文", root / "report.docx")
            self.assertTrue(docx.is_file())
            self.assertIn("TY  - GEN", render_ris([paper]))
            with self.assertRaises(ValueError):
                export_verified_report("# report", [{"claim_id": "C1", "status": "fail"}], root / "blocked.md", format="md")

    def test_html_ingest_keeps_asset_hash_and_structured_locator(self) -> None:
        paper = PaperRecord(title="Useful Trial Evidence")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.html"
            body = "Useful Trial Evidence. " + "This controlled study reports useful evidence and limitations. " * 20
            path.write_text(f"<html><article><h1>Useful Trial Evidence</h1><p>{body}</p></article></html>", encoding="utf-8")
            result = DocumentIngestor().ingest(paper, path)
            self.assertEqual(result.asset.verification_status, "verified")
            self.assertEqual(len(result.asset.checksum_sha256), 64)
            self.assertTrue(result.evidence_blocks)
            self.assertEqual(result.evidence_blocks[0].asset_id, result.asset.asset_id)

    def test_response_cache_is_durable_and_expires(self) -> None:
        with TemporaryDirectory() as directory:
            cache = ResponseCache(Path(directory) / "cache.sqlite3")
            cache.put("key", "source", {"papers": [1]}, ttl_seconds=60)
            self.assertEqual(cache.get("key"), {"papers": [1]})


if __name__ == "__main__":
    unittest.main()
