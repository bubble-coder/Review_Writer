"""Keyword-tree and search-expression generation.

Local generation is deterministic and never performs network I/O.  Agent
generation makes exactly one explicit call through an already configured
``LLMClient`` and validates the returned JSON before it crosses into the
workflow model.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .generators.llm_client import LLMClient, LLMRequestError
from .models import ResearchBrief
from .workflow_models import KeywordNode, SearchStrategyBundle


class SearchStrategyError(ValueError):
    """Raised when a strategy cannot be generated or validated."""


_ENGLISH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "is", "of", "on", "or", "the", "to", "what", "which",
    "with", "within", "whether", "why", "study", "research",
}
_CHINESE_FILLERS = (
    "有哪些", "是什么", "如何", "是否", "为什么", "哪些", "目前", "主要",
    "研究", "文献", "情况", "问题", "影响", "作用", "方面", "对于", "关于",
    "以及", "及其", "与", "的", "了", "吗", "呢",
)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n,;，；。?？!！")
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _quote(term: str) -> str:
    return '"' + term.replace('"', " ").strip() + '"'


def _alternates(text: str) -> list[str]:
    """Extract only user-supplied aliases; local rules never invent synonyms."""

    candidates: list[str] = []
    candidates.extend(re.findall(r"[（(]([^()（）]{2,80})[）)]", text))
    candidates.extend(re.split(r"\s*(?:/|／|\||、)\s*", text))
    return [item for item in _unique(candidates) if item.casefold() != text.casefold()]


def _salient_phrases(text: str, *, maximum: int = 5) -> list[str]:
    """Extract conservative phrases without pretending to perform NLP."""

    text = re.sub(r"[（(][^()（）]+[）)]", " ", str(text))
    phrases: list[str] = []
    for segment in re.split(r"[\n,，;；。!?！？:：]+", text):
        segment = segment.strip()
        if not segment:
            continue
        english = [
            token
            for token in re.findall(r"[A-Za-z][A-Za-z0-9+_.-]{2,}", segment)
            if token.casefold() not in _ENGLISH_STOPWORDS
        ]
        phrases.extend(english)
        chinese = "".join(re.findall(r"[\u3400-\u9fff]+", segment))
        for filler in _CHINESE_FILLERS:
            chinese = chinese.replace(filler, " ")
        phrases.extend(part for part in chinese.split() if len(part) >= 2)
        if not english and not chinese and len(segment) >= 2:
            phrases.append(segment)
    return _unique(phrases)[:maximum]


def _or_group(terms: list[str]) -> str:
    values = _unique(terms)
    if not values:
        return ""
    return "(" + " OR ".join(_quote(term) for term in values) + ")"


def _and_query(groups: list[list[str]]) -> str:
    return " AND ".join(group for terms in groups if (group := _or_group(terms)))


def _source_queries(
    broad: list[str],
    precision: list[str],
    *,
    start_year: int | None,
    end_year: int | None,
) -> dict[str, dict[str, list[str]]]:
    # OpenAlex and Crossref use year filters as API parameters rather than
    # embedding provider-specific syntax in the query string.
    pubmed_year = (
        f'("{start_year}"[Date - Publication] : "{end_year}"[Date - Publication])'
        if start_year is not None and end_year is not None
        else ""
    )
    pubmed_broad = [f"{query} AND {pubmed_year}" if pubmed_year else query for query in broad]
    pubmed_precision = [
        f"{query} AND {pubmed_year}" if pubmed_year else query for query in precision
    ]
    return {
        "openalex": {"broad": list(broad), "precision": list(precision)},
        "crossref": {"broad": list(broad), "precision": list(precision)},
        "zotero": {"broad": list(broad), "precision": list(precision)},
        "ima": {"broad": list(broad), "precision": list(precision)},
        "pubmed": {
            "broad": pubmed_broad,
            "precision": pubmed_precision,
        },
    }


def build_source_queries(
    broad_queries: list[str],
    precision_queries: list[str],
    *,
    start_year: int | None,
    end_year: int | None,
) -> dict[str, dict[str, list[str]]]:
    """Regenerate provider variants after a user edits the general queries."""

    broad = _unique(broad_queries)
    precision = _unique(precision_queries)
    if not broad or not precision:
        raise SearchStrategyError("宽检索和精检索都必须至少包含一条检索式。")
    return _source_queries(
        broad,
        precision,
        start_year=int(start_year) if start_year is not None else None,
        end_year=int(end_year) if end_year is not None else None,
    )


def generate_local_strategy(brief: ResearchBrief) -> SearchStrategyBundle:
    """Build an editable keyword tree and recall/precision queries locally."""

    if not brief.topic.strip():
        raise SearchStrategyError("调研主题不能为空。")
    if not brief.core_questions:
        raise SearchStrategyError("至少需要一个核心问题才能生成检索式。")

    topic_aliases = _alternates(brief.topic)
    objective_terms = _salient_phrases(brief.objectives)
    scope_terms = _salient_phrases(brief.scope_notes)
    question_nodes: list[KeywordNode] = []
    question_terms: list[list[str]] = []
    for question in brief.core_questions:
        terms = _salient_phrases(question)
        if not terms:
            terms = [question]
        question_terms.append(terms)
        question_nodes.append(
            KeywordNode(
                term=terms[0],
                related_terms=terms[1:],
                note=f"来源于核心问题：{question}",
            )
        )

    children = [
        KeywordNode(
            term=brief.topic,
            synonyms=topic_aliases,
            note="主题概念；同义词仅来自用户原文中的括号或分隔表达。",
        ),
        KeywordNode(
            term="调研目标",
            related_terms=objective_terms,
            note=brief.objectives,
        ),
        KeywordNode(
            term="核心问题",
            children=question_nodes,
            note="逐条保留用户确认的核心问题。",
        ),
    ]
    if scope_terms:
        children.append(KeywordNode(term="范围限定", related_terms=scope_terms, note=brief.scope_notes))
    tree = KeywordNode(term=brief.topic, children=children, note="本地规则关键词树")

    topic_group = [brief.topic, *topic_aliases]
    broad = [_and_query([topic_group])]
    broad.extend(_and_query([topic_group, terms]) for terms in question_terms)

    precision: list[str] = []
    constraints = _unique([*objective_terms[:2], *scope_terms[:2]])
    for terms in question_terms:
        groups = [[brief.topic], terms]
        if constraints:
            groups.append(constraints)
        precision.append(_and_query(groups))
    broad = _unique(broad)
    precision = _unique(precision)

    return SearchStrategyBundle(
        topic=brief.topic,
        core_questions=list(brief.core_questions),
        keyword_tree=tree,
        broad_queries=broad,
        precision_queries=precision,
        source_queries=_source_queries(
            broad,
            precision,
            start_year=brief.start_year,
            end_year=brief.end_year,
        ),
        start_year=brief.start_year,
        end_year=brief.end_year,
        generation_mode="local",
        notes=[
            "本地规则不会自动创造同义词；请在执行检索前人工补充领域术语、缩写和受控词表。",
            "年份通过数据库筛选参数应用；PubMed 版本同时显示日期字段表达式。",
        ],
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:] if lines else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise SearchStrategyError("大模型没有返回可解析的 JSON 检索策略。")
    try:
        value = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise SearchStrategyError(f"大模型返回的检索策略 JSON 无效：{error.msg}") from error
    if not isinstance(value, dict):
        raise SearchStrategyError("大模型返回的检索策略不是 JSON 对象。")
    return value


def _agent_prompt(brief: ResearchBrief) -> str:
    contract = json.dumps(brief.to_dict(), ensure_ascii=False, indent=2)
    return f"""为以下已经由用户确认的文献调研需求生成关键词树和多套检索式：
{contract}

