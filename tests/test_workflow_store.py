import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from review_writer.workflow_store import WorkflowStore


class WorkflowStoreTests(unittest.TestCase):
    def test_upgrades_planner_project_and_preserves_brief(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project.json").write_text(
                json.dumps({"schema_version": 1, "brief": {"topic": "测试"}}),
                encoding="utf-8",
            )

            store = WorkflowStore(root)
            manifest = store.initialize()

            self.assertEqual(manifest["schema_version"], 4)
            self.assertEqual(store.project_brief()["topic"], "测试")
            self.assertTrue((root / "reading_notes").is_dir())
            self.assertTrue((root / "audit").is_dir())
            self.assertEqual(manifest["workflow"]["stages"]["audit"], "locked")

    def test_migrates_legacy_audit_status_from_report_and_artifact(self) -> None:
        for report_status in ("complete", "warning"):
            with self.subTest(report_status=report_status), TemporaryDirectory() as directory:
                root = Path(directory)
                legacy_audit = root / "report" / "claim_citation_audit.md"
                legacy_audit.parent.mkdir(parents=True)
                legacy_audit.write_text("legacy audit", encoding="utf-8")
                (root / "project.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 3,
                            "files": {
                                "claim_citation_audit": "report/claim_citation_audit.md"
                            },
                            "workflow": {
                                "current_stage": "report",
                                "stages": {
                                    "strategy": "complete",
                                    "search": "complete",
                                    "reading": "complete",
                                    "report": report_status,
                                },
                                "legacy_value": {"preserve": True},
                            },
                        }
                    ),
                    encoding="utf-8",
                )

                manifest = WorkflowStore(root).initialize()

                self.assertEqual(manifest["schema_version"], 4)
                self.assertEqual(
                    manifest["workflow"]["stages"]["audit"], report_status
                )
                self.assertEqual(manifest["workflow"]["current_stage"], "audit")
                self.assertEqual(
                    manifest["workflow"]["legacy_value"], {"preserve": True}
                )
                self.assertEqual(legacy_audit.read_text(encoding="utf-8"), "legacy audit")
                self.assertEqual(manifest["migrations"][-1]["strategy"], "additive")

    def test_completed_legacy_report_without_audit_is_pending_verification(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "workflow": {
                            "current_stage": "report",
                            "stages": {"report": "complete"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            manifest = WorkflowStore(root).initialize()

            self.assertEqual(manifest["workflow"]["stages"]["audit"], "pending")
            self.assertEqual(manifest["workflow"]["current_stage"], "audit")

    def test_preserves_explicit_audit_stage_during_additive_migration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "workflow": {
                            "current_stage": "audit",
                            "stages": {
                                "report": "complete",
                                "audit": "in_progress",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            manifest = WorkflowStore(root).initialize()

            self.assertEqual(
                manifest["workflow"]["stages"]["audit"], "in_progress"
            )
            self.assertEqual(manifest["workflow"]["current_stage"], "audit")

    def test_artifacts_are_registered_and_stage_unlocks(self) -> None:
        with TemporaryDirectory() as directory:
            store = WorkflowStore(Path(directory))
            store.initialize()
            store.save_json("papers", "search/papers.json", [{"id": "P001"}])
            manifest = store.set_stage(
                "strategy", "complete", next_stage="search", message="confirmed"
            )

            saved = store.load_manifest()
            self.assertEqual(saved["files"]["papers"], "search/papers.json")
            self.assertEqual(manifest["workflow"]["stages"]["search"], "pending")

    def test_rejects_paths_outside_project(self) -> None:
        with TemporaryDirectory() as directory:
            store = WorkflowStore(Path(directory))
            with self.assertRaises(ValueError):
                store.path_for("../outside.json")

    def test_persists_selected_and_core_papers(self) -> None:
        with TemporaryDirectory() as directory:
            store = WorkflowStore(Path(directory))
            store.initialize()
            manifest = store.set_paper_selection(
                selected_paper_ids=["P1", "P2", "P1"],
                core_paper_ids=["P2"],
            )

            self.assertEqual(manifest["workflow"]["selected_paper_ids"], ["P1", "P2"])
            self.assertEqual(manifest["workflow"]["core_paper_ids"], ["P2"])


if __name__ == "__main__":
    unittest.main()
