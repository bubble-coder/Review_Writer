"""Delivery templates layered over the evidence-bound summary draft."""

from __future__ import annotations

from enum import Enum


class ReportTemplate(str, Enum):
    ACADEMIC_REVIEW = "academic_review"
    GRANT_PROPOSAL = "grant_proposal"
    INDUSTRY_RESEARCH = "industry_research"

    @property
    def label(self) -> str:
        return {
            self.ACADEMIC_REVIEW: "学术综述",
            self.GRANT_PROPOSAL: "课题申报",
            self.INDUSTRY_RESEARCH: "行业调研",
        }[self]


def apply_report_template(markdown: str, template: ReportTemplate | str) -> str:
    template = ReportTemplate(template)
    if template is ReportTemplate.ACADEMIC_REVIEW:
        return markdown
    if template is ReportTemplate.GRANT_PROPOSAL:
        note = (
            "> 交付模板：课题申报。以下证据综述用于支撑立项依据、研究现状与研究空白；"
            "拟解决问题、创新点和技术路线仍须由申报人基于本报告审定。\n\n"
        )
        replacements = {
            "## 1. 调研范围与方法": "## 1. 立项依据与调研方法",
            "## 2. 证据概览": "## 2. 国内外研究现状与证据基础",
            "## 3. 核心问题与证据综合": "## 3. 关键科学问题与研究空白",
            "## 4. 局限性": "## 4. 现有证据局限与项目风险",
        }
    else:
        note = (
            "> 交付模板：行业调研。以下结论严格继承证据等级；独立核验状态见核验报告；"
            "市场规模、商业预测和竞争判断若无论文证据，不在本报告中自动生成。\n\n"
        )
        replacements = {
            "## 1. 调研范围与方法": "## 1. 行业问题、范围与方法",
            "## 2. 证据概览": "## 2. 技术与证据版图",
            "## 3. 核心问题与证据综合": "## 3. 核心发现、机会与风险",
            "## 4. 局限性": "## 4. 数据边界与决策风险",
        }
    result = markdown
    for source, target in replacements.items():
        result = result.replace(source, target)
    return note + result
