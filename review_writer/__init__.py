"""Local evidence-traceable literature research and reporting assistant."""

from .models import ResearchBrief
from .planner import generate_research_plan

__all__ = ["ResearchBrief", "generate_research_plan"]
__version__ = "0.7.0"
