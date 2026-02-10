"""
基础统计分析器

统计 DrawCall、Dispatch、Clear、Copy 等基础信息。
"""

from typing import Dict, Any
from .base import BaseAnalyzer


class BasicStatsAnalyzer(BaseAnalyzer):
    """基础统计分析器"""
    
    @property
    def name(self) -> str:
        return "基础统计"
    
    @property
    def requires_action_iteration(self) -> bool:
        return False  # 直接递归统计，不需要通过调度器遍历
    
    def analyze(self) -> Dict[str, Any]:
        """执行基础统计分析"""
        rd = self.rd
        controller = self.controller
        
        self.results = {
            'draw_count': 0,
            'dispatch_count': 0,
            'clear_count': 0,
            'copy_count': 0,
            'marker_count': 0,
            'total_actions': 0,
            'max_depth': 0,
        }
        
        def count_actions(action, depth=0):
            self.results['total_actions'] += 1
            self.results['max_depth'] = max(self.results['max_depth'], depth)
            
            flags = int(action.flags)
            
            if flags & int(rd.ActionFlags.Drawcall):
                self.results['draw_count'] += 1
            if flags & int(rd.ActionFlags.Dispatch):
                self.results['dispatch_count'] += 1
            if flags & int(rd.ActionFlags.Clear):
                self.results['clear_count'] += 1
            if flags & int(rd.ActionFlags.Copy):
                self.results['copy_count'] += 1
            if flags & int(rd.ActionFlags.PushMarker):
                self.results['marker_count'] += 1
            
            for child in action.children:
                count_actions(child, depth + 1)
        
        for action in controller.GetRootActions():
            count_actions(action)
        
        return self.results
    
    def format_report(self) -> str:
        """格式化报告"""
        r = self.results
        lines = [
            "=" * 60,
            "  📊 基础统计",
            "=" * 60,
            f"    Draw 调用数:      {r.get('draw_count', 0):>8}",
            f"    Dispatch 调用数:  {r.get('dispatch_count', 0):>8}",
            f"    Clear 调用数:     {r.get('clear_count', 0):>8}",
            f"    Copy 调用数:      {r.get('copy_count', 0):>8}",
            f"    Marker 数量:      {r.get('marker_count', 0):>8}",
            f"    总 Action 数:     {r.get('total_actions', 0):>8}",
            f"    最大嵌套深度:     {r.get('max_depth', 0):>8}",
        ]
        return "\n".join(lines)
