"""Core domain logic: projects, media, analysis, alignment, transitions."""

from .alignment import AlignmentResult, TimeMap, align_featuresets

__all__ = ["AlignmentResult", "TimeMap", "align_featuresets"]
