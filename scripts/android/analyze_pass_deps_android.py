#!/usr/bin/env python3
"""
RenderDoc Android Pass 依赖分析脚本

用法: python analyze_pass_deps_android.py <android_rdc_path> [--host <ip>] [--port <port>]

功能:
- 分析 Render Pass 之间的依赖关系
- 检测 RT 的读写依赖
- 识别可以并行的 Pass
- 检测冗余的 Pass 切换
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


def analyze_pass_deps_remote(controller):
    """分析 Pass 依赖关系（远程版本）"""
    
    print("\n正在分析 Pass 依赖关系...", flush=True)
    
    # 收集 Pass 信息
    passes = []  # {name, eid, writes: set(), reads: set(), drawcalls}
    current_pass = None
    
    # RT 使用记录
    rt_first_write = {}  # resource_id -> pass_name
    rt_last_write = {}   # resource_id -> pass_name
    rt_reads = defaultdict(list)  # resource_id -> [pass_name]
    
    # 依赖关系
    dependencies = []  # (from_pass, to_pass, resource_id, type)
    
    def process_action(action, depth=0):
        nonlocal current_pass
        
        # 检测 Pass 标记
        if action.flags & rd.ActionFlags.PushMarker:
            if current_pass and current_pass['drawcalls'] > 0:
                passes.append(current_pass)
            
            current_pass = {
                'name': action.customName or f"Pass_{action.eventId}",
                'eid': action.eventId,
                'writes': set(),
                'reads': set(),
                'drawcalls': 0
            }
        
        # 统计 Drawcall 并收集 RT 读写信息
        if action.flags & rd.ActionFlags.Drawcall:
            if current_pass:
                current_pass['drawcalls'] += 1
            
            try:
                controller.SetFrameEvent(action.eventId, False)
                pipe = controller.GetPipelineState()
                
                # 获取当前绑定的 RT (写入)
                try:
                    outputs = pipe.GetOutputTargets()
                    for out in outputs:
                        if hasattr(out, 'resourceId') and out.resourceId != rd.ResourceId.Null():
                            res_id = str(out.resourceId)
                            if current_pass:
                                current_pass['writes'].add(res_id)
                except:
                    pass
                
                # 获取 Depth Target (写入)
                try:
                    depth_target = pipe.GetDepthTarget()
                    if hasattr(depth_target, 'resourceId') and depth_target.resourceId != rd.ResourceId.Null():
                        res_id = str(depth_target.resourceId)
                        if current_pass:
                            current_pass['writes'].add(res_id)
                except:
                    pass
                
                # 获取 SRV (读取)
                for stage in [rd.ShaderStage.Vertex, rd.ShaderStage.Pixel, rd.ShaderStage.Compute]:
                    try:
                        resources = pipe.GetReadOnlyResources(stage)
                        for res in resources:
                            if hasattr(res, 'descriptor'):
                                res_id = res.descriptor.resource
                                if res_id != rd.ResourceId.Null():
                                    if current_pass:
                                        current_pass['reads'].add(str(res_id))
                    except:
                        pass
                
            except:
                pass
        
        for child in action.children:
            process_action(child, depth + 1)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    # 保存最后一个 Pass
    if current_pass and current_pass['drawcalls'] > 0:
        passes.append(current_pass)
    
    print(f"  共识别 {len(passes)} 个 Pass", flush=True)
    
    # 分析依赖关系
    for i, pass_info in enumerate(passes):
        # 记录写入
        for res_id in pass_info['writes']:
            if res_id not in rt_first_write:
                rt_first_write[res_id] = pass_info['name']
            rt_last_write[res_id] = pass_info['name']
        
        # 检查读取依赖
        for res_id in pass_info['reads']:
            if res_id in rt_last_write:
                writer = rt_last_write[res_id]
                if writer != pass_info['name']:
                    dependencies.append({
                        'from': writer,
                        'to': pass_info['name'],
                        'resource': res_id,
                        'type': 'read_after_write'
                    })
            
            rt_reads[res_id].append(pass_info['name'])
    
    # 检测冗余 Pass 切换
    redundant_switches = []
    for i in range(1, len(passes)):
        prev_pass = passes[i-1]
        curr_pass = passes[i]
        
        # 如果写入相同的 RT 集合且没有依赖，可能可以合并
        if prev_pass['writes'] == curr_pass['writes'] and len(prev_pass['writes']) > 0:
            # 检查是否有依赖
            has_dep = False
            for res_id in curr_pass['reads']:
                if res_id in prev_pass['writes']:
                    has_dep = True
                    break
            
            if not has_dep:
                redundant_switches.append({
                    'pass1': prev_pass['name'],
                    'pass2': curr_pass['name'],
                    'shared_rts': list(prev_pass['writes'])
                })
    
    # 找出没有依赖可以并行的 Pass
    parallelizable = []
    for i in range(len(passes)):
        for j in range(i + 1, len(passes)):
            pass_i = passes[i]
            pass_j = passes[j]
            
            # 检查是否有 RAW/WAW/WAR 依赖
            has_conflict = False
            
            # RAW: j reads what i writes
            if pass_i['writes'] & pass_j['reads']:
                has_conflict = True
            
            # WAW: both write to same
            if pass_i['writes'] & pass_j['writes']:
                has_conflict = True
            
            # WAR: j writes what i reads
            if pass_i['reads'] & pass_j['writes']:
                has_conflict = True
            
            if not has_conflict:
                parallelizable.append({
                    'pass1': pass_i['name'],
                    'pass2': pass_j['name']
                })
    
    return {
        'passes': passes,
        'dependencies': dependencies,
        'redundant_switches': redundant_switches,
        'parallelizable': parallelizable[:50],  # 限制数量
        'rt_first_write': rt_first_write,
        'rt_usage_count': len(rt_reads)
    }


def print_pass_deps_report(results):
    """打印 Pass 依赖分析报告"""
    
    print("\n" + "=" * 70)
    print("                      📊 Pass 依赖分析总览")
    print("=" * 70)
    
    passes = results['passes']
    dependencies = results['dependencies']
    
    print(f"\n  总 Pass 数量:           {len(passes)}")
    print(f"  依赖关系数量:           {len(dependencies)}")
    print(f"  涉及 RT 数量:           {results['rt_usage_count']}")
    
    total_draws = sum(p['drawcalls'] for p in passes)
    print(f"  总 Drawcall 数量:       {total_draws}")
    
    if len(passes) > 0:
        avg_draws = total_draws / len(passes)
        print(f"  平均每 Pass Drawcall:   {avg_draws:.1f}")
    
    # Pass 列表
    print("\n" + "-" * 70)
    print("                    📋 Pass 列表")
    print("-" * 70)
    
    print(f"\n  {'Pass 名称':<35} {'Drawcall':>10} {'写入 RT':>8} {'读取 RT':>8}")
    print("  " + "-" * 65)
    
    for p in passes[:20]:
        name = p['name'][:33] + ".." if len(p['name']) > 35 else p['name']
        print(f"  {name:<35} {p['drawcalls']:>10} {len(p['writes']):>8} {len(p['reads']):>8}")
    
    if len(passes) > 20:
        print(f"\n  ... 还有 {len(passes) - 20} 个未显示")
    
    # 依赖关系
    if dependencies:
        print("\n" + "-" * 70)
        print("                    🔗 依赖关系 (Top 20)")
        print("-" * 70)
        
        print(f"\n  {'源 Pass':<25} → {'目标 Pass':<25} {'类型'}")
        print("  " + "-" * 65)
        
        for dep in dependencies[:20]:
            from_name = dep['from'][:23] + ".." if len(dep['from']) > 25 else dep['from']
            to_name = dep['to'][:23] + ".." if len(dep['to']) > 25 else dep['to']
            print(f"  {from_name:<25} → {to_name:<25} RAW")
        
        if len(dependencies) > 20:
            print(f"\n  ... 还有 {len(dependencies) - 20} 个未显示")
    
    # 冗余 Pass 切换
    redundant = results['redundant_switches']
    if redundant:
        print("\n" + "-" * 70)
        print("                ⚠️ 可能冗余的 Pass 切换")
        print("-" * 70)
        
        print(f"\n  共发现 {len(redundant)} 对可能冗余的 Pass 切换\n")
        
        for r in redundant[:10]:
            p1 = r['pass1'][:30] + ".." if len(r['pass1']) > 32 else r['pass1']
            p2 = r['pass2'][:30] + ".." if len(r['pass2']) > 32 else r['pass2']
            print(f"  • {p1}")
            print(f"    → {p2}")
            print(f"    共享 RT: {len(r['shared_rts'])} 个\n")
    else:
        print("\n  ✅ 未发现明显冗余的 Pass 切换")
    
    # 可并行的 Pass
    parallelizable = results['parallelizable']
    if parallelizable:
        print("\n" + "-" * 70)
        print("                🚀 可并行的 Pass 对 (部分)")
        print("-" * 70)
        
        print(f"\n  共发现 {len(parallelizable)} 对可并行的 Pass\n")
        
        for p in parallelizable[:10]:
            p1 = p['pass1'][:25] + ".." if len(p['pass1']) > 27 else p['pass1']
            p2 = p['pass2'][:25] + ".." if len(p['pass2']) > 27 else p['pass2']
            print(f"  • {p1} || {p2}")
    
    # 优化建议
    print("\n" + "=" * 70)
    print("                       💡 Pass 优化建议")
    print("=" * 70)
    
    suggestions = []
    
    if len(redundant) > 5:
        suggestions.append(f"  • 存在 {len(redundant)} 对冗余 Pass 切换，考虑合并相同 RT 的 Pass")
    
    if len(passes) > 50:
        suggestions.append(f"  • Pass 数量较多 ({len(passes)})，检查是否可以减少 RT 切换")
    
    avg_deps = len(dependencies) / len(passes) if len(passes) > 0 else 0
    if avg_deps > 3:
        suggestions.append(f"  • 平均依赖较多 ({avg_deps:.1f}/Pass)，检查资源生命周期")
    
    if len(parallelizable) > 10:
        suggestions.append(f"  • 存在 {len(parallelizable)} 对可并行 Pass，考虑异步计算优化")
    
    if not suggestions:
        print("  ✅ Pass 依赖情况良好，没有明显问题")
    else:
        for s in suggestions:
            print(s)


def main():
    parser = argparse.ArgumentParser(description='RenderDoc Android Pass 依赖分析')
    parser.add_argument('rdc_path', help='Android 设备上的 RDC 文件路径')
    parser.add_argument('--host', default=DEFAULT_HOST, help=f'远程服务器地址 (默认: {DEFAULT_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'远程服务器端口 (默认: {DEFAULT_PORT})')
    parser.add_argument('--no-forward', action='store_true', help='跳过 ADB 端口转发设置')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("       RenderDoc Android Pass 依赖分析工具")
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
        print("                    分析 Pass 依赖")
        print("=" * 70)
        results = analyze_pass_deps_remote(controller)
        print_pass_deps_report(results)
        
    finally:
        controller.Shutdown()
        remote.Shutdown()
    
    print("\n" + "=" * 70)
    print("                         分析完成!")
    print("=" * 70)


if __name__ == "__main__":
    main()
