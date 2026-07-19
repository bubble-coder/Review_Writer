import unittest

from review_writer.report_lifecycle import (
    AuditState,
    audit_matches_current,
    ledger_hash,
    nonclaim_text_hash,
    synchronize_claims_from_report,
    text_hash,
)
from review_writer.reporting import ClaimLedgerEntry


class ReportLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.claim = ClaimLedgerEntry(
            claim_id="C001", core_question_index=1, claim_text="原始论断。",
            citation_ids=("P001",), evidence_block_ids=("P001:B1",), evidence_level="全文证据",
        )

    def test_visible_claim_edit_is_synchronized_but_keeps_evidence_binding(self) -> None:
        result = synchronize_claims_from_report(
            "- **C001** 修改后的论断。 〔全文证据〕 [P001]\n", [self.claim]
        )
        self.assertFalse(result.errors)
        self.assertEqual(result.changed_claim_ids, ("C001",))
        self.assertEqual(result.claims[0].claim_text, "修改后的论断。")
        self.assertEqual(result.claims[0].evidence_block_ids, ("P001:B1",))

    def test_unknown_missing_and_duplicate_ids_are_blocking(self) -> None:
        result = synchronize_claims_from_report(
            "- **C002** 新论断。 [P001]\n- **C002** 重复。 [P001]\n", [self.claim]
        )
        self.assertEqual(len(result.errors), 3)

    def test_audit_hash_must_match_current_report_and_ledger(self) -> None:
        report = "summary"
        state = AuditState(text_hash(report), ledger_hash([self.claim]), "local", "pass")
        self.assertTrue(audit_matches_current(state, report, [self.claim]))
        self.assertFalse(audit_matches_current(state, report + " edited", [self.claim]))

    def test_nonclaim_hash_ignores_claim_bullet_edits_but_not_other_prose(self) -> None:
        original = "# 报告\n\n方法正文\n- **C001** 原始论断。 〔全文证据〕 [P001]\n"
        claim_edit = original.replace("原始论断", "改写论断")
        prose_edit = original.replace("方法正文", "被改写的方法正文")
        self.assertEqual(nonclaim_text_hash(original), nonclaim_text_hash(claim_edit))
        self.assertNotEqual(nonclaim_text_hash(original), nonclaim_text_hash(prose_edit))


if __name__ == "__main__":
    unittest.main()
