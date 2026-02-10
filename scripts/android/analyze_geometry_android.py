#!/usr/bin/env python3
"""
RenderDoc Android 几何复杂度分析脚本

用法: python analyze_geometry_android.py <android_rdc_path> [--host <ip>] [--port <port>]

功能:
- 统计每个 Drawcall 的顶点数、三角形数、实例数
- 检测几何复杂度过高的 Drawcall
- 分析 Mesh 复用率
- 提供几何优化建议
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


def format_number(num):
    """格式化数字"""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    else:
        return str(num)


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


def analyze_geometry_remote(controller):
    """分析几何复杂度（远程版本）"""
    
    print("\n正在扫描所有 Drawcall...", flush=True)
    
    # 统计变量
    total_draws = 0
    total_vertices = 0
    total_triangles = 0
    total_instances = 0
    
    # Drawcall 详情列表
    draw_details = []
    
    # 按 Pass 统计
    pass_stats = defaultdict(lambda: {'draws': 0, 'vertices': 0, 'triangles': 0, 'instances': 0})
    
    # VB 使用统计（用于检测复用率）
    vb_usage = defaultdict(int)
    ib_usage = defaultdict(int)
    
    current_pass = "Root"
    
    def process_action(action, depth=0):
        nonlocal total_draws, total_vertices, total_triangles, total_instances, current_pass
        
        # 检测 Pass 标记
        if action.flags & rd.ActionFlags.PushMarker:
            current_pass = action.customName or f"Pass_{action.eventId}"
        
        # 统计 Drawcall
        if action.flags & rd.ActionFlags.Drawcall:
            total_draws += 1
            
            if total_draws % 100 == 0:
                print(f"  已处理 {total_draws} 个 Drawcall...", flush=True)
            
            num_indices = action.numIndices if hasattr(action, 'numIndices') else 0
            num_instances = max(1, action.numInstances) if hasattr(action, 'numInstances') else 1
            
            # 估算三角形数
            triangles = num_indices // 3 * num_instances
            vertices = num_indices * num_instances
            
            total_vertices += vertices
            total_triangles += triangles
            total_instances += num_instances
            
            # Pass 统计
            pass_stats[current_pass]['draws'] += 1
            pass_stats[current_pass]['vertices'] += vertices
            pass_stats[current_pass]['triangles'] += triangles
            pass_stats[current_pass]['instances'] += num_instances
            
            # 记录详情
            draw_details.append({
                'eid': action.eventId,
                'name': action.customName or f"Draw_{action.eventId}",
                'pass': current_pass,
                'vertices': num_indices,
                'triangles': num_indices // 3,
                'instances': num_instances,
                'total_triangles': triangles
            })
            
            # 尝试获取 VB/IB 信息
            try:
                controller.SetFrameEvent(action.eventId, False)
                pipe = controller.GetPipelineState()
                
                # 获取 VB
                try:
                    vb_list = pipe.GetVBuffers()
                    for vb in vb_list:
                        if hasattr(vb, 'resourceId') and vb.resourceId != rd.ResourceId.Null():
                            vb_usage[str(vb.resourceId)] += 1
                except:
                    pass
                
                # 获取 IB
                try:
                    ib = pipe.GetIBuffer()
                    if hasattr(ib, 'resourceId') and ib.resourceId != rd.ResourceId.Null():
                        ib_usage[str(ib.resourceId)] += 1
                except:
                    pass
            except:
                pass
        
        for child in action.children:
            process_action(child, depth + 1)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    # 计算复用率
    vb_reuse_rate = 0
    ib_reuse_rate = 0
    
    if vb_usage:
        total_vb_uses = sum(vb_usage.values())
        unique_vbs = len(vb_usage)
        vb_reuse_rate = total_vb_uses / unique_vbs if unique_vbs > 0 else 0
    
    if ib_usage:
        total_ib_uses = sum(ib_usage.values())
        unique_ibs = len(ib_usage)
        ib_reuse_rate = total_ib_uses / unique_ibs if unique_ibs > 0 else 0
    
    # 排序找出高复杂度 Drawcall
    draw_details.sort(key=lambda x: x['total_triangles'], reverse=True)
    
    return {
        'total_draws': total_draws,
        'total_vertices': total_vertices,
        'total_triangles': total_triangles,
        'total_instances': total_instances,
        'pass_stats': dict(pass_stats),
        'draw_details': draw_details,
        'vb_reuse_rate': vb_reuse_rate,
        'ib_reuse_rate': ib_reuse_rate,
        'unique_vbs': len(vb_usage),
        'unique_ibs': len(ib_usage)
    }


def print_geometry_report(results):
    """打印几何复杂度报告"""
    
    print("\n" + "=" * 70)
    print("                      📊 几何复杂度总览")
    print("=" * 70)
    
    print(f"\n  总 Drawcall 数:         {results['total_draws']:,}")
    print(f"  总顶点数:               {format_number(results['total_vertices'])}")
    print(f"  总三角形数:             {format_number(results['total_triangles'])}")
    print(f"  总实例数:               {results['total_instances']:,}")
    
    if results['total_draws'] > 0:
        avg_tris = results['total_triangles'] / results['total_draws']
        print(f"\n  平均每 Drawcall 三角形: {format_number(int(avg_tris))}")
    
    # 缓冲区复用率
    print("\n" + "-" * 70)
    print("                    🔄 缓冲区复用分析")
    print("-" * 70)
    
    print(f"\n  唯一 VB 数量:           {results['unique_vbs']}")
    print(f"  唯一 IB 数量:           {results['unique_ibs']}")
    print(f"  VB 平均复用率:          {results['vb_reuse_rate']:.2f}x")
    print(f"  IB 平均复用率:          {results['ib_reuse_rate']:.2f}x")
    
    # 复用率评级
    if results['vb_reuse_rate'] > 3:
        reuse_rating = "✅ 优秀"
    elif results['vb_reuse_rate'] > 1.5:
        reuse_rating = "👍 良好"
    else:
        reuse_rating = "⚠️ 较低"
    print(f"  复用率评级:             {reuse_rating}")
    
    # 按 Pass 统计
    print("\n" + "-" * 70)
    print("                 🏆 几何量最高的 Pass (Top 15)")
    print("-" * 70)
    
    pass_stats = results['pass_stats']
    sorted_passes = sorted(pass_stats.items(), key=lambda x: x[1]['triangles'], reverse=True)
    
    print(f"\n  {'Pass 名称':<30} {'Drawcall':>8} {'三角形':>12}")
    print("  " + "-" * 55)
    
    for pass_name, stats in sorted_passes[:15]:
        name = pass_name[:28] + ".." if len(pass_name) > 30 else pass_name
        print(f"  {name:<30} {stats['draws']:>8} {format_number(stats['triangles']):>12}")
    
    # 高复杂度 Drawcall
    print("\n" + "-" * 70)
    print("            ⚠️ 高复杂度 Drawcall (Top 20)")
    print("-" * 70)
    
    draw_details = results['draw_details']
    
    # 过滤出三角形 > 10K 的
    high_complexity = [d for d in draw_details if d['total_triangles'] > 10000]
    
    if high_complexity:
        print(f"\n  共发现 {len(high_complexity)} 个高复杂度 Drawcall (> 10K 三角形)\n")
        print(f"  {'EID':<8} {'三角形':>12} {'实例数':>10} {'Pass 名称'}")
        print("  " + "-" * 60)
        
        for d in high_complexity[:20]:
            pass_name = d['pass'][:25] + ".." if len(d['pass']) > 27 else d['pass']
            print(f"  {d['eid']:<8} {format_number(d['total_triangles']):>12} {d['instances']:>10} {pass_name}")
        
        if len(high_complexity) > 20:
            print(f"\n  ... 还有 {len(high_complexity) - 20} 个未显示")
    else:
        print("\n  ✅ 没有发现高复杂度 Drawcall (> 10K 三角形)")
    
    # Instancing 使用情况
    print("\n" + "-" * 70)
    print("                    📦 Instancing 使用分析")
    print("-" * 70)
    
    instanced_draws = [d for d in draw_details if d['instances'] > 1]
    if instanced_draws:
        print(f"\n  使用 Instancing 的 Drawcall: {len(instanced_draws)} 个")
        total_instanced_tris = sum(d['total_triangles'] for d in instanced_draws)
        print(f"  Instancing 渲染的三角形:   {format_number(total_instanced_tris)}")
        
        max_instances = max(d['instances'] for d in instanced_draws)
        print(f"  最大实例数:                 {max_instances}")
    else:
        print("\n  ⚠️ 未检测到 Instancing 使用")
    
    # 优化建议
    print("\n" + "=" * 70)
    print("                       💡 几何优化建议")
    print("=" * 70)
    
    suggestions = []
    
    if results['total_triangles'] > 5_000_000:
        suggestions.append(f"  • 总三角形数较高 ({format_number(results['total_triangles'])})，考虑 LOD 系统")
    
    if len(high_complexity) > 10:
        suggestions.append(f"  • 存在 {len(high_complexity)} 个高复杂度 Drawcall，检查是否可以简化模型")
    
    if results['vb_reuse_rate'] < 1.5:
        suggestions.append("  • 缓冲区复用率较低，考虑合并相同材质的网格")
    
    if not instanced_draws and results['total_draws'] > 100:
        suggestions.append("  • 未使用 Instancing，对于重复对象可以显著减少 Drawcall")
    
    if results['total_draws'] > 2000:
        suggestions.append(f"  • Drawcall 数量较多 ({results['total_draws']})，考虑批处理或合并")
    
    if not suggestions:
        print("  ✅ 几何复杂度情况良好，没有明显问题")
    else:
        for s in suggestions:
            print(s)


def main():
    parser = argparse.ArgumentParser(description='RenderDoc Android 几何复杂度分析')
    parser.add_argument('rdc_path', help='Android 设备上的 RDC 文件路径')
    parser.add_argument('--host', default=DEFAULT_HOST, help=f'远程服务器地址 (默认: {DEFAULT_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'远程服务器端口 (默认: {DEFAULT_PORT})')
    parser.add_argument('--no-forward', action='store_true', help='跳过 ADB 端口转发设置')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("       RenderDoc Android 几何复杂度分析工具")
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
        print("                    分析几何复杂度")
        print("=" * 70)
        results = analyze_geometry_remote(controller)
        print_geometry_report(results)
        
    finally:
        controller.Shutdown()
        remote.Shutdown()
    
    print("\n" + "=" * 70)
    print("                         分析完成!")
    print("=" * 70)


if __name__ == "__main__":
    main()
