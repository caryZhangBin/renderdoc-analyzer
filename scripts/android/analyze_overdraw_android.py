#!/usr/bin/env python3
"""
RenderDoc Android Overdraw 分析脚本

用法: python analyze_overdraw_android.py <android_rdc_path> [--host <ip>] [--port <port>]

功能:
- 分析每个 Pass 的 Drawcall 密度
- 检测可能存在 Overdraw 问题的区域
- 统计透明物体渲染次数
- 提供 Overdraw 优化建议
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


def analyze_overdraw_remote(controller):
    """分析 Overdraw 情况（远程版本）"""
    
    # 统计变量
    pass_draw_stats = []
    rt_usage_count = defaultdict(int)
    transparent_passes = []
    fullscreen_draws = []
    eid_overdraw_stats = []
    
    # 获取帧信息来计算分辨率
    textures = controller.GetTextures()
    rt_resolutions = {}
    max_rt_width = 0
    max_rt_height = 0
    
    for tex in textures:
        if hasattr(tex, 'creationFlags') and hasattr(rd, 'TextureCategory'):
            if tex.creationFlags & rd.TextureCategory.ColorTarget:
                res = (tex.width, tex.height)
                rt_resolutions[res] = rt_resolutions.get(res, 0) + 1
                if tex.width > max_rt_width:
                    max_rt_width = tex.width
                    max_rt_height = tex.height
    
    # 找最常见的分辨率作为主屏幕
    main_screen_width = 1920
    main_screen_height = 1080
    
    if rt_resolutions:
        candidate_resolutions = [
            (w, h, cnt) for (w, h), cnt in rt_resolutions.items() 
            if w >= 256 and h >= 256 and w != h
        ]
        if candidate_resolutions:
            candidate_resolutions.sort(key=lambda x: -x[2])
            main_screen_width, main_screen_height, _ = candidate_resolutions[0]
        else:
            for (w, h), cnt in sorted(rt_resolutions.items(), key=lambda x: -x[1]):
                if w <= 4096 and h <= 4096:
                    main_screen_width, main_screen_height = w, h
                    break
    
    main_screen_pixels = main_screen_width * main_screen_height
    total_screen_pixels = main_screen_pixels
    
    print(f"\n  检测到的最大 RT 分辨率: {max_rt_width} x {max_rt_height}")
    print(f"  使用主屏幕分辨率计算: {main_screen_width} x {main_screen_height}")
    
    # Pass 分析
    current_pass = {
        'name': 'Root',
        'drawcalls': 0,
        'estimated_pixels': 0,
        'rt_count': 0,
        'has_blend': False,
        'event_start': 0
    }
    
    def estimate_draw_pixels(action, screen_pixels):
        """估算 Drawcall 的像素量"""
        num_verts = action.numIndices if hasattr(action, 'numIndices') and action.numIndices > 0 else 0
        if num_verts <= 6:
            return screen_pixels  # 全屏
        
        instances = max(1, action.numInstances) if hasattr(action, 'numInstances') else 1
        triangles = num_verts // 3 * instances
        
        avg_coverage = 500
        return min(triangles * avg_coverage, screen_pixels * instances)
    
    def process_action(action, depth=0):
        """递归处理 Action"""
        nonlocal current_pass
        
        is_pass_marker = (action.flags & rd.ActionFlags.PushMarker) and action.children
        
        if is_pass_marker:
            # 保存上一个 Pass 的统计
            if current_pass['drawcalls'] > 0:
                overdraw = current_pass['estimated_pixels'] / total_screen_pixels if total_screen_pixels > 0 else 0
                pass_draw_stats.append({
                    'name': current_pass['name'],
                    'drawcalls': current_pass['drawcalls'],
                    'pixels': current_pass['estimated_pixels'],
                    'overdraw': overdraw,
                    'has_blend': current_pass['has_blend']
                })
                
                name_lower = current_pass['name'].lower()
                if current_pass['has_blend'] or 'transparent' in name_lower or 'alpha' in name_lower:
                    transparent_passes.append({
                        'name': current_pass['name'],
                        'drawcalls': current_pass['drawcalls'],
                        'overdraw': overdraw
                    })
            
            # 开始新 Pass
            current_pass = {
                'name': action.customName or f"Pass_{action.eventId}",
                'drawcalls': 0,
                'estimated_pixels': 0,
                'rt_count': 0,
                'has_blend': False,
                'event_start': action.eventId
            }
        
        # 统计 Drawcall
        if action.flags & rd.ActionFlags.Drawcall:
            current_pass['drawcalls'] += 1
            
            pixels = estimate_draw_pixels(action, total_screen_pixels)
            current_pass['estimated_pixels'] += pixels
            
            eid_overdraw = pixels / total_screen_pixels if total_screen_pixels > 0 else 0
            eid_overdraw_stats.append({
                'eid': action.eventId,
                'name': action.customName or f"Draw_{action.eventId}",
                'pass': current_pass['name'],
                'pixels': pixels,
                'overdraw': eid_overdraw,
                'num_verts': action.numIndices if hasattr(action, 'numIndices') else 0,
                'num_instances': action.numInstances if hasattr(action, 'numInstances') else 1
            })
            
            num_verts = action.numIndices if hasattr(action, 'numIndices') else 0
            if num_verts <= 6 and num_verts > 0:
                fullscreen_draws.append({
                    'name': action.customName or f"Draw_{action.eventId}",
                    'event_id': action.eventId,
                    'pass': current_pass['name']
                })
        
        for child in action.children:
            process_action(child, depth + 1)
    
    print("\n正在扫描所有 Action...", flush=True)
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    # 保存最后一个 Pass
    if current_pass['drawcalls'] > 0:
        overdraw = current_pass['estimated_pixels'] / total_screen_pixels if total_screen_pixels > 0 else 0
        pass_draw_stats.append({
            'name': current_pass['name'],
            'drawcalls': current_pass['drawcalls'],
            'pixels': current_pass['estimated_pixels'],
            'overdraw': overdraw,
            'has_blend': current_pass['has_blend']
        })
    
    # 分析 RT 使用情况
    try:
        resources = controller.GetResources()
        for res in resources:
            try:
                usage = controller.GetUsage(res.resourceId)
                write_count = 0
                for u in usage:
                    if u.usage in [rd.ResourceUsage.ColorTarget, rd.ResourceUsage.DepthStencilTarget,
                                  rd.ResourceUsage.RenderTarget]:
                        write_count += 1
                if write_count > 0:
                    rt_usage_count[str(res.resourceId)] = write_count
            except:
                pass
    except:
        pass
    
    return {
        'pass_draw_stats': pass_draw_stats,
        'rt_usage_count': dict(rt_usage_count),
        'transparent_passes': transparent_passes,
        'fullscreen_draws': fullscreen_draws,
        'eid_overdraw_stats': eid_overdraw_stats,
        'main_screen_width': main_screen_width,
        'main_screen_height': main_screen_height,
        'total_screen_pixels': total_screen_pixels
    }


def print_overdraw_report(results):
    """打印 Overdraw 分析报告"""
    pass_draw_stats = results['pass_draw_stats']
    transparent_passes = results['transparent_passes']
    fullscreen_draws = results['fullscreen_draws']
    eid_overdraw_stats = results['eid_overdraw_stats']
    rt_usage_count = results['rt_usage_count']
    total_screen_pixels = results['total_screen_pixels']
    
    print("\n" + "=" * 70)
    print("                      📊 Overdraw 分析总览")
    print("=" * 70)
    
    total_draws = sum(p['drawcalls'] for p in pass_draw_stats)
    total_overdraw_pixels = sum(p['pixels'] for p in pass_draw_stats)
    avg_overdraw = total_overdraw_pixels / total_screen_pixels if total_screen_pixels > 0 else 0
    
    print(f"  主屏幕分辨率:           {results['main_screen_width']} x {results['main_screen_height']}")
    print(f"  总 Drawcall 数:         {total_draws:,}")
    print(f"  估算总像素写入量:       {total_overdraw_pixels:,}")
    print(f"  屏幕像素数:             {total_screen_pixels:,}")
    print(f"  平均 Overdraw 倍数:     {avg_overdraw:.2f}x")
    
    # Overdraw 评级
    if avg_overdraw < 2:
        rating = "✅ 优秀"
    elif avg_overdraw < 3:
        rating = "👍 良好"
    elif avg_overdraw < 5:
        rating = "⚠️ 一般"
    else:
        rating = "❌ 较差"
    print(f"  Overdraw 评级:          {rating}")
    
    # 按 Overdraw 排序的 Pass
    pass_draw_stats.sort(key=lambda x: x['overdraw'], reverse=True)
    
    print("\n" + "-" * 70)
    print("                 🏆 Overdraw 最高的 Pass (Top 15)")
    print("-" * 70)
    print(f"  {'Pass 名称':<35} {'Drawcall':>10} {'Overdraw':>12}")
    print("-" * 70)
    for p in pass_draw_stats[:15]:
        name = p['name'][:33] + ".." if len(p['name']) > 35 else p['name']
        overdraw_str = f"{p['overdraw']:.2f}x"
        print(f"  {name:<35} {p['drawcalls']:>10} {overdraw_str:>12}")
    
    # 按 EID 输出 Overdraw > 3x 的 Drawcall
    high_overdraw_eids = [e for e in eid_overdraw_stats if e['overdraw'] > 3]
    high_overdraw_eids.sort(key=lambda x: x['overdraw'], reverse=True)
    
    print("\n" + "-" * 70)
    print("            🔥 Overdraw > 3x 的 Drawcall (按 EID)")
    print("-" * 70)
    
    if high_overdraw_eids:
        print(f"  共发现 {len(high_overdraw_eids)} 个 Drawcall 的 Overdraw > 3x\n")
        print(f"  {'EID':<10} {'Overdraw':>10} {'顶点数':>12} {'实例数':>10}")
        print("  " + "-" * 50)
        for e in high_overdraw_eids[:30]:
            print(f"  {e['eid']:<10} {e['overdraw']:>9.2f}x {e['num_verts']:>12,} {e['num_instances']:>10,}")
        
        if len(high_overdraw_eids) > 30:
            print(f"\n  ... 还有 {len(high_overdraw_eids) - 30} 个未显示")
    else:
        print("  ✅ 没有发现 Overdraw > 3x 的 Drawcall")
    
    # 透明 Pass 分析
    if transparent_passes:
        print("\n" + "-" * 70)
        print("                    🔮 透明物体渲染分析")
        print("-" * 70)
        print(f"  透明 Pass 数量: {len(transparent_passes)}")
        total_transparent_draws = sum(p['drawcalls'] for p in transparent_passes)
        print(f"  透明 Drawcall 总数: {total_transparent_draws}")
        
        if total_draws > 0:
            transparent_ratio = total_transparent_draws / total_draws * 100
            print(f"  透明 Drawcall 占比: {transparent_ratio:.1f}%")
    
    # 全屏绘制分析
    print("\n" + "-" * 70)
    print("                    📺 全屏绘制分析")
    print("-" * 70)
    print(f"  全屏绘制次数: {len(fullscreen_draws)}")
    
    if len(fullscreen_draws) > 0:
        fs_by_pass = defaultdict(int)
        for fs in fullscreen_draws:
            fs_by_pass[fs['pass']] += 1
        
        print("\n  按 Pass 分布 (Top 10):")
        for pass_name, count in sorted(fs_by_pass.items(), key=lambda x: -x[1])[:10]:
            name = pass_name[:40] + ".." if len(pass_name) > 42 else pass_name
            print(f"    {name}: {count} 次")
    
    # 优化建议
    print("\n" + "=" * 70)
    print("                       💡 Overdraw 优化建议")
    print("=" * 70)
    
    suggestions = []
    
    if avg_overdraw > 3:
        suggestions.append("  • 平均 Overdraw 较高，考虑实现深度预渲染 (Z-Prepass)")
    
    if len(transparent_passes) > 10:
        suggestions.append(f"  • 透明 Pass 较多 ({len(transparent_passes)} 个)，考虑合并透明批次")
    
    if len(fullscreen_draws) > 20:
        suggestions.append(f"  • 全屏绘制较多 ({len(fullscreen_draws)} 次)，考虑合并后处理 Pass")
    
    high_overdraw_passes = [p for p in pass_draw_stats if p['overdraw'] > 2]
    if len(high_overdraw_passes) > 5:
        suggestions.append(f"  • {len(high_overdraw_passes)} 个 Pass 的 Overdraw > 2x，考虑启用遮挡剔除")
    
    if not suggestions:
        print("  ✅ Overdraw 情况良好，没有明显问题")
    else:
        for s in suggestions:
            print(s)


def main():
    parser = argparse.ArgumentParser(description='RenderDoc Android Overdraw 分析')
    parser.add_argument('rdc_path', help='Android 设备上的 RDC 文件路径')
    parser.add_argument('--host', default=DEFAULT_HOST, help=f'远程服务器地址 (默认: {DEFAULT_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'远程服务器端口 (默认: {DEFAULT_PORT})')
    parser.add_argument('--no-forward', action='store_true', help='跳过 ADB 端口转发设置')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("       RenderDoc Android Overdraw 分析工具")
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
        print("                    分析 Overdraw")
        print("=" * 70)
        results = analyze_overdraw_remote(controller)
        print_overdraw_report(results)
        
    finally:
        controller.Shutdown()
        remote.Shutdown()
    
    print("\n" + "=" * 70)
    print("                         分析完成!")
    print("=" * 70)


if __name__ == "__main__":
    main()
