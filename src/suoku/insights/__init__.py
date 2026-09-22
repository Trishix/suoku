"""Opt-in structured observations and evidence-grounded Q&A."""

from .engine import InsightEngine
from .models import Citation, InsightAnswer, InsightProvider, InsightRecipe, Observation

__all__ = ["InsightEngine", "InsightRecipe", "InsightProvider", "Observation", "InsightAnswer",
           "Citation"]
