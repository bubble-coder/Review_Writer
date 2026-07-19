from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from unittest.mock import patch

from review_writer.models import ResearchBrief
from review_writer.reader import read_paper_deterministically
from review_writer.reporting import generate_literature_summary_bundle, generate_verification_bundle
from review_writer.search_strategy import generate_local_strategy
from review_writer.secret_store import SecretStore
from review_writer.settings import SettingsStore
from review_writer.storage import save_project
from review_writer.workflow_models import EvidenceLevel, PaperRecord
from review_writer.workflow_store import WorkflowStore
from review_writer.workflow_view import ExecutionWorkspace


class ExecutionWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tkinter display is unavailable: {error}")
        self.root.withdraw()
        self.temporary = TemporaryDirectory()
        self.settings_store = SettingsStore(Path(self.temporary.name) / "settings.json")
        self.settings = self.settings_store.load()
        self.secret_store = SecretStore(Path(self.temporary.name) / "secrets.json")
        self.brief = ResearchBrief(
            topic="生成式人工智能教育应用",
            objectives="梳理效果与风险",
            core_questions=["学习效果如何？", "有哪些风险？"],
            start_year=2020,
            end_year=2026,
            delivery_format="Markdown 综合报告",
            delivery_requirements="中文",
        )
        self.project = save_project(
            brief=self.brief,
            plan_text="# 已确认计划",
            status="confirmed",
            output_root=Path(self.temporary.name) / "outputs",
        )
        self.view = ExecutionWorkspace(
            self.root,
            settings_store=self.settings_store,
            settings=self.settings,
            secret_store=self.secret_store,
            on_back=lambda: None,
            on_database_settings=lambda: None,
            on_model_settings=lambda: None,
        )
        self.view.set_project(
            brief=self.brief,
            confirmed_plan="# 已确认计划",
            project_directory=self.project,
        )
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            self.root.destroy()
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def test_confirmed_strategy_unlocks_search_and_writes_artifacts(self) -> None:
        self.view.strategy = generate_local_strategy(self.brief)
        self.view._display_strategy()
        self.view.broad_queries_text.insert("end", "\n用户补充宽检索式")

        self.view.confirm_strategy()

        manifest = WorkflowStore(self.project).load_manifest()
        self.assertEqual(manifest["workflow"]["stages"]["strategy"], "complete")
        self.assertEqual(manifest["workflow"]["stages"]["search"], "pending")
        saved = WorkflowStore(self.project).load_json("search/search_strategy.json")
        self.assertIn("用户补充宽检索式", saved["broad_queries"])
        self.assertTrue((self.project / "search" / "keyword_tree.md").is_file())

    def test_markdown_windows_have_preview_and_source_modes(self) -> None:
        self.view.strategy = generate_local_strategy(self.brief)
        self.view._display_strategy()
        self.root.update()

        self.assertEqual(
            set(self.view.markdown_views),
            {"keyword_tree", "reading_card", "report", "audit"},
        )
        keyword = self.view.markdown_views["keyword_tree"]
        self.assertEqual(keyword.mode.get(), "preview")
        self.assertTrue(keyword.preview.tag_ranges("h1"))

        keyword.source_button.invoke()
        self.assertEqual(keyword.mode.get(), "source")
        self.view.keyword_tree_text.insert("end", "\n## 用户补充")
        keyword.preview_button.invoke()
        self.assertIn("用户补充", keyword.preview.get("1.0", "end-1c"))
        self.assertTrue(keyword.preview.tag_ranges("h2"))

        self.assertEqual(self.view.markdown_views["reading_card"].source_button.cget("text"), "原文")
        self.assertEqual(str(self.view.reading_preview.cget("state")), "disabled")

    def test_execution_tabs_are_controlled_by_the_app_sidebar(self) -> None:
        self.assertEqual(self.view.notebook.cget("style"), "Workspace.TNotebook")

    def test_summary_and_verification_have_separate_pages_and_actions(self) -> None:
        self.assertEqual(len(self.view.report_audit_notebook.tabs()), 2)
        self.assertEqual(self.view.report_generate_button.cget("text"), "生成文献总结报告")
        self.assertEqual(self.view.audit_generate_button.cget("text"), "生成核验报告")
        self.assertIsNot(self.view.report_status_var, self.view.audit_status_var)

    def test_summary_can_be_saved_before_independent_verification(self) -> None:
        paper = PaperRecord(
            title="A controlled education study",
            authors=["Example, A."],
            year=2025,
            doi="10.1234/example",
            source="Crossref",
            abstract="This study reports a measured improvement and documents limitations.",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
            access_status="仅发现摘要",
        )
        note = read_paper_deterministically(paper, core_questions=self.brief.core_questions)
        strategy = generate_local_strategy(self.brief)
        summary = generate_literature_summary_bundle(
            self.brief, "# 已确认计划", strategy, [paper], [note]
        )

        self.view._save_summary_bundle(
            summary,
            report_text=summary.research_report,
            mode="local",
            template="academic_review",
        )

        self.assertTrue((self.project / "report" / "literature_summary.md").is_file())
        self.assertTrue((self.project / "report" / "claim_ledger.json").is_file())
        self.assertFalse((self.project / "audit" / "verification_report.md").exists())

        verification = generate_verification_bundle(summary.claim_ledger, [paper], [note])
        self.view._save_verification_bundle(
            verification,
            report_text=summary.research_report,
            claims=summary.claim_ledger,
            mode="local",
        )

        self.assertTrue((self.project / "audit" / "verification_report.md").is_file())
        self.assertTrue((self.project / "audit" / "audit_state.json").is_file())
        self.assertTrue((self.project / "report" / "literature_summary.md").is_file())
        from review_writer.report_lifecycle import AuditState, audit_matches_current

        store = WorkflowStore(self.project)
        audit_state = AuditState.from_dict(store.load_json("audit/audit_state.json"))
        ledger = store.load_json("report/claim_ledger.json")
        self.assertTrue(audit_matches_current(audit_state, summary.research_report, ledger))

    def test_audit_task_failure_does_not_regenerate_or_remove_summary(self) -> None:
        paper = PaperRecord(
            title="A controlled education study",
            authors=["Example, A."],
            year=2025,
            doi="10.1234/example",
            source="Crossref",
            abstract="This study reports a measured improvement and documents limitations.",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
            access_status="仅发现摘要",
        )
        self.view.papers = [paper]
        self.view.reading_notes = [read_paper_deterministically(paper, core_questions=self.brief.core_questions)]
        self.view.strategy = generate_local_strategy(self.brief)

        class Context:
            def progress(self, *_args, **_kwargs):
                return None

        result = self.view._execute_report_task(
            Context(), {"mode": "local", "template": "academic_review"}
        )
        summary_path = self.project / "report" / "literature_summary.md"
        before = summary_path.read_text(encoding="utf-8")
        self.assertNotIn("claim_citation_audit", result)
        self.assertFalse((self.project / "audit" / "verification_report.md").exists())

        with patch("review_writer.reporting.generate_verification_bundle", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                self.view._execute_audit_task(Context(), {"mode": "local"})

        self.assertEqual(summary_path.read_text(encoding="utf-8"), before)
        self.assertFalse((self.project / "audit" / "verification_report.md").exists())

    def test_resume_restores_papers_core_selection_and_abstract_boundary(self) -> None:
        paper = PaperRecord(
            title="An abstract-only study",
            authors=["Example, A."],
            year=2025,
            doi="10.1234/example",
            source="Crossref",
            abstract="This study reports a measured improvement and notes limitations.",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
            access_status="仅发现摘要",
        )
        note = read_paper_deterministically(paper, core_questions=self.brief.core_questions)
        store = WorkflowStore(self.project)
        store.save_json("papers_data", "search/papers.json", [paper.to_dict()])
        store.save_json("reading_notes_index", "reading_notes/index.json", [note.to_dict()])
        store.set_paper_selection(
            selected_paper_ids=[paper.record_id],
            core_paper_ids=[paper.record_id],
        )

        self.view.set_project(
            brief=self.brief,
            confirmed_plan="# 已确认计划",
            project_directory=self.project,
        )

        self.assertEqual(len(self.view.papers), 1)
        self.assertEqual(self.view.core_paper_ids, [paper.record_id])
        self.assertEqual(self.view.reading_notes[0].evidence_level, EvidenceLevel.ABSTRACT_ONLY)
        values = self.view.paper_tree.item(paper.record_id, "values")
        self.assertIn("仅摘要证据", values)


if __name__ == "__main__":
    unittest.main()
