# src/paperbot/core/collaboration/__init__.py
"""
Agent collaboration module for PaperBot.
Enables multi-agent communication and coordination.
"""

from .score_bus import ScoreShareBus, StageScore

__all__ = [
    "ScoreShareBus",
    "StageScore",
]

