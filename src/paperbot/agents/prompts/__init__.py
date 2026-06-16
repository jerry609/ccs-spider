# src/paperbot/agents/prompts/__init__.py
"""
Prompt 模板集中管理。
"""

# 学者追踪系统提示词
from .scholar_prompts import (
    # Schema 定义
    output_schema_scholar_info,
    output_schema_paper_analysis,
    output_schema_influence_assessment,
    output_schema_research_direction,
    output_schema_reflection_search,
    
    # 系统提示词
    SYSTEM_PROMPT_EXTRACT_SCHOLAR_INFO,
    SYSTEM_PROMPT_ANALYZE_PAPER,
    SYSTEM_PROMPT_ASSESS_INFLUENCE,
    SYSTEM_PROMPT_ANALYZE_RESEARCH_DIRECTION,
    SYSTEM_PROMPT_REFLECTION_SEARCH,
    SYSTEM_PROMPT_GENERATE_REPORT,
    SYSTEM_PROMPT_DETECT_NEW_PAPERS,
    
    # 辅助函数
    get_prompt_for_task,
    get_schema_for_task,
)

__all__ = [
    # Scholar Tracking Schema
    'output_schema_scholar_info',
    'output_schema_paper_analysis',
    'output_schema_influence_assessment',
    'output_schema_research_direction',
    'output_schema_reflection_search',
    
    # Scholar Tracking Prompts
    'SYSTEM_PROMPT_EXTRACT_SCHOLAR_INFO',
    'SYSTEM_PROMPT_ANALYZE_PAPER',
    'SYSTEM_PROMPT_ASSESS_INFLUENCE',
    'SYSTEM_PROMPT_ANALYZE_RESEARCH_DIRECTION',
    'SYSTEM_PROMPT_REFLECTION_SEARCH',
    'SYSTEM_PROMPT_GENERATE_REPORT',
    'SYSTEM_PROMPT_DETECT_NEW_PAPERS',
    
    # Helper Functions
    'get_prompt_for_task',
    'get_schema_for_task',
]

