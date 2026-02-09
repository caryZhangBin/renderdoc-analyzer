#!/usr/bin/env python3
"""
RenderDoc GPU 内存占用分析脚本

用法: python analyze_memory.py <rdc_file_path>

功能:
- 按类型统计 GPU 内存占用
- 分析纹理格式分布
- 识别大内存消耗资源
- 提供内存优化建议
"""

import sys
import os
from collections import defaultdict

def format_bytes(size):
    """格式化字节数"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def analyze_memory(rdc_path):
    """分析 GPU 内存占用"""
    
    try:
        import renderdoc as rd
    except ImportError:
        print("错误: 无法导入 renderdoc 模块")
        print("请设置环境变量:")
        print("  set PATH=%PATH%;C:\\Program Files\\RenderDoc")
        print("  set PYTHONPATH=%PYTHONPATH%;C:\\Program Files\\RenderDoc\\pymodules")
        sys.exit(1)
    
    if not os.path.exists(rdc_path):
        print(f"错误: 文件不存在 - {rdc_path}")
        sys.exit(1)
    
    print(f"正在分析内存占用: {rdc_path}")
    print("=" * 70)
    
    # 打开捕获文件
    cap = rd.OpenCaptureFile()
    result = cap.OpenFile(rdc_path, '', None)
    
    if result != rd.ResultCode.Succeeded:
        print(f"错误: 无法打开文件 - {result}")
        sys.exit(1)
    
    result = cap.OpenCapture(rd.ReplayOptions(), None)
    if isinstance(result, tuple):
        status, controller = result
        if status != rd.ResultCode.Succeeded:
            print(f"错误: 无法创建回放控制器 - {status}")
            cap.Shutdown()
            sys.exit(1)
    else:
        controller = result
        if controller is None:
            print("错误: 无法创建回放控制器")
            cap.Shutdown()
            sys.exit(1)
    
    # ============ 纹理分析 ============
    textures = controller.GetTextures()
    
    texture_total_size = 0
    texture_by_format = defaultdict(lambda: {'count': 0, 'size': 0})
    texture_by_dimension = defaultdict(lambda: {'count': 0, 'size': 0})
    texture_by_usage = defaultdict(lambda: {'count': 0, 'size': 0})
    large_textures = []  # 大纹理列表
    
    for tex in textures:
        size = tex.byteSize if hasattr(tex, 'byteSize') else 0
        texture_total_size += size
        
        # 按格式分类
        fmt_name = tex.format.Name() if hasattr(tex.format, 'Name') else str(tex.format)
        texture_by_format[fmt_name]['count'] += 1
        texture_by_format[fmt_name]['size'] += size
        
        # 按维度分类
        if tex.depth > 1:
            dim = '3D'
        elif tex.arraysize > 1:
            if tex.cubemap if hasattr(tex, 'cubemap') else False:
                dim = 'Cube'
            else:
                dim = '2D Array'
        else:
            dim = '2D'
        texture_by_dimension[dim]['count'] += 1
        texture_by_dimension[dim]['size'] += size
        
        # 按用途分类（根据 creationFlags）
        usage = 'General'
        if hasattr(tex, 'creationFlags'):
            flags = tex.creationFlags
            if hasattr(rd, 'TextureCategory'):
                if flags & rd.TextureCategory.ColorTarget:
                    usage = 'RenderTarget'
                elif flags & rd.TextureCategory.DepthTarget:
                    usage = 'DepthStencil'
                elif flags & rd.TextureCategory.ShaderRead:
                    usage = 'ShaderResource'
        texture_by_usage[usage]['count'] += 1
        texture_by_usage[usage]['size'] += size
        
        # 记录大纹理 (> 16MB)
        if size > 16 * 1024 * 1024:
            large_textures.append({
                'id': str(tex.resourceId),
                'name': tex.name if hasattr(tex, 'name') and tex.name else f"Texture_{tex.resourceId}",
                'size': size,
                'format': fmt_name,
                'dimensions': f"{tex.width}x{tex.height}x{tex.depth}",
                'mips': tex.mips,
                'arrays': tex.arraysize
            })
    
    # ============ 缓冲区分析 ============
    buffers = controller.GetBuffers()
    
    buffer_total_size = 0
    buffer_by_size = {
        'Tiny (< 1KB)': {'count': 0, 'size': 0},
        'Small (1-64KB)': {'count': 0, 'size': 0},
        'Medium (64KB-1MB)': {'count': 0, 'size': 0},
        'Large (1-16MB)': {'count': 0, 'size': 0},
        'Huge (> 16MB)': {'count': 0, 'size': 0}
    }
    large_buffers = []
    
    for buf in buffers:
        size = buf.length if hasattr(buf, 'length') else 0
        buffer_total_size += size
        
        # 按大小分类
        if size < 1024:
            category = 'Tiny (< 1KB)'
        elif size < 64 * 1024:
            category = 'Small (1-64KB)'
        elif size < 1024 * 1024:
            category = 'Medium (64KB-1MB)'
        elif size < 16 * 1024 * 1024:
            category = 'Large (1-16MB)'
        else:
            category = 'Huge (> 16MB)'
        
        buffer_by_size[category]['count'] += 1
        buffer_by_size[category]['size'] += size
        
        # 记录大缓冲区 (> 8MB)
        if size > 8 * 1024 * 1024:
            large_buffers.append({
                'id': str(buf.resourceId),
                'name': buf.name if hasattr(buf, 'name') and buf.name else f"Buffer_{buf.resourceId}",
                'size': size
            })
    
    # ============ 输出结果 ============
    total_gpu_memory = texture_total_size + buffer_total_size
    
    print("\n" + "=" * 70)
    print("                       📊 GPU 内存占用总览")
    print("=" * 70)
    print(f"  总 GPU 内存:    {format_bytes(total_gpu_memory):>15}")
    print(f"  ├─ 纹理:        {format_bytes(texture_total_size):>15} ({texture_total_size/total_gpu_memory*100:.1f}%)")
    print(f"  └─ 缓冲区:      {format_bytes(buffer_total_size):>15} ({buffer_total_size/total_gpu_memory*100:.1f}%)")
    
    # 纹理详情
    print("\n" + "-" * 70)
    print("                       🖼️ 纹理内存分析")
    print("-" * 70)
    print(f"  纹理总数:       {len(textures)}")
    print(f"  纹理总大小:     {format_bytes(texture_total_size)}")
    
    print("\n  📦 按格式分布 (Top 10):")
    sorted_formats = sorted(texture_by_format.items(), key=lambda x: x[1]['size'], reverse=True)
    print(f"    {'格式':<30} {'数量':>8} {'大小':>15} {'占比':>8}")
    print("    " + "-" * 64)
    for fmt, data in sorted_formats[:10]:
        pct = data['size'] / texture_total_size * 100 if texture_total_size > 0 else 0
        print(f"    {fmt:<30} {data['count']:>8} {format_bytes(data['size']):>15} {pct:>7.1f}%")
    
    print("\n  📐 按维度分布:")
    for dim, data in sorted(texture_by_dimension.items(), key=lambda x: x[1]['size'], reverse=True):
        pct = data['size'] / texture_total_size * 100 if texture_total_size > 0 else 0
        print(f"    {dim:<15}: {data['count']:>5} 个, {format_bytes(data['size']):>12} ({pct:.1f}%)")
    
    print("\n  🎯 按用途分布:")
    for usage, data in sorted(texture_by_usage.items(), key=lambda x: x[1]['size'], reverse=True):
        pct = data['size'] / texture_total_size * 100 if texture_total_size > 0 else 0
        print(f"    {usage:<15}: {data['count']:>5} 个, {format_bytes(data['size']):>12} ({pct:.1f}%)")
    
    # 缓冲区详情
    print("\n" + "-" * 70)
    print("                       💾 缓冲区内存分析")
    print("-" * 70)
    print(f"  缓冲区总数:     {len(buffers)}")
    print(f"  缓冲区总大小:   {format_bytes(buffer_total_size)}")
    
    print("\n  📊 按大小分布:")
    for category in ['Huge (> 16MB)', 'Large (1-16MB)', 'Medium (64KB-1MB)', 'Small (1-64KB)', 'Tiny (< 1KB)']:
        data = buffer_by_size[category]
        if data['count'] > 0:
            pct = data['size'] / buffer_total_size * 100 if buffer_total_size > 0 else 0
            bar_len = int(pct / 2)
            bar = "█" * bar_len
            print(f"    {category:<18}: {data['count']:>5} 个, {format_bytes(data['size']):>12} ({pct:>5.1f}%) {bar}")
    
    # 大资源列表
    if large_textures:
        large_textures.sort(key=lambda x: x['size'], reverse=True)
        print("\n" + "-" * 70)
        print("                    ⚠️ 大纹理列表 (> 16MB)")
        print("-" * 70)
        print(f"    {'名称':<30} {'尺寸':<20} {'格式':<20} {'大小':>12}")
        print("    " + "-" * 84)
        for tex in large_textures[:15]:
            name = tex['name'][:28] + ".." if len(tex['name']) > 30 else tex['name']
            dims = f"{tex['dimensions']} (m{tex['mips']})"
            print(f"    {name:<30} {dims:<20} {tex['format']:<20} {format_bytes(tex['size']):>12}")
    
    if large_buffers:
        large_buffers.sort(key=lambda x: x['size'], reverse=True)
        print("\n" + "-" * 70)
        print("                    ⚠️ 大缓冲区列表 (> 8MB)")
        print("-" * 70)
        print(f"    {'名称':<50} {'大小':>18}")
        print("    " + "-" * 70)
        for buf in large_buffers[:15]:
            name = buf['name'][:48] + ".." if len(buf['name']) > 50 else buf['name']
            print(f"    {name:<50} {format_bytes(buf['size']):>18}")
    
    # 优化建议
    print("\n" + "=" * 70)
    print("                       💡 内存优化建议")
    print("=" * 70)
    
    suggestions = []
    
    # 检查未压缩格式
    uncompressed_size = 0
    for fmt, data in texture_by_format.items():
        if 'BC' not in fmt and 'ASTC' not in fmt and 'ETC' not in fmt and 'DXT' not in fmt:
            if 'R8G8B8A8' in fmt or 'R16G16' in fmt or 'R32' in fmt:
                uncompressed_size += data['size']
    
    if uncompressed_size > 50 * 1024 * 1024:
        suggestions.append(f"  • 未压缩纹理占用 {format_bytes(uncompressed_size)}, 考虑使用 BC/ASTC 压缩格式")
    
    # 检查大纹理
    if large_textures:
        suggestions.append(f"  • 有 {len(large_textures)} 个超大纹理 (>16MB), 考虑降低分辨率或使用流式加载")
    
    # 检查 mipmap
    no_mip_count = sum(1 for tex in textures if tex.mips == 1 and tex.width > 256)
    if no_mip_count > 10:
        suggestions.append(f"  • {no_mip_count} 个纹理没有 Mipmap, 可能导致纹理抖动和带宽浪费")
    
    # 检查缓冲区碎片
    tiny_buffers = buffer_by_size['Tiny (< 1KB)']['count']
    if tiny_buffers > 100:
        suggestions.append(f"  • 有 {tiny_buffers} 个小缓冲区 (<1KB), 考虑合并以减少管理开销")
    
    if not suggestions:
        print("  ✅ 内存使用看起来比较合理，没有明显问题")
    else:
        for s in suggestions:
            print(s)
    
    # 清理
    controller.Shutdown()
    cap.Shutdown()
    
    print("\n" + "=" * 70)
    print("分析完成!")


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_memory.py <rdc_file_path>")
        sys.exit(1)
    
    analyze_memory(sys.argv[1])


if __name__ == "__main__":
    main()
