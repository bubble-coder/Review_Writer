import json
import unittest

from review_writer.models import ResearchBrief
from review_writer.search_strategy import (
    SearchStrategyError,
    build_source_queries,
    generate_agent_strategy,
    generate_local_strategy,
    render_keyword_tree_markdown,
    render_search_strategies_markdown,
)


def _brief() -> ResearchBrief:
    return ResearchBrief(
        topic="Explainable AI (XAI)",
        objectives="Compare clinical validation methods",
        core_questions=[
            "Which validation methods are used?",
            "How are safety outcomes reported?",
        ],
        start_year=2020,
        end_year=2026,
        delivery_format="Markdown",
        delivery_requirements="Chinese report",
        scope_notes="Medical imaging",
    )


class FakeClient:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls = 0

    def request_text(self, **kwargs: object) -> str:
        self.calls += 1
        self.kwargs = kwargs
        return json.dumps(self.payload, ensure_ascii=False)


class SearchStrategyTests(unittest.TestCase):
    def test_local_strategy_preserves_contract_and_has_broad_and_precision_queries(self) -> None:
        brief = _brief()

        bundle = generate_local_strategy(brief)

        self.assertEqual(bundle.topic, brief.topic)
        self.assertEqual(bundle.core_questions, brief.core_questions)
        self.assertGreaterEqual(len(bundle.broad_queries), 2)
        self.assertEqual(len(bundle.precision_queries), len(brief.core_questions))
        self.assertIn("XAI", bundle.keyword_tree.all_terms())
        self.assertIn("pubmed", bundle.source_queries)
        self.assertIn("2020", bundle.source_queries["pubmed"]["broad"][0])

    def test_markdown_renders_questions_and_generation_mode(self) -> None:
        bundle = generate_local_strategy(_brief())

        tree = render_keyword_tree_markdown(bundle)
        queries = render_search_strategies_markdown(bundle)

        self.assertIn("Which validation methods are used?", tree)
        self.assertIn("宽检索（召回）", queries)
        self.assertIn("精检索（高相关筛选）", queries)
        self.assertIn("本地规则", queries)

    def test_source_queries_can_be_resynced_after_user_edit(self) -> None:
        sources = build_source_queries(
            ["edited broad"],
            ["edited precise"],
            start_year=2021,
            end_year=2025,
        )

        self.assertEqual(sources["crossref"]["broad"], ["edited broad"])
        self.assertIn("2021", sources["pubmed"]["precision"][0])

    def test_agent_strategy_calls_client_once_but_preserves_user_questions(self) -> None:
        client = FakeClient(
            {
                "keyword_tree": {
                    "term": "Explainable AI",
                    "synonyms": ["XAI"],
                    "related_terms": [],
                    "children": [],
                },
                "broad_queries": ["XAI OR explainability"],
                "precision_queries": ["XAI AND clinical validation"],
                "notes": ["Confirm controlled vocabulary"],
            }
        )

        bundle = generate_agent_strategy(_brief(), client)  # type: ignore[arg-type]

        self.assertEqual(client.calls, 1)
        self.assertEqual(bundle.core_questions, _brief().core_questions)
        self.assertEqual(bundle.generation_mode, "agent")
        self.assertIn("openalex", bundle.source_queries)

    def test_agent_strategy_rejects_missing_precision_queries(self) -> None:
        client = FakeClient(
            {
                "keyword_tree": {"term": "XAI"},
                "broad_queries": ["XAI"],
                "precision_queries": [],
            }
        )

        with self.assertRaises(SearchStrategyError):
            generate_agent_strategy(_brief(), client)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
