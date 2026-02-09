#!/usr/bin/env python3
"""
RenderDoc Overdraw 分析脚本

用法: python analyze_overdraw.py <rdc_file_path>

功能:
- 分析每个 Pass 的 Drawcall 密度
- 检测可能存在 Overdraw 问题的区域
- 统计透明物体渲染次数
- 提供 Overdraw 优化建议
"""

import sys
import os
from collections import defaultdict

def analyze_overdraw(rdc_path):
    """分析 Overdraw 情况"""
    
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
    
    print(f"正在分析 Overdraw: {rdc_path}")
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
    
    # ============ 统计变量 ============
    pass_draw_stats = []  # (pass_name, drawcall_count, total_pixels, estimated_overdraw)
    rt_usage_count = defaultdict(int)  # RT 被写入次数
    transparent_passes = []  # 透明 Pass 列表
    fullscreen_draws = []  # 全屏绘制列表
    
    # 获取帧信息来计算分辨率
    textures = controller.GetTextures()
    max_rt_width = 0
    max_rt_height = 0
    
    for tex in textures:
        if hasattr(tex, 'creationFlags') and hasattr(rd, 'TextureCategory'):
            if tex.creationFlags & rd.TextureCategory.ColorTarget:
                if tex.width > max_rt_width:
                    max_rt_width = tex.width
                    max_rt_height = tex.height
    
    if max_rt_width == 0:
        max_rt_width = 1920
        max_rt_height = 1080
    
    total_screen_pixels = max_rt_width * max_rt_height
    print(f"  检测到的最大 RT 分辨率: {max_rt_width} x {max_rt_height}")
    
    # ============ Pass 分析 ============
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
        # 如果是全屏 Quad (通常 4 或 6 个顶点)
        num_verts = action.numIndices if hasattr(action, 'numIndices') and action.numIndices > 0 else 0
        if num_verts <= 6:
            return screen_pixels  # 全屏
        
        # 否则估算
        instances = max(1, action.numInstances) if hasattr(action, 'numInstances') else 1
        triangles = num_verts // 3 * instances
        
        # 粗略估算每个三角形覆盖的像素 (假设平均覆盖 100-1000 像素)
        avg_coverage = 500  # 可调参数
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
                
                # 检测透明 Pass
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
            
            # 估算像素量
            pixels = estimate_draw_pixels(action, total_screen_pixels)
            current_pass['estimated_pixels'] += pixels
            
            # 检测全屏绘制
            num_verts = action.numIndices if hasattr(action, 'numIndices') else 0
            if num_verts <= 6 and num_verts > 0:
                fullscreen_draws.append({
                    'name': action.customName or f"Draw_{action.eventId}",
                    'event_id': action.eventId,
                    'pass': current_pass['name']
                })
        
        for child in action.children:
            process_action(child, depth + 1)
    
    # 处理所有 Action
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
    
    # ============ 分析 RT 使用情况 ============
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
    
    # ============ 输出结果 ============
    print("\n" + "=" * 70)
    print("                      📊 Overdraw 分析总览")
    print("=" * 70)
    
    total_draws = sum(p['drawcalls'] for p in pass_draw_stats)
    total_overdraw_pixels = sum(p['pixels'] for p in pass_draw_stats)
    avg_overdraw = total_overdraw_pixels / total_screen_pixels if total_screen_pixels > 0 else 0
    
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
        
        transparent_passes.sort(key=lambda x: x['overdraw'], reverse=True)
        print("\n  高 Overdraw 透明 Pass:")
        for p in transparent_passes[:5]:
            name = p['name'][:40] + ".." if len(p['name']) > 42 else p['name']
            print(f"    {name}: {p['drawcalls']} draws, {p['overdraw']:.2f}x overdraw")
    
    # 全屏绘制分析
    print("\n" + "-" * 70)
    print("                    📺 全屏绘制分析")
    print("-" * 70)
    print(f"  全屏绘制次数: {len(fullscreen_draws)}")
    
    if len(fullscreen_draws) > 0:
        # 按 Pass 分组
        fs_by_pass = defaultdict(int)
        for fs in fullscreen_draws:
            fs_by_pass[fs['pass']] += 1
        
        print("\n  按 Pass 分布:")
        for pass_name, count in sorted(fs_by_pass.items(), key=lambda x: -x[1])[:10]:
            name = pass_name[:40] + ".." if len(pass_name) > 42 else pass_name
            print(f"    {name}: {count} 次")
    
    # RT 重复写入分析
    multi_write_rts = [(rid, count) for rid, count in rt_usage_count.items() if count > 5]
    if multi_write_rts:
        multi_write_rts.sort(key=lambda x: -x[1])
        print("\n" + "-" * 70)
        print("                    📝 RT 多次写入分析")
        print("-" * 70)
        print(f"  被写入超过 5 次的 RT 数量: {len(multi_write_rts)}")
        print("\n  Top 10 高频写入 RT:")
        for rid, count in multi_write_rts[:10]:
            print(f"    {rid}: {count} 次写入")
    
    # 优化建议
    print("\n" + "=" * 70)
    print("                       💡 Overdraw 优化建议")
    print("=" * 70)
    
    suggestions = []
    
    if avg_overdraw > 3:
        suggestions.append("  • 平均 Overdraw 较高，考虑实现深度预渲染 (Z-Prepass)")
    
    if len(transparent_passes) > 10:
        suggestions.append(f"  • 透明 Pass 较多 ({len(transparent_passes)} 个)，考虑合并透明批次或减少透明层数")
    
    if len(fullscreen_draws) > 20:
        suggestions.append(f"  • 全屏绘制较多 ({len(fullscreen_draws)} 次)，考虑合并后处理 Pass")
    
    high_overdraw_passes = [p for p in pass_draw_stats if p['overdraw'] > 2]
    if len(high_overdraw_passes) > 5:
        suggestions.append(f"  • {len(high_overdraw_passes)} 个 Pass 的 Overdraw > 2x，考虑启用遮挡剔除")
    
    if multi_write_rts and len(multi_write_rts) > 10:
        suggestions.append("  • 多个 RT 被频繁写入，检查是否有冗余渲染")
    
    if not suggestions:
        print("  ✅ Overdraw 情况良好，没有明显问题")
    else:
        for s in suggestions:
            print(s)
    
    # 清理
    controller.Shutdown()
    cap.Shutdown()
    
    print("\n" + "=" * 70)
    print("分析完成!")
    print("\n注意: Overdraw 估算基于顶点数启发式，实际值需要结合 GPU 性能工具验证")


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_overdraw.py <rdc_file_path>")
        sys.exit(1)
    
    analyze_overdraw(sys.argv[1])


if __name__ == "__main__":
    main()
