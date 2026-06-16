# src/paperbot/agents/mixins/json_parser.py
"""通用 JSON 解析 Mixin。

委托给统一的 ``paperbot.utils.json_parser.RobustJSONParser``，从而复用同一套强修复
逻辑（markdown 围栏 / 思考内容清理 / json_repair 回退 / 多候选策略）。
``JSONParseError`` 从规范模块再导出，保证 ``except JSONParseError`` 在任一导入路径下
都捕获同一个异常类型。
"""

from typing import Any

from paperbot.utils.json_parser import JSONParseError, RobustJSONParser

__all__ = ["JSONParserMixin", "JSONParseError"]


class JSONParserMixin:
    """JSON 解析 Mixin。

    提供鲁棒的 JSON 解析能力（代码块清理 + json_repair 回退），实现委托给共享的
    ``RobustJSONParser``。
    """

    def parse_json(self, text: str, allow_repair: bool = True) -> Any:
        """解析 JSON 文本。

        Args:
            text: 待解析的文本
            allow_repair: 是否允许使用 json_repair 修复

        Returns:
            解析后的 Python 对象

        Raises:
            JSONParseError: 解析失败时抛出
        """
        return RobustJSONParser(enable_json_repair=allow_repair).parse(text)
