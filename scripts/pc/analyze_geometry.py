#!/usr/bin/env python3
"""
RenderDoc 几何量统计分析脚本

用法: python analyze_geometry.py <rdc_file_path>

功能:
- 统计每个 Pass 的顶点数和三角形数
- 识别高几何负载的 Pass
- 分析 Mesh/Index Buffer 使用情况
"""

import sys
import os
from collections import defaultdict

def analyze_geometry(rdc_path):
    """分析几何量统计"""
    
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
    
    print(f"正在分析几何量: {rdc_path}")
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
    
    # 统计变量
    total_vertices = 0
    total_triangles = 0
    total_drawcalls = 0
    pass_stats = []  # (pass_name, vertices, triangles, drawcall_count)
    drawcall_details = []  # 每个 Drawcall 的详情
    
    current_pass_name = "Root"
    current_pass_verts = 0
    current_pass_tris = 0
    current_pass_draws = 0
    
    def estimate_triangles(topology, num_indices, num_vertices):
        """根据拓扑类型估算三角形数"""
        count = num_indices if num_indices > 0 else num_vertices
        
        if topology == rd.Topology.TriangleList:
            return count // 3
        elif topology == rd.Topology.TriangleStrip:
            return max(0, count - 2)
        elif topology == rd.Topology.TriangleFan:
            return max(0, count - 2)
        elif topology == rd.Topology.TriangleList_Adj:
            return count // 6
        elif topology == rd.Topology.TriangleStrip_Adj:
            return max(0, (count - 4) // 2)
        elif topology == rd.Topology.PointList:
            return 0  # 点不是三角形
        elif topology == rd.Topology.LineList:
            return 0  # 线不是三角形
        elif topology == rd.Topology.LineStrip:
            return 0
        else:
            # 对于其他拓扑类型，假设为三角形列表
            return count // 3
    
    def process_action(action, depth=0, parent_pass="Root"):
        """递归处理 Action"""
        nonlocal total_vertices, total_triangles, total_drawcalls
        nonlocal current_pass_name, current_pass_verts, current_pass_tris, current_pass_draws
        
        is_pass_marker = (action.flags & rd.ActionFlags.PushMarker) and action.children
        
        if is_pass_marker:
            # 保存上一个 Pass 的统计
            if current_pass_draws > 0:
                pass_stats.append((current_pass_name, current_pass_verts, current_pass_tris, current_pass_draws))
            
            # 开始新 Pass
            current_pass_name = action.customName or f"Pass_{action.eventId}"
            current_pass_verts = 0
            current_pass_tris = 0
            current_pass_draws = 0
        
        # 统计 Drawcall
        if action.flags & rd.ActionFlags.Drawcall:
            total_drawcalls += 1
            current_pass_draws += 1
            
            # 获取顶点数
            num_verts = action.numIndices if action.numIndices > 0 else action.numInstances
            if num_verts == 0:
                num_verts = getattr(action, 'vertexCount', 0) or getattr(action, 'numVertices', 0)
            
            # 实例化乘数
            instances = max(1, action.numInstances) if hasattr(action, 'numInstances') else 1
            
            # 计算三角形数
            topology = action.topology if hasattr(action, 'topology') else rd.Topology.TriangleList
            num_indices = action.numIndices if hasattr(action, 'numIndices') else 0
            tris = estimate_triangles(topology, num_indices, num_verts) * instances
            verts = num_verts * instances
            
            total_vertices += verts
            total_triangles += tris
            current_pass_verts += verts
            current_pass_tris += tris
            
            # 记录大型 Drawcall
            if tris > 10000:
                drawcall_details.append({
                    'name': action.customName or f"Draw_{action.eventId}",
                    'event_id': action.eventId,
                    'vertices': verts,
                    'triangles': tris,
                    'instances': instances,
                    'pass': current_pass_name
                })
        
        for child in action.children:
            process_action(child, depth + 1, current_pass_name)
    
    # 处理所有 Action
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    # 保存最后一个 Pass
    if current_pass_draws > 0:
        pass_stats.append((current_pass_name, current_pass_verts, current_pass_tris, current_pass_draws))
    
    # 输出总体统计
    print("\n📊 几何量总体统计")
    print("-" * 50)
    print(f"  总 Drawcall 数:    {total_drawcalls:,}")
    print(f"  总顶点数:          {total_vertices:,}")
    print(f"  总三角形数:        {total_triangles:,}")
    if total_drawcalls > 0:
        print(f"  平均每 Draw 顶点:  {total_vertices // total_drawcalls:,}")
        print(f"  平均每 Draw 三角:  {total_triangles // total_drawcalls:,}")
    
    # 按三角形数排序，找出最大的 Pass
    pass_stats.sort(key=lambda x: x[2], reverse=True)
    
    print("\n🏆 几何量最大的 Pass (Top 15)")
    print("-" * 70)
    print(f"  {'Pass 名称':<35} {'顶点数':>12} {'三角形数':>12} {'Draw数':>8}")
    print("-" * 70)
    for name, verts, tris, draws in pass_stats[:15]:
        display_name = name[:33] + ".." if len(name) > 35 else name
        print(f"  {display_name:<35} {verts:>12,} {tris:>12,} {draws:>8}")
    
    # 几何量分布分析
    print("\n📈 几何量分布分析")
    print("-" * 50)
    
    # 按三角形数分组
    tris_buckets = {
        '> 100K': 0,
        '50K-100K': 0,
        '10K-50K': 0,
        '1K-10K': 0,
        '< 1K': 0
    }
    
    for name, verts, tris, draws in pass_stats:
        if tris > 100000:
            tris_buckets['> 100K'] += 1
        elif tris > 50000:
            tris_buckets['50K-100K'] += 1
        elif tris > 10000:
            tris_buckets['10K-50K'] += 1
        elif tris > 1000:
            tris_buckets['1K-10K'] += 1
        else:
            tris_buckets['< 1K'] += 1
    
    print("  Pass 三角形数分布:")
    for bucket, count in tris_buckets.items():
        bar = "█" * min(count, 50)
        print(f"    {bucket:>10}: {count:>4} {bar}")
    
    # 显示大型 Drawcall
    if drawcall_details:
        drawcall_details.sort(key=lambda x: x['triangles'], reverse=True)
        print("\n⚠️ 高几何量 Drawcall (>10K 三角形)")
        print("-" * 70)
        print(f"  {'Drawcall 名称':<25} {'事件ID':>8} {'三角形':>12} {'实例数':>8}")
        print("-" * 70)
        for dc in drawcall_details[:20]:
            name = dc['name'][:23] + ".." if len(dc['name']) > 25 else dc['name']
            print(f"  {name:<25} {dc['event_id']:>8} {dc['triangles']:>12,} {dc['instances']:>8}")
    
    # 清理
    controller.Shutdown()
    cap.Shutdown()
    
    print("\n" + "=" * 70)
    print("分析完成!")


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_geometry.py <rdc_file_path>")
        sys.exit(1)
    
    analyze_geometry(sys.argv[1])


if __name__ == "__main__":
    main()
