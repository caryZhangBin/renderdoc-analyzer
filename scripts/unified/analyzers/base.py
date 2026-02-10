"""
分析器基类

所有分析模块都继承自 BaseAnalyzer，提供统一接口。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from collections import defaultdict


def format_bytes(size: int) -> str:
    """格式化字节数为人类可读格式"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


class BaseAnalyzer(ABC):
    """
    分析器基类
    
    每个分析器必须实现:
    - name: 分析器名称
    - analyze(): 执行分析并返回结果字典
    - format_report(): 将结果格式化为可读字符串
    
    可选实现:
    - analyze_action(): 对单个 DrawCall 进行分析 (用于需要遍历的分析器)
    """
    
    def __init__(self, rd, controller):
        """
        初始化分析器
        
        Args:
            rd: renderdoc 模块
            controller: IReplayController 实例
        """
        self.rd = rd
        self.controller = controller
        self.results = {}
    
    @property
    @abstractmethod
    def name(self) -> str:
        """分析器名称"""
        pass
    
    @property
    def requires_action_iteration(self) -> bool:
        """是否需要遍历 DrawCall (默认 False)"""
        return False
    
    @abstractmethod
    def analyze(self) -> Dict[str, Any]:
        """
        执行分析
        
        Returns:
            包含分析结果的字典
        """
        pass
    
    def analyze_action(self, action, pipe) -> None:
        """
        分析单个 Action (可选)
        
        对于需要遍历 DrawCall 的分析器，实现此方法。
        调度器会在遍历时调用此方法，避免多次遍历。
        
        Args:
            action: ActionDescription 对象
            pipe: PipelineState 对象
        """
        pass
    
    def finalize(self) -> None:
        """
        遍历结束后的收尾工作 (可选)
        
        在所有 analyze_action 调用完成后执行。
        """
        pass
    
    @abstractmethod
    def format_report(self) -> str:
        """
        格式化分析报告
        
        Returns:
            格式化后的报告字符串
        """
        pass
    
    def get_summary(self) -> Dict[str, Any]:
        """
        获取摘要信息 (用于合并报告)
        
        Returns:
            包含关键指标的字典
        """
        return self.results
