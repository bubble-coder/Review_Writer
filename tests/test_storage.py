from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from review_writer.models import ResearchBrief
from review_writer.storage import load_project, safe_folder_name, save_project


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = ResearchBrief(
            topic="AI/医学影像：综述?",
            objectives="梳理证据",
            core_questions=["效果如何？"],
            start_year=2020,
            end_year=2026,
            delivery_format="Markdown 综合报告",
            delivery_requirements="中文",
        )

    def test_safe_folder_name_removes_windows_reserved_characters(self) -> None:
        value = safe_folder_name(self.brief.topic)

        self.assertNotIn("/", value)
        self.assertNotIn(":", value)
        self.assertNotIn("?", value)
        self.assertIn("医学影像", value)

    def test_saves_utf8_project_and_updates_same_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved_at = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)

            directory = save_project(
                brief=self.brief,
                plan_text="# 初始计划",
                status="draft",
                output_root=root,
                saved_at=saved_at,
            )
            updated_directory = save_project(
                brief=self.brief,
                plan_text="# 已确认计划",
                status="confirmed",
                output_root=root,
                project_directory=directory,
                saved_at=saved_at,
            )

            self.assertEqual(updated_directory, directory)
            payload = json.loads((directory / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "confirmed")
            self.assertEqual(payload["brief"]["topic"], self.brief.topic)
            self.assertEqual(
                (directory / "research_plan.md").read_text(encoding="utf-8"),
                "# 已确认计划\n",
            )

    def test_rejects_empty_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "调研计划不能为空"):
                save_project(
                    brief=self.brief,
                    plan_text="   ",
                    status="draft",
                    output_root=Path(temporary),
                )

    def test_loads_saved_project_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = save_project(
                brief=self.brief,
                plan_text="# 计划",
                status="confirmed",
                output_root=Path(temporary),
            )

            brief, plan, status, manifest = load_project(directory)

            self.assertEqual(brief.topic, self.brief.topic)
            self.assertIn("# 计划", plan)
            self.assertEqual(status, "confirmed")
            self.assertEqual(manifest["schema_version"], 3)


if __name__ == "__main__":
    unittest.main()
