from datetime import datetime, timezone
import unittest

from review_writer.models import ResearchBrief
from review_writer.planner import generate_research_plan, mark_plan_confirmed


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = ResearchBrief(
            topic="生成式人工智能教育应用",
            objectives="识别有效场景\n评估风险",
            core_questions=["学习效果如何？", "有哪些伦理风险？"],
            start_year=2021,
            end_year=2026,
            delivery_format="Markdown 综合报告",
            delivery_requirements="中文\n包含证据表",
            scope_notes="高等教育场景",
        )

    def test_plan_contains_user_contract_and_evidence_rules(self) -> None:
        plan = generate_research_plan(
            self.brief,
            generated_at=datetime(2026, 7, 12, 8, 30, tzinfo=timezone.utc),
        )

        self.assertIn("# 生成式人工智能教育应用：文献调研计划", plan)
        self.assertIn("2021—2026", plan)
        self.assertIn("1. 学习效果如何？", plan)
        self.assertIn("2. 有哪些伦理风险？", plan)
        self.assertIn("仅摘要证据", plan)
        self.assertIn("论断—引文逐条核验表", plan)
        self.assertIn("高等教育场景", plan)

    def test_confirmation_keeps_user_edits(self) -> None:
        plan = "> 状态：待确认\n\n用户新增内容"

        confirmed = mark_plan_confirmed(plan)

        self.assertIn("> 状态：已确认", confirmed)
        self.assertIn("用户新增内容", confirmed)
        self.assertNotIn("待确认", confirmed)

    def test_confirmation_adds_marker_if_user_removed_it(self) -> None:
        self.assertTrue(mark_plan_confirmed("# 自定义计划").startswith("> 状态：已确认"))


if __name__ == "__main__":
    unittest.main()
