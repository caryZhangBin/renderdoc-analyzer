"""
RenderDoc 分析模块包

每个分析模块都是独立的，可以单独测试，也可以通过 analyze_all.py 统一调度。
模块之间通过 try/except 隔离，单个模块出错不影响其他模块执行。
"""

from .base import BaseAnalyzer
from .basic_stats import BasicStatsAnalyzer
from .memory import MemoryAnalyzer
from .vertex_attrs import VertexAttributeAnalyzer
from .shader_bindings import ShaderBindingAnalyzer
from .overdraw import OverdrawAnalyzer

# 所有可用的分析器 (按执行顺序)
# 格式: (模块ID, 显示名称, 分析器类, 是否需要遍历DrawCall)
ALL_ANALYZERS = [
    ("basic_stats", "基础统计", BasicStatsAnalyzer, False),
    ("memory", "内存分析", MemoryAnalyzer, False),
    ("vertex_attrs", "顶点属性浪费", VertexAttributeAnalyzer, True),
    ("shader_bindings", "Shader绑定浪费", ShaderBindingAnalyzer, True),
    ("overdraw", "Overdraw估算", OverdrawAnalyzer, True),
]

__all__ = [
    'BaseAnalyzer',
    'BasicStatsAnalyzer', 
    'MemoryAnalyzer',
    'VertexAttributeAnalyzer',
    'ShaderBindingAnalyzer',
    'OverdrawAnalyzer',
    'ALL_ANALYZERS',
]
