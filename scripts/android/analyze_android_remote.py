#!/usr/bin/env python3
"""
RenderDoc Android 远程回放分析脚本

用法: 
  python analyze_android_remote.py <android_rdc_path> [--host <ip>] [--port <port>]

示例:
  # 分析 Android 设备上的 RDC 文件
  python analyze_android_remote.py /sdcard/RenderDoc/capture.rdc
  
  # 指定设备 IP（通过 WiFi 连接时）
  python analyze_android_remote.py /sdcard/RenderDoc/capture.rdc --host 192.168.1.100

前置条件:
  1. Android 设备已通过 ADB 连接
  2. RenderDoc Replay Server 已在 Android 上启动
     - 通过 RenderDoc GUI: Tools → Manage Remote Servers → Run Server
     - 或通过 ADB: adb shell am start -n org.renderdoc.renderdoccmd/.Loader -e rdargs "remoteserver"
  3. 端口转发已设置: adb forward tcp:38920 tcp:38920
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
DEFAULT_HOST = "localhost"  # 通过 ADB 端口转发时使用 localhost
DEFAULT_PORT = 38920        # RenderDoc 默认端口


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


def get_format_byte_size(fmt):
    """估算格式的字节大小"""
    fmt_str = str(fmt).lower()
    
    if 'r32g32b32a32' in fmt_str:
        return 16
    elif 'r32g32b32' in fmt_str:
        return 12
    elif 'r32g32' in fmt_str:
        return 8
    elif 'r32' in fmt_str:
        return 4
    elif 'r16g16b16a16' in fmt_str:
        return 8
    elif 'r16g16' in fmt_str:
        return 4
    elif 'r16' in fmt_str:
        return 2
    elif 'r8g8b8a8' in fmt_str:
        return 4
    elif 'r8g8' in fmt_str:
        return 2
    elif 'r8' in fmt_str:
        return 1
    else:
        return 4


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
        # 创建远程服务器连接
        result, remote = rd.CreateRemoteServerConnection(host, port, None)
        
        if result != rd.ResultCode.Succeeded:
            print(f"❌ 连接失败: {result}")
            print("\n可能的原因:")
            print("  1. Android 上的 RenderDoc Replay Server 未启动")
            print("  2. ADB 端口转发未设置: adb forward tcp:38920 tcp:38920")
            print("  3. 设备不在同一网络或端口被防火墙阻止")
            return None
        
        print(f"✅ 成功连接到远程服务器")
        
        # 获取远程设备信息
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
        # 复制到远程执行回放
        local_progress = None
        result, path_or_error = remote.CopyCaptureToRemote(rdc_path, local_progress)
        
        if result != rd.ResultCode.Succeeded:
            # 如果复制失败，尝试直接使用路径（文件可能已在设备上）
            print(f"   文件复制跳过，尝试直接打开...")
            remote_path = rdc_path
        else:
            remote_path = path_or_error
            print(f"   文件已复制到远程: {remote_path}")
        
        # 打开捕获文件
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


def analyze_vertex_attributes_remote(controller):
    """分析顶点属性使用情况（远程版本）"""
    
    # 统计数据
    total_draws = 0
    draws_with_waste = 0
    total_wasted_bytes_per_vertex = 0
    total_vertices_drawn = 0
    waste_details = []
    semantic_stats = defaultdict(lambda: {'provided': 0, 'used': 0, 'wasted': 0})
    
    print("\n正在扫描所有 Draw 调用...", flush=True)
    
    def process_action(action):
        nonlocal total_draws, draws_with_waste, total_wasted_bytes_per_vertex, total_vertices_drawn
        
        flags = int(action.flags)
        is_draw = flags & int(rd.ActionFlags.Drawcall)
        
        if is_draw:
            total_draws += 1
            
            if total_draws % 50 == 0:
                print(f"  已处理 {total_draws} 个 Draw...", flush=True)
            
            controller.SetFrameEvent(action.eventId, False)
            pipe = controller.GetPipelineState()
            
            vs_shader = pipe.GetShader(rd.ShaderStage.Vertex)
            if vs_shader == rd.ResourceId.Null():
                for child in action.children:
                    process_action(child)
                return
            
            vs_refl = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            if vs_refl is None:
                for child in action.children:
                    process_action(child)
                return
            
            # 获取着色器实际使用的输入语义
            shader_inputs = set()
            for sig in vs_refl.inputSignature:
                semantic_name = sig.semanticName if hasattr(sig, 'semanticName') else ''
                semantic_index = sig.semanticIndex if hasattr(sig, 'semanticIndex') else 0
                semantic_key = f"{semantic_name}{semantic_index}"
                
                channel_used_mask = getattr(sig, 'channelUsedMask', 0xF)
                is_actually_used = channel_used_mask > 0
                
                if is_actually_used:
                    shader_inputs.add(semantic_key)
                    semantic_stats[semantic_name]['used'] += 1
            
            # 获取输入布局中提供的属性
            try:
                vertex_inputs = pipe.GetVertexInputs()
            except:
                vertex_inputs = []
            
            if not vertex_inputs:
                for child in action.children:
                    process_action(child)
                return
            
            # 比较浪费
            wasted_attrs = []
            wasted_bytes = 0
            
            for attr in vertex_inputs:
                semantic_name = attr.name if hasattr(attr, 'name') else ''
                base_name = semantic_name.rstrip('0123456789')
                semantic_index = ''
                for c in reversed(semantic_name):
                    if c.isdigit():
                        semantic_index = c + semantic_index
                    else:
                        break
                semantic_index = int(semantic_index) if semantic_index else 0
                semantic_key = f"{base_name}{semantic_index}"
                
                fmt = attr.format if hasattr(attr, 'format') else None
                byte_size = get_format_byte_size(fmt) if fmt else 4
                
                semantic_stats[base_name]['provided'] += 1
                
                if semantic_key not in shader_inputs:
                    wasted_attrs.append({
                        'name': semantic_name,
                        'key': semantic_key,
                        'size': byte_size
                    })
                    wasted_bytes += byte_size
                    semantic_stats[base_name]['wasted'] += 1
            
            if wasted_attrs:
                draws_with_waste += 1
                num_vertices = action.numIndices if hasattr(action, 'numIndices') else 0
                if num_vertices == 0:
                    num_vertices = action.numVertices if hasattr(action, 'numVertices') else 0
                
                total_vertices_drawn += num_vertices
                total_wasted_bytes_per_vertex += wasted_bytes * num_vertices
                
                waste_details.append({
                    'eid': action.eventId,
                    'num_vertices': num_vertices,
                    'shader_needs': list(shader_inputs),
                    'wasted': wasted_attrs,
                    'wasted_bytes_per_vertex': wasted_bytes,
                    'total_wasted_bytes': wasted_bytes * num_vertices
                })
        
        for child in action.children:
            process_action(child)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    return {
        'total_draws': total_draws,
        'draws_with_waste': draws_with_waste,
        'total_wasted_bytes': total_wasted_bytes_per_vertex,
        'total_vertices': total_vertices_drawn,
        'waste_details': waste_details,
        'semantic_stats': dict(semantic_stats)
    }


def analyze_shader_bindings_remote(controller):
    """分析 Shader 绑定使用情况（远程版本）"""
    
    total_draws = 0
    total_bindings = 0
    unused_bindings = 0
    binding_stats = defaultdict(lambda: {'total': 0, 'unused': 0})
    unused_binding_details = []
    
    def get_shader_stage_name(stage):
        stage_names = {
            int(rd.ShaderStage.Vertex): "VS",
            int(rd.ShaderStage.Hull): "HS",
            int(rd.ShaderStage.Domain): "DS",
            int(rd.ShaderStage.Geometry): "GS",
            int(rd.ShaderStage.Pixel): "PS",
            int(rd.ShaderStage.Compute): "CS",
        }
        return stage_names.get(int(stage), f"Stage{int(stage)}")
    
    def check_bindings(bindings, bind_type, stage_name, action, refl_resources=None):
        nonlocal total_bindings, unused_bindings
        
        for i, binding in enumerate(bindings):
            if hasattr(binding, 'descriptor'):
                res_id = binding.descriptor.resource
            else:
                continue
            
            if res_id == rd.ResourceId.Null():
                continue
            
            total_bindings += 1
            binding_stats[bind_type]['total'] += 1
            
            is_unused = False
            if hasattr(binding, 'access'):
                is_unused = getattr(binding.access, 'staticallyUnused', False)
            
            res_name = ""
            slot_num = i
            if refl_resources and i < len(refl_resources):
                res_name = refl_resources[i].name if hasattr(refl_resources[i], 'name') else ""
                slot_num = refl_resources[i].fixedBindNumber if hasattr(refl_resources[i], 'fixedBindNumber') else i
            
            if is_unused:
                unused_bindings += 1
                binding_stats[bind_type]['unused'] += 1
                unused_binding_details.append({
                    'eid': action.eventId,
                    'stage': stage_name,
                    'type': bind_type,
                    'slot': slot_num,
                    'name': res_name
                })
    
    def process_action(action):
        nonlocal total_draws
        
        flags = int(action.flags)
        is_draw = flags & int(rd.ActionFlags.Drawcall)
        is_dispatch = flags & int(rd.ActionFlags.Dispatch)
        
        if is_draw or is_dispatch:
            total_draws += 1
            
            if total_draws % 50 == 0:
                print(f"  已处理 {total_draws} 个 Draw/Dispatch...", flush=True)
            
            controller.SetFrameEvent(action.eventId, False)
            pipe = controller.GetPipelineState()
            
            if is_dispatch:
                stages = [rd.ShaderStage.Compute]
            else:
                stages = [rd.ShaderStage.Vertex, rd.ShaderStage.Pixel, 
                         rd.ShaderStage.Geometry, rd.ShaderStage.Hull, rd.ShaderStage.Domain]
            
            for stage in stages:
                shader = pipe.GetShader(stage)
                if shader == rd.ResourceId.Null():
                    continue
                
                refl = pipe.GetShaderReflection(stage)
                if refl is None:
                    continue
                
                stage_name = get_shader_stage_name(stage)
                
                try:
                    cb_bindings = pipe.GetConstantBlocks(stage, False)
                    refl_cbs = refl.constantBlocks if hasattr(refl, 'constantBlocks') else None
                    check_bindings(cb_bindings, 'ConstantBuffer', stage_name, action, refl_cbs)
                except:
                    pass
                
                try:
                    ro_resources = pipe.GetReadOnlyResources(stage)
                    refl_srvs = refl.readOnlyResources if hasattr(refl, 'readOnlyResources') else None
                    check_bindings(ro_resources, 'SRV', stage_name, action, refl_srvs)
                except:
                    pass
                
                try:
                    rw_resources = pipe.GetReadWriteResources(stage)
                    refl_uavs = refl.readWriteResources if hasattr(refl, 'readWriteResources') else None
                    check_bindings(rw_resources, 'UAV', stage_name, action, refl_uavs)
                except:
                    pass
        
        for child in action.children:
            process_action(child)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    return {
        'total_draws': total_draws,
        'total_bindings': total_bindings,
        'unused_bindings': unused_bindings,
        'binding_stats': dict(binding_stats),
        'unused_details': unused_binding_details[:50]
    }


def print_vertex_report(results):
    """打印顶点属性分析报告"""
    print(f"\n{'='*80}")
    print("                    顶点属性浪费分析结果")
    print("=" * 80)
    
    print(f"\n  总 Draw 调用数: {results['total_draws']}")
    print(f"  存在属性浪费的 Draw 数: {results['draws_with_waste']}")
    
    if results['total_draws'] > 0:
        waste_ratio = results['draws_with_waste'] / results['total_draws'] * 100
        print(f"  浪费率: {waste_ratio:.1f}%")
    
    print(f"\n  📊 带宽浪费估算:")
    print(f"     总顶点数: {results['total_vertices']:,}")
    print(f"     浪费的带宽: {format_size(results['total_wasted_bytes'])}")
    
    # 按语义统计
    print(f"\n{'='*80}")
    print("                    按语义统计")
    print("=" * 80)
    
    print(f"\n  {'语义名称':<20} {'提供次数':<12} {'使用次数':<12} {'浪费次数':<12}")
    print(f"  {'-'*55}")
    
    sorted_semantics = sorted(results['semantic_stats'].items(), 
                             key=lambda x: x[1]['wasted'], reverse=True)
    for semantic_name, stats in sorted_semantics:
        if stats['provided'] > 0 or stats['used'] > 0:
            print(f"  {semantic_name:<20} {stats['provided']:<12} {stats['used']:<12} {stats['wasted']:<12}")
    
    # 显示浪费最严重的 Draw 调用
    if results['waste_details']:
        print(f"\n{'='*80}")
        print("                浪费最严重的 Draw 调用 (前 10 个)")
        print("=" * 80)
        
        sorted_waste = sorted(results['waste_details'], 
                             key=lambda x: x['total_wasted_bytes'], reverse=True)
        
        for detail in sorted_waste[:10]:
            print(f"\n  EID {detail['eid']}:")
            print(f"    顶点数: {detail['num_vertices']:,}")
            print(f"    着色器需要: {', '.join(detail['shader_needs'][:8])}...")
            print(f"    浪费的属性: {', '.join([a['name'] for a in detail['wasted']])}")
            print(f"    每顶点浪费: {detail['wasted_bytes_per_vertex']} bytes")
            print(f"    总浪费: {format_size(detail['total_wasted_bytes'])}")


def print_binding_report(results):
    """打印 Shader 绑定分析报告"""
    print(f"\n{'='*80}")
    print("                    Shader 绑定使用分析结果")
    print("=" * 80)
    
    print(f"\n  总 Draw/Dispatch 调用数: {results['total_draws']}")
    print(f"  总绑定数量: {results['total_bindings']}")
    print(f"  未使用绑定数量: {results['unused_bindings']}")
    
    if results['total_bindings'] > 0:
        waste_ratio = results['unused_bindings'] / results['total_bindings'] * 100
        print(f"\n  📊 绑定利用率: {100 - waste_ratio:.1f}%")
    
    # 按类型统计
    print(f"\n  {'类型':<20} {'总绑定':<12} {'未使用':<12}")
    print(f"  {'-'*45}")
    
    for bind_type in ['ConstantBuffer', 'SRV', 'UAV']:
        stats = results['binding_stats'].get(bind_type, {'total': 0, 'unused': 0})
        if stats['total'] > 0:
            print(f"  {bind_type:<20} {stats['total']:<12} {stats['unused']:<12}")


def main():
    parser = argparse.ArgumentParser(description='RenderDoc Android 远程回放分析')
    parser.add_argument('rdc_path', help='Android 设备上的 RDC 文件路径 (例如: /sdcard/RenderDoc/capture.rdc)')
    parser.add_argument('--host', default=DEFAULT_HOST, help=f'远程服务器地址 (默认: {DEFAULT_HOST})')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'远程服务器端口 (默认: {DEFAULT_PORT})')
    parser.add_argument('--no-forward', action='store_true', help='跳过 ADB 端口转发设置')
    parser.add_argument('--vertex-only', action='store_true', help='只分析顶点属性')
    parser.add_argument('--binding-only', action='store_true', help='只分析 Shader 绑定')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("       RenderDoc Android 远程回放分析工具")
    print("=" * 80)
    
    # 设置 ADB 端口转发
    if not args.no_forward and args.host == "localhost":
        setup_adb_port_forward()
    
    # 连接远程服务器
    remote = connect_to_remote_server(args.host, args.port)
    if remote is None:
        sys.exit(1)
    
    # 打开远程捕获文件
    controller = open_remote_capture(remote, args.rdc_path)
    if controller is None:
        remote.Shutdown()
        sys.exit(1)
    
    try:
        # 执行分析
        if not args.binding_only:
            print("\n" + "=" * 80)
            print("                    分析顶点属性使用情况")
            print("=" * 80)
            vertex_results = analyze_vertex_attributes_remote(controller)
            print_vertex_report(vertex_results)
        
        if not args.vertex_only:
            print("\n" + "=" * 80)
            print("                    分析 Shader 绑定使用情况")
            print("=" * 80)
            binding_results = analyze_shader_bindings_remote(controller)
            print_binding_report(binding_results)
        
    finally:
        controller.Shutdown()
        remote.Shutdown()
    
    print("\n" + "=" * 80)
    print("                         分析完成!")
    print("=" * 80)


if __name__ == "__main__":
    main()
