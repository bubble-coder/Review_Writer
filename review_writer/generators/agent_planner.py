"""Structured LLM Agent research-plan generation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
import json
from typing import Any

from ..models import ResearchBrief
from ..settings import ModelSettings
from .llm_client import LLMClient, LLMRequestError


REQUIRED_KEYS = (
    "research_scope",
    "inclusion_criteria",
    "exclusion_criteria",
    "workflow",
    "screening_priorities",
    "reading_fields",
    "quality_controls",
    "deliverables",
    "risks",
)


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise LLMRequestError("大模型没有返回可解析的 JSON 调研计划。")
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise LLMRequestError(f"大模型返回的 JSON 无效：{error.msg}") from error
    if not isinstance(payload, dict):
        raise LLMRequestError("大模型返回的调研计划不是 JSON 对象。")
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise LLMRequestError(f"大模型调研计划缺少字段：{', '.join(missing)}")
    return payload


def _items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line.strip(" -*•\t") for line in value.splitlines() if line.strip(" -*•\t")]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            parts = [str(item.get(key, "")).strip() for key in ("stage", "task", "output")]
            text = " — ".join(part for part in parts if part)
            if text:
                result.append(text)
    return result


def _bullets(value: Any, fallback: str) -> str:
    values = _items(value)
    if not values:
        values = [fallback]
    return "\n".join(f"- {item}" for item in values)


def _numbered(value: Any, fallback: str) -> str:
    values = _items(value)
    if not values:
        values = [fallback]
    return "\n".join(f"{index}. {item}" for index, item in enumerate(values, 1))


def _brief_prompt(brief: ResearchBrief) -> str:
    contract = json.dumps(brief.to_dict(), ensure_ascii=False, indent=2)
    return f"""根据以下已经由用户确认的调研需求，生成文献调研执行计划。

用户需求：
{contract}

只能输出一个 JSON 对象，不要输出 Markdown 或解释。必须包含以下字段：
{{
  "research_scope": "具体范围和边界说明",
  "inclusion_criteria": ["纳入标准"],
  "exclusion_criteria": ["排除标准"],
  "workflow": [{{"stage": "阶段", "task": "任务", "output": "产物"}}],
  "screening_priorities": ["筛选与优先级规则"],
  "reading_fields": ["核心论文精读字段"],
  "quality_controls": ["质量控制与引用核验规则"],
  "deliverables": ["交付物"],
  "risks": ["风险、限制或待确认事项"]
}}

约束：
1. 必须围绕用户给出的每个核心问题组织计划。
2. 必须区分全文证据、仅摘要证据和知识库片段证据。
3. 不得虚构数据库权限、文献数量、完成日期或已经获得的论文。
4. 必须包含关键词树、宽检索、精检索、去重筛选、精读、综合写作和逐条引用核验。
5. 输出必须是严格 JSON。"""


def _render_plan(
    brief: ResearchBrief,
    payload: dict[str, Any],
    settings: ModelSettings,
    generated_at: datetime,
) -> str:
    questions = "\n".join(
        f"{index}. {question}" for index, question in enumerate(brief.core_questions, 1)
    )
    objectives = _bullets(brief.objectives, "按用户目标完成调研")
    delivery_requirements = _bullets(brief.delivery_requirements, "按用户要求交付")
    scope = str(payload.get("research_scope") or brief.scope_notes or "按核心问题界定范围")
    return f"""# {brief.topic}：文献调研计划

> 状态：待确认  
> 生成时间：{generated_at:%Y-%m-%d %H:%M %Z}  
> 生成方式：大模型 Agent（{settings.provider_name} / {settings.model}）

## 1. 调研任务定义

| 项目 | 内容 |
| --- | --- |
| 调研主题 | {brief.topic} |
| 文献时间范围 | {brief.start_year}—{brief.end_year} |
| 交付形式 | {brief.delivery_format} |

### 调研目标

{objectives}

### 交付要求

{delivery_requirements}

## 2. 核心问题

{questions}

## 3. 范围与边界

{scope}

### 纳入标准

{_bullets(payload.get('inclusion_criteria'), '直接回应至少一个核心问题')}

### 排除标准

{_bullets(payload.get('exclusion_criteria'), '与核心问题无关或来源不可核查')}

## 4. 执行路径

{_numbered(payload.get('workflow'), '设计检索、筛选、精读、综合与核验流程')}

## 5. 筛选与优先级规则

{_bullets(payload.get('screening_priorities'), '优先纳入直接相关且方法透明的研究')}

### 证据状态强制标记

- 获得并核验全文：标记为“全文证据”。
- 仅获得摘要：标记为“仅摘要证据”，不得支撑摘要未呈现的细节。
- 仅获得 IMA 等知识库检索片段：标记为“知识库片段证据”，不能替代原始论文。

## 6. 核心论文精读字段

{_bullets(payload.get('reading_fields'), '题名、DOI、方法、结果、限制与可支持论断')}

## 7. 质量控制与引用核验

{_bullets(payload.get('quality_controls'), '逐条核验论断与引文是否匹配')}

## 8. 预计交付物

{_numbered(payload.get('deliverables'), brief.delivery_format)}

## 9. 风险与待确认事项

{_bullets(payload.get('risks'), '数据库权限和全文可用性需在执行阶段确认')}

## 10. 用户确认清单

- [ ] 调研主题、目标、时间范围和核心问题准确。
- [ ] 纳入与排除标准合理。
- [ ] 交付形式和证据等级规则可接受。
- [ ] 可以进入关键词树和检索式设计阶段。
"""


def generate_agent_plan(
    brief: ResearchBrief,
    *,
    settings: ModelSettings,
    api_key: str | None,
    generated_at: datetime | None = None,
    audit_callback: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Call the configured model and render its validated JSON as Markdown."""

    client = LLMClient(settings, api_key, audit_callback=audit_callback)
    raw = client.request_text(
        system_prompt=(
            "你是严谨的文献调研规划 Agent。你只生成可执行、可核验的计划，"
            "不假装已经检索或读过任何文献。"
        ),
        user_prompt=_brief_prompt(brief),
        json_mode=True,
    )
    payload = _extract_json_object(raw)
    return _render_plan(
        brief,
        payload,
        settings,
        generated_at or datetime.now().astimezone(),
    )
