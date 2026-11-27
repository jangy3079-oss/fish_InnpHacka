"""Explainable loss comparison and configured emergency-release checks (#27)."""

from .calculator import LossCalculator
from .schemas import ComparisonRequest

__all__ = ["LossCalculator", "ComparisonRequest"]
