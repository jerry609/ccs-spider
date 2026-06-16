"""鲁棒 JSON 解析（兼容垫片）。

规范实现已统一到 ``paperbot.utils.json_parser``。保留此模块仅为兼容现有导入
（``from ..utils.json_parser import RobustJSONParser, JSONParseError``），
使报告引擎节点复用同一套强修复逻辑（markdown 围栏清理 / json_repair / 候选策略）。
"""

from __future__ import annotations

from paperbot.utils.json_parser import JSONParseError, RobustJSONParser

__all__ = ["RobustJSONParser", "JSONParseError"]
