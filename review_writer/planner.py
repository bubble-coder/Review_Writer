"""Deterministic research-plan generation for the first MVP."""

from __future__ import annotations

from datetime import datetime

from .models import ResearchBrief


def _as_markdown_lines(value: str, fallback: str = "无补充说明") -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return f"- {fallback}"
    return "\n".join(f"- {line}" for line in lines)


def generate_research_plan(
    brief: ResearchBrief,
    *,
    generated_at: datetime | None = None,
) -> str:
    """Generate an editable Markdown plan from a validated research brief."""

    generated_at = generated_at or datetime.now().astimezone()
    questions = "\n".join(
        f"{index}. {question}"
        for index, question in enumerate(brief.core_questions, start=1)
    )
    objectives = _as_markdown_lines(brief.objectives)
    scope_notes = _as_markdown_lines(brief.scope_notes)
    delivery_requirements = _as_markdown_lines(brief.delivery_requirements)

    return f"""# {brief.topic}：文献调研计划

> 状态：待确认  
> 生成时间：{generated_at:%Y-%m-%d %H:%M %Z}
> 生成方式：本地规则模板

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

最终报告应逐条回应上述问题；无法获得充分证据的问题将明确标记证据缺口，不能用推测补足。

## 3. 范围与边界

### 用户补充的范围说明

{scope_notes}

### 默认纳入范围

- 发表年份位于 {brief.start_year}—{brief.end_year} 的学术文献。
- 研究内容直接回答至少一个核心问题。
- 优先纳入同行评议论文、系统综述、权威指南及高质量会议论文。
- 同时记录支持、反驳和不确定性证据，避免只保留单一方向的结论。

### 默认排除范围

- 与调研主题仅有词面重合、但不回答核心问题的文献。
- 无法识别来源、作者或发表时间的材料。
- 重复记录、撤稿文献及无可核查出处的二手转述。

## 4. 执行路径

1. **问题拆解**：把每个核心问题拆成研究对象、干预/因素、结局、场景和研究类型。
2. **检索设计**：生成关键词树、同义词和受控词，并构造宽检索式与精检索式。
3. **文献召回**：调用已配置的数据库接口，保存检索式、数据库、时间和结果数量。
4. **去重与筛选**：按 DOI、题名和作者信息去重，再执行标题/摘要筛选和全文筛选。
5. **结构化精读**：对核心论文提取研究问题、方法、样本、主要结果、限制和可支持论断。
6. **证据分层**：未获得全文的文献显式标记为“仅摘要证据”，不得用于支撑全文层面的细节。
7. **综合写作**：围绕核心问题综合共识、分歧、证据强度和研究空白。
8. **引用核验**：逐条检查论断与引文是否匹配，并核验 DOI、题名、作者和原文链接。

## 5. 筛选与优先级规则

### 高优先级

- 直接回答核心问题，且研究设计与目标高度匹配。
- 样本、方法、指标和统计过程描述充分。
- 来自权威期刊、数据库或机构，且可获得全文。
- 被多项独立研究验证，或能解释关键争议。

### 需要降级或单独标注

- 只有摘要、新闻稿或数据库摘要可用：标记为“仅摘要证据”。
- 预印本、非同行评议材料或小样本探索性研究：标记证据状态。
- 结论超出研究设计、样本或统计结果所能支持的范围：记录风险，不直接采纳。

## 6. 核心论文精读字段

每篇核心论文至少记录：

- DOI、题名、作者、年份、期刊和原文链接；
- 全文获取状态（全文 / 仅摘要证据 / 未获取）；
- 研究问题、研究设计、样本与数据来源；
- 方法、关键变量、主要结果和效应方向；
- 作者结论、研究局限、潜在偏倚；
- 可支持的具体论断，以及对应的页码、图表或段落位置；
- 与哪些核心问题相关，以及纳入或排除理由。

## 7. 质量控制与引用核验

- 每个关键论断至少关联一个可定位的证据来源。
- 区分“论文原文结论”“报告作者综合判断”和“待验证假设”。
- 不使用“仅摘要证据”支撑摘要中未呈现的方法或结果细节。
- DOI、题名、作者、年份与原文链接至少进行一次交叉核验。
- 发现论文互相冲突时，比较研究设计、样本、测量方法和适用边界，不进行简单多数表决。
- 最终输出论断—引文核验表，记录“匹配 / 部分匹配 / 不匹配 / 待核验”。

## 8. 预计交付物

1. 已确认的调研计划。
2. 关键词树、宽检索式和精检索式。
3. 文献清单 Markdown（含 DOI、题名、原文链接、获取状态和筛选状态）。
4. 核心论文结构化精读记录。
5. {brief.delivery_format}。
6. 论断—引文逐条核验表与证据缺口清单。

## 9. 用户确认清单

- [ ] 调研主题、目标和时间范围准确。
- [ ] 核心问题完整，且顺序符合优先级。
- [ ] 纳入/排除边界合理。
- [ ] 交付形式和要求明确可执行。
- [ ] 对“仅摘要证据”的使用限制已接受。
- [ ] 可以进入关键词树与检索式设计阶段。
"""


def mark_plan_confirmed(plan_text: str) -> str:
    """Mark an edited plan as confirmed without discarding user changes."""

    pending_marker = "> 状态：待确认"
    confirmed_marker = "> 状态：已确认"
    if pending_marker in plan_text:
        return plan_text.replace(pending_marker, confirmed_marker, 1)
    if confirmed_marker in plan_text:
        return plan_text
    return f"> 状态：已确认\n\n{plan_text.lstrip()}"
