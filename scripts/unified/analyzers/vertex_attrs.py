"""
顶点属性浪费分析器

检测 Input Assembly 提供了数据，但 Vertex Shader 未使用的顶点属性。
"""

from typing import Dict, Any
from collections import defaultdict
from .base import BaseAnalyzer, format_bytes


class VertexAttributeAnalyzer(BaseAnalyzer):
    """顶点属性浪费分析器"""
    
    @property
    def name(self) -> str:
        return "顶点属性浪费"
    
    @property
    def requires_action_iteration(self) -> bool:
        return True  # 需要遍历 DrawCall
    
    def __init__(self, rd, controller):
        super().__init__(rd, controller)
        self.total_draws = 0
        self.wasted_draws = 0
        self.wasted_bytes = 0
        self.wasted_vertices = 0
        self.semantic_stats = defaultdict(lambda: {'provided': 0, 'used': 0, 'wasted': 0})
        self.worst_draws = []
        self.MAX_DETAIL_RECORDS = 50
    
    def analyze(self) -> Dict[str, Any]:
        """返回当前收集的结果"""
        self.results = {
            'total_draws': self.total_draws,
            'wasted_draws': self.wasted_draws,
            'wasted_bytes': self.wasted_bytes,
            'wasted_vertices': self.wasted_vertices,
            'semantic_stats': dict(self.semantic_stats),
            'worst_draws': self.worst_draws[:10],
        }
        return self.results
    
    def analyze_action(self, action, pipe) -> None:
        """分析单个 DrawCall 的顶点属性浪费"""
        rd = self.rd
        
        self.total_draws += 1
        
        # 获取 VS 反射信息
        try:
            vs_refl = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            if vs_refl is None:
                return
        except:
            return
        
        # 获取着色器实际使用的属性
        used_semantics = set()
        for sig in vs_refl.inputSignature:
            if hasattr(sig, 'semanticName'):
                sem_name = sig.semanticName
                sem_idx = sig.semanticIndex if hasattr(sig, 'semanticIndex') else 0
                used_semantics.add(f"{sem_name}{sem_idx}")
                self.semantic_stats[sem_name]['used'] += 1
        
        # 获取 IA 提供的属性
        try:
            ia = pipe.GetIAState() if hasattr(pipe, 'GetIAState') else None
            if ia is None:
                return
            attrs = ia.attributes if hasattr(ia, 'attributes') else []
        except:
            return
        
        wasted_attrs = []
        wasted_bytes_per_vertex = 0
        
        for attr in attrs:
            if not hasattr(attr, 'semanticName'):
                continue
            
            sem_name = attr.semanticName
            sem_idx = attr.semanticIndex if hasattr(attr, 'semanticIndex') else 0
            full_name = f"{sem_name}{sem_idx}"
            
            self.semantic_stats[sem_name]['provided'] += 1
            
            # 检查是否被 VS 使用
            if full_name not in used_semantics:
                self.semantic_stats[sem_name]['wasted'] += 1
                
                # 计算字节大小
                byte_size = 4  # 默认
                if hasattr(attr, 'format'):
                    fmt = attr.format
                    if hasattr(fmt, 'compByteWidth') and hasattr(fmt, 'compCount'):
                        byte_size = fmt.compByteWidth * fmt.compCount
                
                wasted_attrs.append((sem_name, byte_size))
                wasted_bytes_per_vertex += byte_size
        
        if wasted_attrs:
            self.wasted_draws += 1
            
            # 计算浪费的总带宽
            num_verts = action.numIndices if hasattr(action, 'numIndices') else 0
            num_instances = action.numInstances if hasattr(action, 'numInstances') else 1
            total_verts = num_verts * num_instances
            wasted = wasted_bytes_per_vertex * total_verts
            
            self.wasted_bytes += wasted
            self.wasted_vertices += total_verts
            
            # 记录严重浪费的 Draw
            if wasted > 100 * 1024 and len(self.worst_draws) < self.MAX_DETAIL_RECORDS:
                self.worst_draws.append({
                    'eid': action.eventId,
                    'vertices': total_verts,
                    'wasted_bytes': wasted,
                    'wasted_attrs': [a[0] for a in wasted_attrs],
                })
    
    def finalize(self) -> None:
        """排序最差的 Draw"""
        self.worst_draws.sort(key=lambda x: x['wasted_bytes'], reverse=True)
    
    def format_report(self) -> str:
        """格式化报告"""
        # 确保结果已生成
        if not self.results:
            self.analyze()
        
        r = self.results
        total = r['total_draws']
        wasted = r['wasted_draws']
        ratio = wasted / total * 100 if total > 0 else 0
        
        lines = [
            "=" * 60,
            "  🔺 顶点属性浪费",
            "=" * 60,
            f"    总 Draw 数:       {total:>8}",
            f"    存在浪费的 Draw:  {wasted:>8} ({ratio:.1f}%)",
            f"    浪费带宽:         {format_bytes(r['wasted_bytes']):>15}",
        ]
        
        # 按语义统计
        sem_stats = r.get('semantic_stats', {})
        wasted_sems = [(k, v) for k, v in sem_stats.items() if v.get('wasted', 0) > 0]
        wasted_sems.sort(key=lambda x: -x[1]['wasted'])
        
        if wasted_sems:
            lines.append("")
            lines.append("    按语义统计 (浪费最多):")
            for sem, stats in wasted_sems[:8]:
                lines.append(f"      {sem:<12}: 提供 {stats['provided']:>5}, 浪费 {stats['wasted']:>5}")
        
        # 最差的 Draw
        worst = r.get('worst_draws', [])
        if worst:
            lines.append("")
            lines.append("    浪费最多的 Draw:")
            for d in worst[:5]:
                attrs = ", ".join(d['wasted_attrs'][:3])
                if len(d['wasted_attrs']) > 3:
                    attrs += "..."
                lines.append(f"      EID {d['eid']}: {format_bytes(d['wasted_bytes'])} ({attrs})")
        
        return "\n".join(lines)
    
    def get_summary(self) -> Dict[str, Any]:
        """获取摘要"""
        return {
            'vertex_waste_bytes': self.wasted_bytes,
            'vertex_waste_ratio': self.wasted_draws / self.total_draws if self.total_draws > 0 else 0,
        }
