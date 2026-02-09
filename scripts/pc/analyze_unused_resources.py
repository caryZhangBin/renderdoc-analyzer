#!/usr/bin/env python3
"""
RenderDoc 未使用资源分析脚本

用法: python analyze_unused_resources.py <rdc_file_path>

功能:
- 检查所有 Texture 和 Buffer 资源
- 分析每个资源的 Usage 记录
- 识别从未被使用的资源（占用显存但无贡献）
- 统计浪费的显存大小
"""

import sys
import os
from collections import defaultdict

def format_size(size_bytes):
    """格式化字节大小为可读字符串"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

def estimate_texture_size(tex):
    """估算纹理占用的显存大小"""
    # 格式到每像素字节数的映射
    format_name = tex.format.Name() if hasattr(tex.format, 'Name') else str(tex.format)
    
    # 常见格式的每像素字节数
    bpp_map = {
        'R8G8B8A8': 4, 'B8G8R8A8': 4, 'R8G8B8A8_UNORM': 4, 'B8G8R8A8_UNORM': 4,
        'R16G16B16A16': 8, 'R16G16B16A16_FLOAT': 8, 'R16G16B16A16_UNORM': 8,
        'R32G32B32A32': 16, 'R32G32B32A32_FLOAT': 16,
        'R32G32B32': 12, 'R32G32B32_FLOAT': 12,
        'R16G16': 4, 'R16G16_FLOAT': 4,
        'R32G32': 8, 'R32G32_FLOAT': 8,
        'R32': 4, 'R32_FLOAT': 4, 'D32_FLOAT': 4,
        'R16': 2, 'R16_FLOAT': 2, 'D16_UNORM': 2,
        'R8': 1, 'R8_UNORM': 1, 'A8_UNORM': 1,
        'R11G11B10': 4, 'R11G11B10_FLOAT': 4,
        'R10G10B10A2': 4, 'R10G10B10A2_UNORM': 4,
        'D24_UNORM_S8_UINT': 4, 'D32_FLOAT_S8X24_UINT': 8,
        'BC1': 0.5, 'BC1_UNORM': 0.5, 'BC1_UNORM_SRGB': 0.5,
        'BC2': 1, 'BC2_UNORM': 1, 'BC2_UNORM_SRGB': 1,
        'BC3': 1, 'BC3_UNORM': 1, 'BC3_UNORM_SRGB': 1,
        'BC4': 0.5, 'BC4_UNORM': 0.5, 'BC4_SNORM': 0.5,
        'BC5': 1, 'BC5_UNORM': 1, 'BC5_SNORM': 1,
        'BC6H': 1, 'BC6H_UF16': 1, 'BC6H_SF16': 1,
        'BC7': 1, 'BC7_UNORM': 1, 'BC7_UNORM_SRGB': 1,
        'ASTC_4x4': 1, 'ASTC_5x5': 0.64, 'ASTC_6x6': 0.44,
        'ASTC_8x8': 0.25, 'ASTC_10x10': 0.16, 'ASTC_12x12': 0.11,
    }
    
    # 查找匹配的格式
    bpp = 4  # 默认 4 字节/像素
    for fmt, b in bpp_map.items():
        if fmt in format_name:
            bpp = b
            break
    
    # 计算大小
    width = tex.width if tex.width > 0 else 1
    height = tex.height if tex.height > 0 else 1
    depth = tex.depth if tex.depth > 0 else 1
    array_size = tex.arraysize if tex.arraysize > 0 else 1
    mips = tex.mips if tex.mips > 0 else 1
    
    # 计算所有 mip 级别的大小
    total_size = 0
    for mip in range(mips):
        mip_w = max(1, width >> mip)
        mip_h = max(1, height >> mip)
        mip_d = max(1, depth >> mip)
        mip_size = int(mip_w * mip_h * mip_d * bpp)
        total_size += mip_size
    
    # 乘以数组大小和采样数
    total_size *= array_size
    if hasattr(tex, 'msQual') and tex.msQual > 1:
        total_size *= tex.msQual
    
    return total_size

def analyze_unused_resources(rdc_path):
    """分析未使用的资源"""
    
    try:
        import renderdoc as rd
    except ImportError:
        print("错误: 无法导入 renderdoc 模块")
        sys.exit(1)
    
    if not os.path.exists(rdc_path):
        print(f"错误: 文件不存在 - {rdc_path}")
        sys.exit(1)
    
    print(f"正在分析未使用资源: {rdc_path}")
    print("=" * 80)
    
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
    
    # 构建有效使用类型列表（排除 Barrier, Discard 等无效使用）
    VALID_USAGES = set()
    INVALID_USAGE_NAMES = ['Barrier', 'Discard', 'Unused']
    
    for name in dir(rd.ResourceUsage):
        if not name.startswith('_'):
            val = getattr(rd.ResourceUsage, name)
            if name not in INVALID_USAGE_NAMES:
                VALID_USAGES.add(int(val))
    
    # 获取所有资源
    textures = controller.GetTextures()
    buffers = controller.GetBuffers()
    
    print(f"\n资源统计:")
    print(f"  Texture 数量: {len(textures)}")
    print(f"  Buffer 数量:  {len(buffers)}")
    
    # ========== 分析 Textures ==========
    print(f"\n{'='*80}")
    print("                    分析 Texture 资源")
    print("=" * 80)
    
    tex_used = []
    tex_unused = []
    tex_used_size = 0
    tex_unused_size = 0
    
    for tex in textures:
        usage = controller.GetUsage(tex.resourceId)
        
        # 过滤有效使用
        valid_usage_count = sum(1 for u in usage if int(u.usage) in VALID_USAGES)
        
        size = estimate_texture_size(tex)
        format_name = tex.format.Name() if hasattr(tex.format, 'Name') else str(tex.format)
        
        info = {
            'id': str(tex.resourceId),
            'size': size,
            'dims': f"{tex.width}x{tex.height}",
            'format': format_name,
            'mips': tex.mips,
            'array': tex.arraysize,
            'usage_count': valid_usage_count,
            'total_usage': len(usage)
        }
        
        if valid_usage_count > 0:
            tex_used.append(info)
            tex_used_size += size
        else:
            tex_unused.append(info)
            tex_unused_size += size
    
    print(f"\n  已使用 Texture: {len(tex_used)} 个, 占用 {format_size(tex_used_size)}")
    print(f"  未使用 Texture: {len(tex_unused)} 个, 占用 {format_size(tex_unused_size)}")
    
    if tex_unused:
        # 按大小排序
        tex_unused.sort(key=lambda x: x['size'], reverse=True)
        
        print(f"\n  未使用的 Texture (按大小排序, 显示前 30 个):")
        print(f"  {'ResourceId':<25} {'尺寸':<15} {'格式':<25} {'大小':<12}")
        print(f"  {'-'*75}")
        
        for info in tex_unused[:30]:
            print(f"  {info['id']:<25} {info['dims']:<15} {info['format']:<25} {format_size(info['size']):<12}")
        
        if len(tex_unused) > 30:
            print(f"  ... 还有 {len(tex_unused) - 30} 个未显示")
    
    # ========== 分析 Buffers ==========
    print(f"\n{'='*80}")
    print("                    分析 Buffer 资源")
    print("=" * 80)
    
    buf_used = []
    buf_unused = []
    buf_used_size = 0
    buf_unused_size = 0
    
    for buf in buffers:
        usage = controller.GetUsage(buf.resourceId)
        
        # 过滤有效使用
        valid_usage_count = sum(1 for u in usage if int(u.usage) in VALID_USAGES)
        
        size = buf.length
        
        info = {
            'id': str(buf.resourceId),
            'size': size,
            'usage_count': valid_usage_count,
            'total_usage': len(usage)
        }
        
        if valid_usage_count > 0:
            buf_used.append(info)
            buf_used_size += size
        else:
            buf_unused.append(info)
            buf_unused_size += size
    
    print(f"\n  已使用 Buffer: {len(buf_used)} 个, 占用 {format_size(buf_used_size)}")
    print(f"  未使用 Buffer: {len(buf_unused)} 个, 占用 {format_size(buf_unused_size)}")
    
    if buf_unused:
        # 按大小排序
        buf_unused.sort(key=lambda x: x['size'], reverse=True)
        
        print(f"\n  未使用的 Buffer (按大小排序, 显示前 30 个):")
        print(f"  {'ResourceId':<25} {'大小':<15}")
        print(f"  {'-'*40}")
        
        for info in buf_unused[:30]:
            print(f"  {info['id']:<25} {format_size(info['size']):<15}")
        
        if len(buf_unused) > 30:
            print(f"  ... 还有 {len(buf_unused) - 30} 个未显示")
    
    # ========== 汇总 ==========
    print(f"\n{'='*80}")
    print("                         汇总")
    print("=" * 80)
    
    total_used = tex_used_size + buf_used_size
    total_unused = tex_unused_size + buf_unused_size
    total = total_used + total_unused
    
    print(f"\n  资源类型        已使用数量    未使用数量    已使用大小        未使用大小")
    print(f"  {'-'*75}")
    print(f"  Texture         {len(tex_used):>6}        {len(tex_unused):>6}        {format_size(tex_used_size):>12}      {format_size(tex_unused_size):>12}")
    print(f"  Buffer          {len(buf_used):>6}        {len(buf_unused):>6}        {format_size(buf_used_size):>12}      {format_size(buf_unused_size):>12}")
    print(f"  {'-'*75}")
    print(f"  总计            {len(tex_used)+len(buf_used):>6}        {len(tex_unused)+len(buf_unused):>6}        {format_size(total_used):>12}      {format_size(total_unused):>12}")
    
    if total > 0:
        waste_ratio = total_unused / total * 100
        print(f"\n  📊 显存利用率: {100-waste_ratio:.1f}%")
        print(f"  ⚠️  浪费显存:   {format_size(total_unused)} ({waste_ratio:.1f}%)")
    
    # ========== 按类别统计未使用 Texture ==========
    if tex_unused:
        print(f"\n{'='*80}")
        print("                 未使用 Texture 按尺寸分类")
        print("=" * 80)
        
        size_categories = defaultdict(lambda: {'count': 0, 'size': 0})
        for info in tex_unused:
            dims = info['dims']
            size_categories[dims]['count'] += 1
            size_categories[dims]['size'] += info['size']
        
        # 按总大小排序
        sorted_cats = sorted(size_categories.items(), key=lambda x: x[1]['size'], reverse=True)
        
        print(f"\n  {'尺寸':<20} {'数量':<10} {'总大小':<15}")
        print(f"  {'-'*45}")
        for dims, data in sorted_cats[:15]:
            print(f"  {dims:<20} {data['count']:<10} {format_size(data['size']):<15}")
    
    controller.Shutdown()
    cap.Shutdown()
    print("\n分析完成!")


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_unused_resources.py <rdc_file_path>")
        sys.exit(1)
    analyze_unused_resources(sys.argv[1])

if __name__ == "__main__":
    main()
