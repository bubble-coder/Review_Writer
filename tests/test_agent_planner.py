from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from review_writer.generators.agent_planner import generate_agent_plan
from review_writer.generators.llm_client import LLMRequestError
from review_writer.models import ResearchBrief
from review_writer.settings import ModelSettings


class AgentPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = ResearchBrief(
            topic="生成式人工智能教育应用",
            objectives="梳理应用场景并评估风险",
            core_questions=["学习效果如何？", "有哪些伦理风险？"],
            start_year=2020,
            end_year=2026,
            delivery_format="Markdown 综合报告",
            delivery_requirements="中文，包含证据表",
            generation_mode="agent",
        )
        self.settings = ModelSettings(
            provider_name="测试模型",
            model="test-model",
            api_base="https://example.com/v1",
            protocol="openai_compatible",
        )
        self.payload = {
            "research_scope": "高等教育中的教学与评估场景",
            "inclusion_criteria": ["同行评议研究"],
            "exclusion_criteria": ["无来源材料"],
            "workflow": [{"stage": "检索", "task": "构建检索式", "output": "文献清单"}],
            "screening_priorities": ["直接回答核心问题"],
            "reading_fields": ["研究设计", "主要结果"],
            "quality_controls": ["逐条核验论断与引文"],
            "deliverables": ["Markdown 报告", "证据表"],
            "risks": ["全文权限不足"],
        }

    @patch("review_writer.generators.agent_planner.LLMClient.request_text")
    def test_generates_valid_markdown_from_structured_json(self, request_text) -> None:
        request_text.return_value = json.dumps(self.payload, ensure_ascii=False)

        plan = generate_agent_plan(
            self.brief,
            settings=self.settings,
            api_key="secret",
            generated_at=datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc),
        )

        self.assertIn("大模型 Agent（测试模型 / test-model）", plan)
        self.assertIn("1. 学习效果如何？", plan)
        self.assertIn("2. 有哪些伦理风险？", plan)
        self.assertIn("IMA 等知识库检索片段", plan)
        self.assertIn("逐条核验论断与引文", plan)

    @patch("review_writer.generators.agent_planner.LLMClient.request_text")
    def test_rejects_missing_required_fields(self, request_text) -> None:
        request_text.return_value = '{"research_scope": "only one field"}'

        with self.assertRaisesRegex(LLMRequestError, "缺少字段"):
            generate_agent_plan(self.brief, settings=self.settings, api_key="secret")


if __name__ == "__main__":
    unittest.main()