只返回严格 JSON 对象：
{{
  "keyword_tree": {{
    "term": "根概念",
    "synonyms": ["同义词或缩写"],
    "related_terms": ["相关术语"],
    "children": [{{"term": "子概念", "synonyms": [], "related_terms": [], "children": [], "note": "来源"}}],
    "note": "说明"
  }},
  "broad_queries": ["用于高召回的布尔检索式，至少两套"],
  "precision_queries": ["用于高相关筛选的布尔检索式，至少两套"],
  "source_queries": {{
    "openalex": {{"broad": [], "precision": []}},
    "crossref": {{"broad": [], "precision": []}},
    "pubmed": {{"broad": [], "precision": []}}
  }},
  "notes": ["需人工确认的术语或数据库语法"]
}}

要求：覆盖每一个核心问题；不得声称已执行检索；不得虚构受控词表映射；保留用户的年份范围；
宽检索侧重召回，精检索加入明确概念约束；每条检索式必须可直接编辑。"""


def generate_agent_strategy(brief: ResearchBrief, client: LLMClient) -> SearchStrategyBundle:
    """Generate a strategy with an explicit LLM call and strict validation."""

    try:
        raw = client.request_text(
            system_prompt=(
                "你是严谨的学术检索策略设计 Agent。只输出用户要求的 JSON；"
                "不得假装已经访问数据库或验证检索结果。"
            ),
            user_prompt=_agent_prompt(brief),
            json_mode=True,
        )
    except LLMRequestError:
        raise
    payload = _extract_json_object(raw)
    tree = payload.get("keyword_tree")
    if not isinstance(tree, Mapping):
        raise SearchStrategyError("大模型检索策略缺少 keyword_tree 对象。")
    raw_broad = payload.get("broad_queries")
    raw_precision = payload.get("precision_queries")
    raw_notes = payload.get("notes")
    broad = _unique(
        [str(item) for item in raw_broad if isinstance(item, str)]
        if isinstance(raw_broad, list)
        else []
    )
    precision = _unique(
        [str(item) for item in raw_precision if isinstance(item, str)]
        if isinstance(raw_precision, list)
        else []
    )
    if not broad or not precision:
        raise SearchStrategyError("大模型检索策略必须同时包含宽检索式和精检索式。")
    raw_sources = payload.get("source_queries")
    source_queries = dict(raw_sources) if isinstance(raw_sources, Mapping) else {}
    if not source_queries:
        source_queries = _source_queries(
            broad,
            precision,
            start_year=brief.start_year,
            end_year=brief.end_year,
        )
    return SearchStrategyBundle(
        topic=brief.topic,
        core_questions=list(brief.core_questions),
        keyword_tree=KeywordNode.from_dict(tree),
        broad_queries=broad,
        precision_queries=precision,
        source_queries=source_queries,
        start_year=brief.start_year,
        end_year=brief.end_year,
        generation_mode="agent",
        notes=_unique(
            [str(item) for item in raw_notes if isinstance(item, str)]
            if isinstance(raw_notes, list)
            else []
        ),
    )


def render_keyword_tree_markdown(bundle: SearchStrategyBundle) -> str:
    """Render an editable Markdown representation of the keyword tree."""

    lines = [f"# {bundle.topic}：关键词树", "", "> 状态：待用户确认", ""]

    def visit(node: KeywordNode, depth: int) -> None:
        indent = "  " * depth
        lines.append(f"{indent}- **{node.term or '未命名概念'}**")
        if node.synonyms:
            lines.append(f"{indent}  - 同义词/缩写：{'；'.join(node.synonyms)}")
        if node.related_terms:
            lines.append(f"{indent}  - 相关术语：{'；'.join(node.related_terms)}")
        if node.note:
            lines.append(f"{indent}  - 说明：{node.note}")
        for child in node.children:
            visit(child, depth + 1)

    visit(bundle.keyword_tree, 0)
    lines.extend(["", "## 核心问题（原样保留）", ""])
    lines.extend(f"{index}. {question}" for index, question in enumerate(bundle.core_questions, 1))
    return "\n".join(lines).rstrip() + "\n"


def render_search_strategies_markdown(bundle: SearchStrategyBundle) -> str:
    """Render recall and precision expressions with their provenance."""

    lines = [
        f"# {bundle.topic}：检索式",
        "",
        f"> 生成方式：{'大模型 Agent' if bundle.generation_mode == 'agent' else '本地规则'}  ",
        f"> 文献年份：{bundle.start_year or '不限'}—{bundle.end_year or '不限'}  ",
        "> 执行前请确认数据库字段语法与受控词表。",
        "",
        "## 宽检索（召回）",
        "",
    ]
    for index, query in enumerate(bundle.broad_queries, 1):
        lines.extend([f"### W{index}", "", f"```text\n{query}\n```", ""])
    lines.extend(["## 精检索（高相关筛选）", ""])
    for index, query in enumerate(bundle.precision_queries, 1):
        lines.extend([f"### P{index}", "", f"```text\n{query}\n```", ""])
    lines.extend(["## 数据库版本", ""])
    for source, modes in bundle.source_queries.items():
        lines.append(f"### {source}")
        lines.append("")
        for mode in ("broad", "precision"):
            for query in modes.get(mode, []):
                lines.append(f"- `{mode}`：`{query.replace('`', '')}`")
        lines.append("")
    if bundle.notes:
        lines.extend(["## 注意事项", ""])
        lines.extend(f"- {note}" for note in bundle.notes)
    return "\n".join(lines).rstrip() + "\n"
