from datetime import date
import unittest

from review_writer.models import ResearchBrief, parse_multiline_items


class ParseMultilineItemsTests(unittest.TestCase):
    def test_removes_common_bullet_and_number_prefixes(self) -> None:
        value = "- 问题一\n2. 问题二\n3、问题三\n\n• 问题四"

        self.assertEqual(
            parse_multiline_items(value),
            ["问题一", "问题二", "问题三", "问题四"],
        )


class ResearchBriefTests(unittest.TestCase):
    def test_builds_valid_brief_from_form_values(self) -> None:
        brief = ResearchBrief.from_form(
            topic="  可解释人工智能  ",
            objectives="梳理临床应用证据",
            core_questions="- 有哪些主要方法？\n- 如何验证？",
            start_year="2020",
            end_year=str(date.today().year),
            delivery_format="Markdown 综合报告",
            delivery_requirements="中文，约 5000 字",
            scope_notes="仅纳入医学影像研究",
        )

        self.assertEqual(brief.topic, "可解释人工智能")
        self.assertEqual(brief.core_questions, ["有哪些主要方法？", "如何验证？"])
        self.assertEqual(brief.start_year, 2020)

    def test_reports_multiple_validation_errors_together(self) -> None:
        with self.assertRaises(ValueError) as context:
            ResearchBrief.from_form(
                topic="",
                objectives="",
                core_questions="",
                start_year="later",
                end_year="1200",
                delivery_format="",
                delivery_requirements="",
            )

        message = str(context.exception)
        self.assertIn("请填写调研主题", message)
        self.assertIn("请至少填写一个核心问题", message)
        self.assertIn("文献起始年份必须是四位数字", message)

    def test_rejects_reversed_year_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "起始年份不能晚于结束年份"):
            ResearchBrief.from_form(
                topic="主题",
                objectives="目标",
                core_questions="问题",
                start_year="2025",
                end_year="2020",
                delivery_format="Markdown",
                delivery_requirements="中文",
            )

    def test_restores_brief_from_project_manifest(self) -> None:
        brief = ResearchBrief.from_dict(
            {
                "topic": "研究主题",
                "objectives": "研究目标",
                "core_questions": ["问题一"],
                "start_year": 2020,
                "end_year": 2026,
                "delivery_format": "Markdown 综合报告",
                "delivery_requirements": "中文",
            }
        )

        self.assertEqual(brief.topic, "研究主题")
        self.assertEqual(brief.core_questions, ["问题一"])


if __name__ == "__main__":
    unittest.main()
