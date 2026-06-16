# src/paperbot/infrastructure/llm/providers/base.py
"""
LLM Provider 抽象基类

定义统一的 LLM 调用接口，支持多后端实现。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Provider 元信息"""
    provider_name: str
    model_name: str
    api_base: Optional[str] = None
    cost_tier: int = 1  # 1=cheap, 2=medium, 3=expensive
    max_tokens: int = 4096
    supports_streaming: bool = True


class LLMProvider(ABC):
    """
    LLM 提供商抽象基类
    
    所有 Provider 实现必须继承此类并实现核心方法。
    
    设计原则:
    - 统一的消息格式 (OpenAI style)
    - 支持流式/非流式调用
    - 异常处理由子类负责
    """
    
    @abstractmethod
    def invoke(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        非流式调用 LLM
        
        Args:
            messages: 消息列表 [{"role": "system", "content": "..."}, ...]
            **kwargs: 额外参数 (temperature, max_tokens 等)
            
        Returns:
            LLM 响应文本
        """
        ...
    
    @abstractmethod
    def stream_invoke(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Generator[str, None, None]:
        """
        流式调用 LLM
        
        Args:
            messages: 消息列表
            **kwargs: 额外参数
            
        Yields:
            响应文本块
        """
        ...
    
    @property
    @abstractmethod
    def info(self) -> ProviderInfo:
        """获取 Provider 元信息"""
        ...
    
    # ==================== 便捷方法 ====================
    
    def invoke_simple(
        self,
        system_prompt: str,
        user_prompt: str,
        include_time: bool = False,
        **kwargs
    ) -> str:
        """
        简化调用接口 (兼容旧 LLMClient API)
        
        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            include_time: 是否包含当前时间
            **kwargs: 额外参数
            
        Returns:
            LLM 响应文本
        """
        if include_time:
            current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
            user_prompt = f"当前时间: {current_time}\n\n{user_prompt}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        return self.invoke(messages, **kwargs)
    
    def stream_to_string(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        流式调用并拼接为完整字符串
        
        Args:
            messages: 消息列表
            **kwargs: 额外参数
            
        Returns:
            完整响应文本
        """
        chunks = []
        for chunk in self.stream_invoke(messages, **kwargs):
            chunks.append(chunk)
        return "".join(chunks)
    
    def __repr__(self) -> str:
        info = self.info
        return f"{self.__class__.__name__}(model={info.model_name}, provider={info.provider_name})"
