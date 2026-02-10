"""
内存分析器

分析 GPU 内存占用，包括纹理和缓冲区统计。
"""

from typing import Dict, Any
from collections import defaultdict
from .base import BaseAnalyzer, format_bytes


class MemoryAnalyzer(BaseAnalyzer):
    """内存分析器"""
    
    @property
    def name(self) -> str:
        return "内存分析"
    
    @property
    def requires_action_iteration(self) -> bool:
        return False  # 不需要遍历 DrawCall
    
    def analyze(self) -> Dict[str, Any]:
        """执行内存分析"""
        rd = self.rd
        controller = self.controller
        
        self.results = {
            'texture_count': 0,
            'texture_size': 0,
            'buffer_count': 0,
            'buffer_size': 0,
            'total_size': 0,
            'texture_formats': {},
            'large_textures': [],
            'large_buffers': [],
        }
        
        texture_formats = defaultdict(lambda: {'count': 0, 'size': 0})
        
        # 分析纹理
        textures = controller.GetTextures()
        self.results['texture_count'] = len(textures)
        
        for tex in textures:
            size = tex.byteSize if hasattr(tex, 'byteSize') else 0
            self.results['texture_size'] += size
            
            # 按格式统计
            fmt_name = tex.format.Name() if hasattr(tex.format, 'Name') else str(tex.format)
            texture_formats[fmt_name]['count'] += 1
            texture_formats[fmt_name]['size'] += size
            
            # 大纹理 (> 16MB)
            if size > 16 * 1024 * 1024:
                self.results['large_textures'].append({
                    'id': str(tex.resourceId),
                    'name': tex.name if hasattr(tex, 'name') and tex.name else f"Texture_{tex.resourceId}",
                    'size': size,
                    'format': fmt_name,
                    'dimensions': f"{tex.width}x{tex.height}x{tex.depth}",
                    'mips': tex.mips,
                })
        
        # 分析缓冲区
        buffers = controller.GetBuffers()
        self.results['buffer_count'] = len(buffers)
        
        for buf in buffers:
            size = buf.length if hasattr(buf, 'length') else 0
            self.results['buffer_size'] += size
            
            # 大缓冲区 (> 8MB)
            if size > 8 * 1024 * 1024:
                self.results['large_buffers'].append({
                    'id': str(buf.resourceId),
                    'name': buf.name if hasattr(buf, 'name') and buf.name else f"Buffer_{buf.resourceId}",
                    'size': size,
                })
        
        self.results['total_size'] = self.results['texture_size'] + self.results['buffer_size']
        self.results['texture_formats'] = dict(texture_formats)
        
        # 排序大资源
        self.results['large_textures'].sort(key=lambda x: x['size'], reverse=True)
        self.results['large_buffers'].sort(key=lambda x: x['size'], reverse=True)
        
        return self.results
    
    def format_report(self) -> str:
        """格式化报告"""
        r = self.results
        total = r['total_size']
        tex_pct = r['texture_size'] / total * 100 if total > 0 else 0
        buf_pct = r['buffer_size'] / total * 100 if total > 0 else 0
        
        lines = [
            "=" * 60,
            "  💾 GPU 内存占用",
            "=" * 60,
            f"    总 GPU 内存:      {format_bytes(total):>15}",
            f"    ├─ 纹理:          {format_bytes(r['texture_size']):>15} ({tex_pct:.1f}%)",
            f"    │   数量:         {r['texture_count']:>15} 个",
            f"    └─ 缓冲区:        {format_bytes(r['buffer_size']):>15} ({buf_pct:.1f}%)",
            f"        数量:         {r['buffer_count']:>15} 个",
        ]
        
        # 大纹理
        if r['large_textures']:
            lines.append("")
            lines.append("    ⚠️ 大纹理 (>16MB):")
            for tex in r['large_textures'][:5]:
                lines.append(f"       • {tex['dimensions']} {tex['format']}: {format_bytes(tex['size'])}")
        
        # 大缓冲区
        if r['large_buffers']:
            lines.append("")
            lines.append("    ⚠️ 大缓冲区 (>8MB):")
            for buf in r['large_buffers'][:5]:
                name = buf['name'][:30] + "..." if len(buf['name']) > 30 else buf['name']
                lines.append(f"       • {name}: {format_bytes(buf['size'])}")
        
        return "\n".join(lines)
    
    def get_summary(self) -> Dict[str, Any]:
        """获取摘要"""
        return {
            'total_gpu_memory': self.results['total_size'],
            'texture_count': self.results['texture_count'],
            'buffer_count': self.results['buffer_count'],
            'large_texture_count': len(self.results['large_textures']),
        }
