"""
Shader 绑定浪费分析器

检测绑定了资源但 Shader 编译器标记为静态未使用的槽位。
"""

from typing import Dict, Any
from collections import defaultdict
from .base import BaseAnalyzer


class ShaderBindingAnalyzer(BaseAnalyzer):
    """Shader 绑定浪费分析器"""
    
    @property
    def name(self) -> str:
        return "Shader绑定浪费"
    
    @property
    def requires_action_iteration(self) -> bool:
        return True  # 需要遍历 DrawCall
    
    def __init__(self, rd, controller):
        super().__init__(rd, controller)
        self.total_bindings = 0
        self.unused_bindings = 0
        self.binding_stats = defaultdict(lambda: {'total': 0, 'unused': 0})
        self.draws_with_waste = 0
        self.total_draws = 0
        self.worst_draws = []
        self.MAX_DETAIL_RECORDS = 50
    
    def analyze(self) -> Dict[str, Any]:
        """返回当前收集的结果"""
        self.results = {
            'total_bindings': self.total_bindings,
            'unused_bindings': self.unused_bindings,
            'binding_stats': dict(self.binding_stats),
            'total_draws': self.total_draws,
            'draws_with_waste': self.draws_with_waste,
            'worst_draws': self.worst_draws[:10],
        }
        return self.results
    
    def analyze_action(self, action, pipe) -> None:
        """分析单个 DrawCall 的 Shader 绑定浪费"""
        rd = self.rd
        
        self.total_draws += 1
        draw_unused = 0
        
        stages = [
            rd.ShaderStage.Vertex,
            rd.ShaderStage.Pixel,
            rd.ShaderStage.Geometry,
            rd.ShaderStage.Hull,
            rd.ShaderStage.Domain,
        ]
        
        for stage in stages:
            try:
                shader = pipe.GetShader(stage)
                if shader == rd.ResourceId.Null():
                    continue
            except:
                continue
            
            # 检查 SRV (只读资源)
            try:
                ro_resources = pipe.GetReadOnlyResources(stage)
                for binding in ro_resources:
                    if not hasattr(binding, 'descriptor'):
                        continue
                    if binding.descriptor.resource == rd.ResourceId.Null():
                        continue
                    
                    self.total_bindings += 1
                    self.binding_stats['SRV']['total'] += 1
                    
                    # 检查是否静态未使用
                    if hasattr(binding, 'access'):
                        if getattr(binding.access, 'staticallyUnused', False):
                            self.unused_bindings += 1
                            self.binding_stats['SRV']['unused'] += 1
                            draw_unused += 1
            except:
                pass
            
            # 检查 UAV (读写资源)
            try:
                rw_resources = pipe.GetReadWriteResources(stage)
                for binding in rw_resources:
                    if not hasattr(binding, 'descriptor'):
                        continue
                    if binding.descriptor.resource == rd.ResourceId.Null():
                        continue
                    
                    self.total_bindings += 1
                    self.binding_stats['UAV']['total'] += 1
                    
                    if hasattr(binding, 'access'):
                        if getattr(binding.access, 'staticallyUnused', False):
                            self.unused_bindings += 1
                            self.binding_stats['UAV']['unused'] += 1
                            draw_unused += 1
            except:
                pass
            
            # 检查 CBV (常量缓冲区)
            try:
                cb_bindings = pipe.GetConstantBlocks(stage, False)
                for binding in cb_bindings:
                    if not hasattr(binding, 'descriptor'):
                        continue
                    if binding.descriptor.resource == rd.ResourceId.Null():
                        continue
                    
                    self.total_bindings += 1
                    self.binding_stats['CBV']['total'] += 1
                    
                    if hasattr(binding, 'access'):
                        if getattr(binding.access, 'staticallyUnused', False):
                            self.unused_bindings += 1
                            self.binding_stats['CBV']['unused'] += 1
                            draw_unused += 1
            except:
                pass
        
        # 记录有浪费的 Draw
        if draw_unused > 0:
            self.draws_with_waste += 1
            
            if draw_unused >= 3 and len(self.worst_draws) < self.MAX_DETAIL_RECORDS:
                self.worst_draws.append({
                    'eid': action.eventId,
                    'unused_count': draw_unused,
                })
    
    def finalize(self) -> None:
        """排序最差的 Draw"""
        self.worst_draws.sort(key=lambda x: x['unused_count'], reverse=True)
    
    def format_report(self) -> str:
        """格式化报告"""
        if not self.results:
            self.analyze()
        
        r = self.results
        total = r['total_bindings']
        unused = r['unused_bindings']
        ratio = unused / total * 100 if total > 0 else 0
        
        draw_ratio = r['draws_with_waste'] / r['total_draws'] * 100 if r['total_draws'] > 0 else 0
        
        lines = [
            "=" * 60,
            "  🔗 Shader 绑定浪费",
            "=" * 60,
            f"    总绑定数:         {total:>8}",
            f"    未使用绑定:       {unused:>8} ({ratio:.1f}%)",
            f"    存在浪费的 Draw:  {r['draws_with_waste']:>8} ({draw_ratio:.1f}%)",
        ]
        
        # 按类型统计
        bs = r.get('binding_stats', {})
        if bs:
            lines.append("")
            lines.append("    按类型统计:")
            for btype in ['SRV', 'UAV', 'CBV']:
                stats = bs.get(btype, {'total': 0, 'unused': 0})
                if stats['total'] > 0:
                    pct = stats['unused'] / stats['total'] * 100
                    lines.append(f"      {btype}: {stats['unused']}/{stats['total']} ({pct:.1f}%)")
        
        return "\n".join(lines)
    
    def get_summary(self) -> Dict[str, Any]:
        """获取摘要"""
        return {
            'binding_waste_ratio': self.unused_bindings / self.total_bindings if self.total_bindings > 0 else 0,
            'unused_bindings': self.unused_bindings,
        }
