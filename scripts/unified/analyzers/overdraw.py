"""
Overdraw 估算分析器

估算屏幕 Overdraw 情况。
"""

from typing import Dict, Any
from collections import defaultdict
from .base import BaseAnalyzer


class OverdrawAnalyzer(BaseAnalyzer):
    """Overdraw 估算分析器"""
    
    @property
    def name(self) -> str:
        return "Overdraw估算"
    
    @property
    def requires_action_iteration(self) -> bool:
        return True  # 需要遍历 DrawCall
    
    def __init__(self, rd, controller):
        super().__init__(rd, controller)
        self.total_triangles = 0
        self.total_vertices = 0
        self.total_instances = 0
        self.main_screen_pixels = 0
        self.high_overdraw_draws = []
        self.MAX_DETAIL_RECORDS = 50
        
        # 预分析主屏幕分辨率
        self._detect_main_resolution()
    
    def _detect_main_resolution(self):
        """检测主渲染目标分辨率"""
        rd = self.rd
        controller = self.controller
        
        rt_resolutions = defaultdict(int)
        
        try:
            textures = controller.GetTextures()
            for tex in textures:
                if hasattr(tex, 'creationFlags'):
                    flags = int(tex.creationFlags)
                    if hasattr(rd, 'TextureCategory'):
                        if flags & int(rd.TextureCategory.ColorTarget):
                            rt_resolutions[(tex.width, tex.height)] += 1
        except:
            pass
        
        # 选择最常见的非正方形分辨率 (排除 CubeMap/探针)
        candidates = [(w, h, cnt) for (w, h), cnt in rt_resolutions.items() 
                      if w >= 256 and h >= 256 and w != h]
        
        if candidates:
            candidates.sort(key=lambda x: -x[2])
            self.main_screen_pixels = candidates[0][0] * candidates[0][1]
    
    def analyze(self) -> Dict[str, Any]:
        """返回当前收集的结果"""
        avg_overdraw = 0
        if self.main_screen_pixels > 0:
            # 假设平均三角形覆盖 100 像素
            estimated_total_pixels = self.total_triangles * 100
            avg_overdraw = estimated_total_pixels / self.main_screen_pixels
        
        self.results = {
            'total_triangles': self.total_triangles,
            'total_vertices': self.total_vertices,
            'total_instances': self.total_instances,
            'main_screen_pixels': self.main_screen_pixels,
            'avg_overdraw': avg_overdraw,
            'high_overdraw_draws': self.high_overdraw_draws[:10],
        }
        return self.results
    
    def analyze_action(self, action, pipe) -> None:
        """分析单个 DrawCall 的 Overdraw 贡献"""
        num_verts = action.numIndices if hasattr(action, 'numIndices') else 0
        num_instances = action.numInstances if hasattr(action, 'numInstances') else 1
        
        self.total_vertices += num_verts * num_instances
        self.total_instances += num_instances
        
        # 估算三角形数 (假设 Triangle List)
        triangles = (num_verts // 3) * num_instances
        self.total_triangles += triangles
        
        # 高 Overdraw 检测
        if self.main_screen_pixels > 0 and triangles > 0:
            # 假设平均三角形覆盖 100 像素
            estimated_pixels = triangles * 100
            overdraw = estimated_pixels / self.main_screen_pixels
            
            if overdraw > 3.0 and len(self.high_overdraw_draws) < self.MAX_DETAIL_RECORDS:
                self.high_overdraw_draws.append({
                    'eid': action.eventId,
                    'overdraw': overdraw,
                    'triangles': triangles,
                    'instances': num_instances,
                })
    
    def finalize(self) -> None:
        """排序高 Overdraw 的 Draw"""
        self.high_overdraw_draws.sort(key=lambda x: x['overdraw'], reverse=True)
    
    def format_report(self) -> str:
        """格式化报告"""
        if not self.results:
            self.analyze()
        
        r = self.results
        
        lines = [
            "=" * 60,
            "  🎨 Overdraw 估算",
            "=" * 60,
            f"    总三角形数:       {r['total_triangles']:>12,}",
            f"    总顶点数:         {r['total_vertices']:>12,}",
            f"    总实例数:         {r['total_instances']:>12,}",
        ]
        
        if r['main_screen_pixels'] > 0:
            w = h = int(r['main_screen_pixels'] ** 0.5)  # 近似
            lines.append(f"    主屏幕像素:       {r['main_screen_pixels']:>12,}")
            lines.append(f"    估算平均 Overdraw:{r['avg_overdraw']:>11.1f}x")
            
            # Overdraw 评级
            od = r['avg_overdraw']
            if od < 2:
                rating = "✅ 优秀"
            elif od < 3:
                rating = "✅ 良好"
            elif od < 5:
                rating = "⚠️ 一般"
            else:
                rating = "❌ 较高"
            lines.append(f"    评级:             {rating:>12}")
        
        # 高 Overdraw 的 Draw
        high = r.get('high_overdraw_draws', [])
        if high:
            lines.append("")
            lines.append("    高 Overdraw Draw (>3x):")
            for d in high[:5]:
                lines.append(f"      EID {d['eid']}: {d['overdraw']:.1f}x ({d['triangles']:,} 三角形)")
        
        return "\n".join(lines)
    
    def get_summary(self) -> Dict[str, Any]:
        """获取摘要"""
        return {
            'avg_overdraw': self.results.get('avg_overdraw', 0),
            'total_triangles': self.total_triangles,
        }
