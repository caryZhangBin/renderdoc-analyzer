#!/usr/bin/env python3
"""
RenderDoc Android 内存分析脚本

用法: python analyze_memory_android.py <android_rdc_path> [--host <ip>] [--port <port>]

功能:
- 统计 GPU 纹理内存占用
- 统计 Buffer 内存占用
- 分析内存按格式/用途分布
- 检测大纹理和潜在的内存浪费
"""

import sys
import os
import argparse
from collections import defaultdict

# 自动添加 RenderDoc Python 模块路径
RENDERDOC_MODULE_PATHS = [
    r"E:\code build\renderdoc-1.x\renderdoc-1.x\x64\Development\pymodules",
    r"E:\code build\RenderDoc_1.37_64",
    r"C:\Program Files\RenderDoc",
]
for path in RENDERDOC_MODULE_PATHS:
    if os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)
        break

import renderdoc as rd

# 默认远程服务器配置
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 38920


def format_size(size_bytes):
    """格式化字节大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_texture_size(tex):
    """估算纹理大小"""
    try:
        width = tex.width
        height = max(1, tex.height)
        depth = max(1, tex.depth)
        array_size = max(1, tex.arraysize)
        mips = max(1, tex.mips)
        
        # 估算每像素字节数
        fmt_str = str(tex.format.type).lower() if hasattr(tex.format, 'type') else str(tex.format).lower()
        
        if 'bc1' in fmt_str or 'dxt1' in fmt_str:
            bytes_per_pixel = 0.5
        elif 'bc2' in fmt_str or 'bc3' in fmt_str or 'bc5' in fmt_str or 'bc7' in fmt_str:
            bytes_per_pixel = 1
        elif 'bc4' in fmt_str:
            bytes_per_pixel = 0.5
        elif 'bc6' in fmt_str:
            bytes_per_pixel = 1
        elif 'r32g32b32a32' in fmt_str:
            bytes_per_pixel = 16
        elif 'r32g32b32' in fmt_str:
            bytes_per_pixel = 12
        elif 'r32g32' in fmt_str:
            bytes_per_pixel = 8
        elif 'r32' in fmt_str or 'd32' in fmt_str:
            bytes_per_pixel = 4
        elif 'r16g16b16a16' in fmt_str:
            bytes_per_pixel = 8
        elif 'r16g16' in fmt_str:
            bytes_per_pixel = 4
        elif 'r16' in fmt_str or 'd16' in fmt_str:
            bytes_per_pixel = 2
        elif 'r11g11b10' in fmt_str:
            bytes_per_pixel = 4
        elif 'r10g10b10a2' in fmt_str:
            bytes_per_pixel = 4
        elif 'd24' in fmt_str or 'd32' in fmt_str:
            bytes_per_pixel = 4
        elif 'r8g8b8a8' in fmt_str or 'b8g8r8a8' in fmt_str:
            bytes_per_pixel = 4
        elif 'r8g8' in fmt_str:
            bytes_per_pixel = 2
        elif 'r8' in fmt_str:
            bytes_per_pixel = 1
        elif 'astc' in fmt_str:
            bytes_per_pixel = 1  # ASTC 压缩
        elif 'etc2' in fmt_str or 'etc1' in fmt_str:
            bytes_per_pixel = 0.5  # ETC 压缩
        else:
            bytes_per_pixel = 4
        
        # 计算 mipmap 总大小
        total_size = 0
        for mip in range(mips):
            mip_w = max(1, width >> mip)
            mip_h = max(1, height >> mip)
            mip_d = max(1, depth >> mip)
            total_size += mip_w * mip_h * mip_d * bytes_per_pixel
        
        total_size *= array_size
        return int(total_size)
        
    except Exception as e:
        return 0


def setup_adb_port_forward():
    """设置 ADB 端口转发"""
    import subprocess
    try:
        result = subprocess.run(
            ["adb", "forward", f"tcp:{DEFAULT_PORT}", f"tcp:{DEFAULT_PORT}"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"✅ ADB 端口转发设置成功: tcp:{DEFAULT_PORT}")
            return True
        else:
            print(f"⚠️ ADB 端口转发失败: {result.stderr}")
            return False
    except FileNotFoundError:
        print("⚠️ 未找到 adb 命令，请确保已安装 Android SDK 并配置环境变量")
        return False


def connect_to_remote_server(host, port):
    """连接到远程 RenderDoc 服务器"""
    print(f"\n正在连接远程服务器 {host}:{port}...")
    
    try:
        result, remote = rd.CreateRemoteServerConnection(host, port, None)
        
        if result != rd.ResultCode.Succeeded:
            print(f"❌ 连接失败: {result}")
            print("\n可能的原因:")
            print("  1. Android 上的 RenderDoc Replay Server 未启动")
            print("  2. ADB 端口转发未设置: adb forward tcp:38920 tcp:38920")
            print("  3. 设备不在同一网络或端口被防火墙阻止")
            return None
        
        print(f"✅ 成功连接到远程服务器")
        home_path = remote.HomeFolder()
        print(f"   远程设备目录: {home_path}")
        
        return remote
        
    except Exception as e:
        print(f"❌ 连接异常: {e}")
        return None


def open_remote_capture(remote, rdc_path):
    """在远程设备上打开 RDC 文件"""
    print(f"\n正在打开远程 RDC 文件: {rdc_path}")
    
    try:
        local_progress = None
        result, path_or_error = remote.CopyCaptureToRemote(rdc_path, local_progress)
        
        if result != rd.ResultCode.Succeeded:
            print(f"   文件复制跳过，尝试直接打开...")
            remote_path = rdc_path
        else:
            remote_path = path_or_error
            print(f"   文件已复制到远程: {remote_path}")
        
        result, controller = remote.OpenCapture(0, remote_path, rd.ReplayOptions(), None)
        
        if result != rd.ResultCode.Succeeded:
            print(f"❌ 无法打开捕获文件: {result}")
            return None
        
        print(f"✅ 成功打开捕获文件")
        return controller
        
    except Exception as e:
        print(f"❌ 打开捕获文件异常: {e}")
        import traceback
        traceback.print_exc()
        return None


def analyze_memory_remote(controller):
    """分析内存使用情况（远程版本）"""
    
    print("\n正在统计资源内存...", flush=True)
    
    # 统计变量
    texture_memory = 0
    buffer_memory = 0
    texture_count = 0
    buffer_count = 0
    
    # 按格式统计
    format_stats = defaultdict(lambda: {'count': 0, 'size': 0})
    # 按用途统计
    usage_stats = defaultdict(lambda: {'count': 0, 'size': 0})
    # 大纹理列表
    large_textures = []
    # 纹理尺寸分布
    texture_size_distribution = defaultdict(int)
    
    # 处理纹理
    textures = controller.GetTextures()
    for tex in textures:
        texture_count += 1
        size = get_texture_size(tex)
        texture_memory += size
        
        # 格式统计
        fmt_name = str(tex.format.type) if hasattr(tex.format, 'type') else str(tex.format)
        format_stats[fmt_name]['count'] += 1
        format_stats[fmt_name]['size'] += size
        
        # 用途统计
        if hasattr(tex, 'creationFlags') and hasattr(rd, 'TextureCategory'):
            flags = tex.creationFlags
            if flags & rd.TextureCategory.ColorTarget:
                usage_stats['RenderTarget']['count'] += 1
                usage_stats['RenderTarget']['size'] += size
            elif flags & rd.TextureCategory.DepthTarget:
                usage_stats['DepthStencil']['count'] += 1
                usage_stats['DepthStencil']['size'] += size
            elif flags & rd.TextureCategory.ShaderRead:
                usage_stats['ShaderResource']['count'] += 1
                usage_stats['ShaderResource']['size'] += size
            else:
                usage_stats['Other']['count'] += 1
                usage_stats['Other']['size'] += size
        
        # 大纹理检测 (> 4MB)
        if size > 4 * 1024 * 1024:
            large_textures.append({
                'id': str(tex.resourceId),
                'width': tex.width,
                'height': tex.height,
                'depth': tex.depth,
                'mips': tex.mips,
                'format': fmt_name,
                'size': size
            })
        
        # 尺寸分布
        max_dim = max(tex.width, tex.height)
        if max_dim <= 64:
            texture_size_distribution['<= 64'] += 1
        elif max_dim <= 256:
            texture_size_distribution['65 - 256'] += 1
        elif max_dim <= 512:
            texture_size_distribution['257 - 512'] += 1
        elif max_dim <= 1024:
            texture_size_distribution['513 - 1024'] += 1
        elif max_dim <= 2048:
            texture_size_distribution['1025 - 2048'] += 1
        else:
            texture_size_distribution['> 2048'] += 1
    
    # 处理 Buffer
    buffers = controller.GetBuffers()
    for buf in buffers:
        buffer_count += 1
        size = buf.length
        buffer_memory += size
    
    large_textures.sort(key=lambda x: x['size'], reverse=True)
    
    return {
        'texture_memory': texture_memory,
        'buffer_memory': buffer_memory,
        'texture_count': texture_count,
        'buffer_count': buffer_count,
        'format_stats': dict(format_stats),
        'usage_stats': dict(usage_stats),
        'large_textures': large_textures[:20],  # Top 20
        'texture_size_distribution': dict(texture_size_distribution)
    }


def print_memory_report(results):
    """打印内存分析报告"""
    
    print("\n" + "=" * 70)
    print("                      📊 GPU 内存使用总览")
    print("=" * 70)
    
    total_memory = results['texture_memory'] + results['buffer_memory']
    
    print(f"\n  总 GPU 内存占用:        {format_size(total_memory)}")
    print(f"  ├─ 纹理内存:            {format_size(results['texture_memory'])} ({results['texture_count']} 个)")
    print(f"  └─ Buffer 内存:         {format_size(results['buffer_memory'])} ({results['buffer_count']} 个)")
    
    if total_memory > 0:
        tex_ratio = results['texture_memory'] / total_memory * 100
        buf_ratio = results['buffer_memory'] / total_memory * 100
        print(f"\n  内存分布: 纹理 {tex_ratio:.1f}% / Buffer {buf_ratio:.1f}%")
    
    # 按用途统计
    print("\n" + "-" * 70)
    print("                    📦 按用途分类")
    print("-" * 70)
    
    usage_stats = results['usage_stats']
    if usage_stats:
        print(f"\n  {'用途':<20} {'数量':>10} {'大小':>15}")
        print("  " + "-" * 50)
        for usage_type, stats in sorted(usage_stats.items(), key=lambda x: -x[1]['size']):
            print(f"  {usage_type:<20} {stats['count']:>10} {format_size(stats['size']):>15}")
    
    # 按格式统计
    print("\n" + "-" * 70)
    print("                    🎨 按格式分类 (Top 15)")
    print("-" * 70)
    
    format_stats = results['format_stats']
    if format_stats:
        print(f"\n  {'格式':<30} {'数量':>8} {'大小':>15}")
        print("  " + "-" * 55)
        sorted_formats = sorted(format_stats.items(), key=lambda x: -x[1]['size'])
        for fmt_name, stats in sorted_formats[:15]:
            fmt_display = fmt_name[:28] + ".." if len(fmt_name) > 30 else fmt_name
            print(f"  {fmt_display:<30} {stats['count']:>8} {format_size(stats['size']):>15}")
    
    # 纹理尺寸分布
    print("\n" + "-" * 70)
    print("                    📐 纹理尺寸分布")
    print("-" * 70)
    
    dist = results['texture_size_distribution']
    if dist:
        print(f"\n  {'尺寸范围':<20} {'数量':>10}")
        print("  " + "-" * 35)
        size_order = ['<= 64', '65 - 256', '257 - 512', '513 - 1024', '1025 - 2048', '> 2048']
        for size_range in size_order:
            if size_range in dist:
                print(f"  {size_range:<20} {dist[size_range]:>10}")
    
    # 大纹理列表
    large_textures = results['large_textures']
    if large_textures:
        print("\n" + "-" * 70)
        print("                    ⚠️ 大纹理列表 (> 4MB)")
        print("-" * 70)
        
        print(f"\n  {'尺寸':<20} {'格式':<25} {'大小':>12}")
        print("  " + "-" * 60)
        for tex in large_textures[:15]:
            dim_str = f"{tex['width']}x{tex['height']}"
            if tex['depth'] > 1:
                dim_str += f"x{tex['depth']}"
            if tex['mips'] > 1:
                dim_str += f" ({tex['mips']}mip)"
            
            fmt_display = tex['format'][:23] + ".." if len(tex['format']) > 25 else tex['format']
            print(f"  {dim_str:<20} {fmt_display:<25} {format_size(tex['size']):>12}")
    
    # 优化建议
    print("\n" + "=" * 70)
    print("                       💡 内存优化建议")
    print("=" * 70)
    
    suggestions = []
    
    if results['texture_memory'] > 500 * 1024 * 1024:
        suggestions.append(f"  • 纹理内存较高 ({format_size(results['texture_memory'])})，考虑使用纹理压缩 (ASTC/ETC2)")
    
    if len(large_textures) > 5:
        suggestions.append(f"  • 存在 {len(large_textures)} 个大纹理 (> 4MB)，检查是否可以降低分辨率")
    
    # 检查是否有非压缩格式
    uncompressed_size = 0
    for fmt_name, stats in format_stats.items():
        fmt_lower = fmt_name.lower()
        if 'bc' not in fmt_lower and 'astc' not in fmt_lower and 'etc' not in fmt_lower:
            if 'r8g8b8a8' in fmt_lower or 'r16' in fmt_lower or 'r32' in fmt_lower:
                uncompressed_size += stats['size']
    
    if uncompressed_size > 100 * 1024 * 1024:
        suggestions.append(f"  • 非压缩纹理占用 {format_size(uncompressed_size)}，考虑转换为压缩格式")
    
    if not suggestions:
        print("  ✅ 内存使用情况良好，没有明显问题")
    else:
        for s in suggestions:
            print(s)


def main():
    parser = argparse.ArgumentParser(description='RenderDoc Android 内存分析')
    parser.add_argument('rdc_path', help='Android 设备上的 RDC 文件路径')
    parser.add_argument('--host', default=DEFAULT_HOST, help=f'远程服务器地址 (默认: {DEFAULT_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'远程服务器端口 (默认: {DEFAULT_PORT})')
    parser.add_argument('--no-forward', action='store_true', help='跳过 ADB 端口转发设置')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("       RenderDoc Android 内存分析工具")
    print("=" * 70)
    
    if not args.no_forward and args.host == "localhost":
        setup_adb_port_forward()
    
    remote = connect_to_remote_server(args.host, args.port)
    if remote is None:
        sys.exit(1)
    
    controller = open_remote_capture(remote, args.rdc_path)
    if controller is None:
        remote.Shutdown()
        sys.exit(1)
    
    try:
        print("\n" + "=" * 70)
        print("                    分析内存使用")
        print("=" * 70)
        results = analyze_memory_remote(controller)
        print_memory_report(results)
        
    finally:
        controller.Shutdown()
        remote.Shutdown()
    
    print("\n" + "=" * 70)
    print("                         分析完成!")
    print("=" * 70)


if __name__ == "__main__":
    main()
