#!/usr/bin/env python3
"""
RenderDoc Android 综合分析入口脚本

用法: python analyze_rdc_android.py <android_rdc_path> [选项]

功能:
- 一键执行所有分析模块
- 支持选择性执行特定分析
- 生成综合分析报告

示例:
  # 执行全部分析
  python analyze_rdc_android.py /sdcard/RenderDoc/capture.rdc

  # 只执行内存和 Overdraw 分析
  python analyze_rdc_android.py /sdcard/RenderDoc/capture.rdc --memory --overdraw

  # 跳过 ADB 端口转发
  python analyze_rdc_android.py /sdcard/RenderDoc/capture.rdc --no-forward
"""

import sys
import os
import argparse
import time
from datetime import datetime

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
# RenderDoc GUI 通常使用这个转发端口
RENDERDOC_GUI_PORT = 38960


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


def format_number(num):
    """格式化数字"""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    else:
        return str(num)


def setup_adb_port_forward():
    """设置 ADB 端口转发 - 自动检测 RenderDoc socket 名称"""
    import subprocess
    try:
        # 首先清除旧的转发
        subprocess.run(["adb", "forward", "--remove-all"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # 查找 RenderDoc 的 abstract socket 名称
        result = subprocess.run(
            ["adb", "shell", "cat /proc/net/unix | grep renderdoc"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        output = result.stdout.decode() if result.stdout else ''
        
        # 解析 socket 名称 (格式: @renderdoc_XXXXX)
        socket_name = None
        for line in output.split('\n'):
            if '@renderdoc_' in line:
                # 提取 socket 名称
                parts = line.split('@')
                if len(parts) >= 2:
                    socket_name = parts[-1].strip()
                    break
        
        if not socket_name:
            print("⚠️ 未找到 RenderDoc socket，请确保 Android 上已启动 RenderDoc Replay Server")
            return False
        
        # 设置端口转发
        result = subprocess.run(
            ["adb", "forward", "tcp:{}".format(DEFAULT_PORT), "localabstract:{}".format(socket_name)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if result.returncode == 0:
            print("✅ ADB 端口转发设置成功: tcp:{} -> localabstract:{}".format(DEFAULT_PORT, socket_name))
            return True
        else:
            print("⚠️ ADB 端口转发失败: {}".format(result.stderr.decode() if result.stderr else ''))
            return False
    except FileNotFoundError:
        print("⚠️ 未找到 adb 命令，请确保已安装 Android SDK 并配置环境变量")
        return False


def connect_to_remote_server(host, port):
    """连接到远程 RenderDoc 服务器"""
    print("\n正在连接远程服务器 {}:{}...".format(host, port))
    
    try:
        # 构建连接 URL
        url = "{}:{}".format(host, port)
        result = rd.CreateRemoteServerConnection(url)
        
        # 检查返回值类型
        if isinstance(result, tuple):
            status, remote = result
            if status != rd.ResultCode.Succeeded:
                print("❌ 连接失败: {}".format(status))
                print("\n可能的原因:")
                print("  1. Android 上的 RenderDoc Replay Server 未启动")
                print("  2. ADB 端口转发未设置: adb forward tcp:38920 tcp:38920")
                print("  3. 设备不在同一网络或端口被防火墙阻止")
                return None
        else:
            remote = result
            if remote is None:
                print("❌ 连接失败")
                return None
        
        print("✅ 成功连接到远程服务器")
        try:
            home_path = remote.HomeFolder()
            print("   远程设备目录: {}".format(home_path))
        except:
            pass
        
        return remote
        
    except Exception as e:
        print("❌ 连接异常: {}".format(e))
        import traceback
        traceback.print_exc()
        return None


def open_remote_capture(remote, rdc_path):
    """在远程设备上打开 RDC 文件"""
    print("\n正在打开远程 RDC 文件: {}".format(rdc_path))
    
    try:
        # 先尝试复制文件到远程设备
        remote_path = rdc_path
        try:
            copy_result = remote.CopyCaptureToRemote(rdc_path, None)
            
            # 处理不同的返回类型
            if isinstance(copy_result, tuple):
                if len(copy_result) == 2:
                    status, path_or_err = copy_result
                    if status == rd.ResultCode.Succeeded:
                        remote_path = path_or_err
                        print("   文件已复制到远程: {}".format(remote_path))
                elif len(copy_result) == 3:
                    status, path, err = copy_result
                    if status == rd.ResultCode.Succeeded:
                        remote_path = path
                        print("   文件已复制到远程: {}".format(remote_path))
            elif isinstance(copy_result, str):
                # 直接返回路径字符串
                remote_path = copy_result
                print("   文件已复制到远程: {}".format(remote_path))
            else:
                print("   文件复制返回: {}，使用原始路径".format(type(copy_result)))
        except Exception as copy_err:
            print("   文件复制跳过 ({}), 使用原始路径".format(copy_err))
        
        # 打开捕获文件
        open_result = remote.OpenCapture(0, remote_path, rd.ReplayOptions(), None)
        
        # 处理不同的返回类型
        if isinstance(open_result, tuple):
            result, controller = open_result[0], open_result[1]
            if result != rd.ResultCode.Succeeded:
                print("❌ 无法打开捕获文件: {}".format(result))
                return None
        else:
            controller = open_result
            if controller is None:
                print("❌ 无法打开捕获文件")
                return None
        
        print("✅ 成功打开捕获文件")
        return controller
        
    except Exception as e:
        print("❌ 打开捕获文件异常: {}".format(e))
        import traceback
        traceback.print_exc()
        return None


# ============ 分析模块导入 ============
# 这里直接内嵌简化版的分析逻辑，避免模块导入问题

def analyze_basic_stats(controller):
    """基础统计分析"""
    from collections import defaultdict
    
    total_draws = 0
    total_dispatches = 0
    pass_count = 0
    
    def process_action(action):
        nonlocal total_draws, total_dispatches, pass_count
        
        if action.flags & rd.ActionFlags.Drawcall:
            total_draws += 1
        if action.flags & rd.ActionFlags.Dispatch:
            total_dispatches += 1
        if action.flags & rd.ActionFlags.PushMarker:
            pass_count += 1
        
        for child in action.children:
            process_action(child)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    textures = controller.GetTextures()
    buffers = controller.GetBuffers()
    
    return {
        'total_draws': total_draws,
        'total_dispatches': total_dispatches,
        'pass_count': pass_count,
        'texture_count': len(textures),
        'buffer_count': len(buffers)
    }


def analyze_memory(controller):
    """内存分析"""
    texture_memory = 0
    buffer_memory = 0
    
    textures = controller.GetTextures()
    for tex in textures:
        # 简化的大小估算
        width = tex.width
        height = max(1, tex.height)
        depth = max(1, tex.depth)
        mips = max(1, tex.mips)
        array_size = max(1, tex.arraysize)
        
        bytes_per_pixel = 4  # 默认
        fmt_str = str(tex.format).lower()
        if 'bc' in fmt_str or 'astc' in fmt_str or 'etc' in fmt_str:
            bytes_per_pixel = 1
        elif 'r16g16b16a16' in fmt_str:
            bytes_per_pixel = 8
        elif 'r32g32b32a32' in fmt_str:
            bytes_per_pixel = 16
        
        size = width * height * depth * bytes_per_pixel * array_size
        # Mipmap 系数
        size = int(size * (1 + 1/3) if mips > 1 else size)
        texture_memory += size
    
    buffers = controller.GetBuffers()
    for buf in buffers:
        buffer_memory += buf.length
    
    return {
        'texture_memory': texture_memory,
        'buffer_memory': buffer_memory,
        'total_memory': texture_memory + buffer_memory
    }


def analyze_overdraw(controller):
    """Overdraw 分析"""
    from collections import defaultdict
    
    # 获取分辨率
    textures = controller.GetTextures()
    main_width = 1920
    main_height = 1080
    
    for tex in textures:
        if hasattr(tex, 'creationFlags') and hasattr(rd, 'TextureCategory'):
            if tex.creationFlags & rd.TextureCategory.ColorTarget:
                if tex.width > 256 and tex.height > 256 and tex.width != tex.height:
                    main_width = tex.width
                    main_height = tex.height
                    break
    
    screen_pixels = main_width * main_height
    total_pixels = 0
    total_draws = 0
    
    def process_action(action):
        nonlocal total_pixels, total_draws
        
        if action.flags & rd.ActionFlags.Drawcall:
            total_draws += 1
            num_verts = action.numIndices if hasattr(action, 'numIndices') else 0
            instances = max(1, action.numInstances) if hasattr(action, 'numInstances') else 1
            
            if num_verts <= 6:
                pixels = screen_pixels
            else:
                triangles = num_verts // 3 * instances
                pixels = min(triangles * 500, screen_pixels * instances)
            
            total_pixels += pixels
        
        for child in action.children:
            process_action(child)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    avg_overdraw = total_pixels / screen_pixels if screen_pixels > 0 else 0
    
    return {
        'screen_resolution': f"{main_width}x{main_height}",
        'total_draws': total_draws,
        'avg_overdraw': avg_overdraw
    }


def analyze_geometry(controller):
    """几何复杂度分析"""
    total_draws = 0
    total_triangles = 0
    total_instances = 0
    
    def process_action(action):
        nonlocal total_draws, total_triangles, total_instances
        
        if action.flags & rd.ActionFlags.Drawcall:
            total_draws += 1
            num_indices = action.numIndices if hasattr(action, 'numIndices') else 0
            num_instances = max(1, action.numInstances) if hasattr(action, 'numInstances') else 1
            
            triangles = num_indices // 3 * num_instances
            total_triangles += triangles
            total_instances += num_instances
        
        for child in action.children:
            process_action(child)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    return {
        'total_draws': total_draws,
        'total_triangles': total_triangles,
        'total_instances': total_instances,
        'avg_triangles_per_draw': total_triangles // total_draws if total_draws > 0 else 0
    }


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
        return 4  # 默认假设 4 字节


def analyze_vertex_attributes(controller):
    """顶点属性浪费分析
    
    检测 IA 下发但 Shader 未使用的顶点属性。
    
    匹配策略:
    - Vulkan: 使用 Location 匹配 (regIndex vs location)
    - OpenGL/GLES: 使用 Location 匹配 (regIndex vs location)  
    - D3D11/D3D12: 使用语义名称匹配 (semanticName + semanticIndex)
    """
    from collections import defaultdict
    
    # 获取 API 类型
    api_props = controller.GetAPIProperties()
    api_type = api_props.pipelineType
    
    # 判断使用哪种匹配方式
    is_vulkan = (api_type == rd.GraphicsAPI.Vulkan)
    is_opengl = (api_type == rd.GraphicsAPI.OpenGL)
    use_location_matching = is_vulkan or is_opengl  # Vulkan 和 OpenGL 使用 Location 匹配
    
    api_name = "Vulkan" if is_vulkan else ("OpenGL/GLES" if is_opengl else "D3D")
    print("    检测到 API: {} (使用{}匹配)".format(
        api_name, "Location" if use_location_matching else "语义名称"))
    
    total_draws = 0
    draws_with_waste = 0
    total_wasted_bytes = 0
    total_vertices = 0
    attr_stats = defaultdict(lambda: {'provided': 0, 'used': 0, 'wasted': 0})
    waste_details = []
    
    def process_action(action):
        nonlocal total_draws, draws_with_waste, total_wasted_bytes, total_vertices
        
        if action.flags & rd.ActionFlags.Drawcall:
            total_draws += 1
            
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
            
            # 根据 API 类型选择匹配方式
            if use_location_matching:
                # Vulkan / OpenGL: 使用 Location 匹配
                shader_locations = set()
                for sig in vs_refl.inputSignature:
                    # regIndex 对应 Vulkan 的 Location
                    loc = getattr(sig, 'regIndex', -1)
                    if loc >= 0:
                        shader_locations.add(loc)
                
                # 获取 IA 下发的顶点输入
                try:
                    vertex_inputs = pipe.GetVertexInputs()
                except:
                    vertex_inputs = []
                
                if not vertex_inputs:
                    for child in action.children:
                        process_action(child)
                    return
                
                # 检查浪费
                wasted_attrs = []
                wasted_bytes_per_vertex = 0
                
                for attr in vertex_inputs:
                    attr_location = getattr(attr, 'location', -1)
                    attr_name = getattr(attr, 'name', 'loc_{}'.format(attr_location))
                    
                    fmt = getattr(attr, 'format', None)
                    byte_size = get_format_byte_size(fmt) if fmt else 4
                    
                    attr_stats[attr_name]['provided'] += 1
                    
                    # 检查该 location 是否被 Shader 使用
                    if attr_location >= 0 and attr_location not in shader_locations:
                        wasted_attrs.append({
                            'name': attr_name,
                            'location': attr_location,
                            'size': byte_size
                        })
                        wasted_bytes_per_vertex += byte_size
                        attr_stats[attr_name]['wasted'] += 1
                    else:
                        attr_stats[attr_name]['used'] += 1
            else:
                # D3D: 使用语义名称匹配
                shader_semantics = set()
                for sig in vs_refl.inputSignature:
                    semantic_name = getattr(sig, 'semanticName', '')
                    semantic_index = getattr(sig, 'semanticIndex', 0)
                    if semantic_name:
                        shader_semantics.add("{}{}".format(semantic_name.upper(), semantic_index))
                
                # 获取 IA 下发的顶点输入
                try:
                    vertex_inputs = pipe.GetVertexInputs()
                except:
                    vertex_inputs = []
                
                if not vertex_inputs:
                    for child in action.children:
                        process_action(child)
                    return
                
                # 检查浪费
                wasted_attrs = []
                wasted_bytes_per_vertex = 0
                
                for attr in vertex_inputs:
                    attr_name = getattr(attr, 'name', '')
                    # 解析语义名称和索引
                    base_name = attr_name.rstrip('0123456789').upper()
                    idx_str = ''
                    for c in reversed(attr_name):
                        if c.isdigit():
                            idx_str = c + idx_str
                        else:
                            break
                    semantic_index = int(idx_str) if idx_str else 0
                    semantic_key = "{}{}".format(base_name, semantic_index)
                    
                    fmt = getattr(attr, 'format', None)
                    byte_size = get_format_byte_size(fmt) if fmt else 4
                    
                    attr_stats[attr_name]['provided'] += 1
                    
                    if semantic_key not in shader_semantics:
                        wasted_attrs.append({
                            'name': attr_name,
                            'size': byte_size
                        })
                        wasted_bytes_per_vertex += byte_size
                        attr_stats[attr_name]['wasted'] += 1
                    else:
                        attr_stats[attr_name]['used'] += 1
            
            # 记录浪费情况
            if wasted_attrs:
                draws_with_waste += 1
                num_vertices = getattr(action, 'numIndices', 0)
                if num_vertices == 0:
                    num_vertices = getattr(action, 'numVertices', 0)
                
                total_vertices += num_vertices
                total_wasted_bytes += wasted_bytes_per_vertex * num_vertices
                
                waste_details.append({
                    'eid': action.eventId,
                    'num_vertices': num_vertices,
                    'wasted_attrs': [a['name'] for a in wasted_attrs],
                    'wasted_bytes': wasted_bytes_per_vertex * num_vertices
                })
        
        for child in action.children:
            process_action(child)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    # 找出最常被浪费的属性
    most_wasted = sorted(
        [(k, v['wasted']) for k, v in attr_stats.items() if v['wasted'] > 0],
        key=lambda x: x[1], reverse=True
    )[:5]
    
    return {
        'total_draws': total_draws,
        'draws_with_waste': draws_with_waste,
        'waste_ratio': draws_with_waste / total_draws * 100 if total_draws > 0 else 0,
        'total_wasted_bytes': total_wasted_bytes,
        'total_vertices': total_vertices,
        'most_wasted_attrs': most_wasted,
        'waste_details': sorted(waste_details, key=lambda x: x['wasted_bytes'], reverse=True)[:10]
    }


def analyze_shader_bindings(controller):
    """Shader 资源绑定浪费分析"""
    from collections import defaultdict
    
    total_draws = 0
    draws_with_unused = 0
    unused_srv_count = 0
    unused_cbv_count = 0
    unused_uav_count = 0
    binding_details = []
    
    def process_action(action):
        nonlocal total_draws, draws_with_unused, unused_srv_count, unused_cbv_count, unused_uav_count
        
        if action.flags & rd.ActionFlags.Drawcall:
            total_draws += 1
            
            controller.SetFrameEvent(action.eventId, False)
            pipe = controller.GetPipelineState()
            
            draw_has_unused = False
            draw_unused_details = {'eid': action.eventId, 'srv': [], 'cbv': [], 'uav': []}
            
            # 检查每个 shader 阶段
            for stage in [rd.ShaderStage.Vertex, rd.ShaderStage.Fragment, rd.ShaderStage.Compute]:
                shader = pipe.GetShader(stage)
                if shader == rd.ResourceId.Null():
                    continue
                
                refl = pipe.GetShaderReflection(stage)
                if refl is None:
                    continue
                
                try:
                    mapping = pipe.GetBindpointMapping(stage)
                except:
                    continue
                
                # 检查只读资源 (SRV/Textures)
                if hasattr(mapping, 'readOnlyResources'):
                    for i, bp in enumerate(mapping.readOnlyResources):
                        if hasattr(bp, 'used') and not bp.used:
                            if hasattr(bp, 'bind') and bp.bind >= 0:
                                draw_has_unused = True
                                unused_srv_count += 1
                                draw_unused_details['srv'].append(bp.bind)
                
                # 检查常量缓冲区 (CBV)
                if hasattr(mapping, 'constantBlocks'):
                    for i, bp in enumerate(mapping.constantBlocks):
                        if hasattr(bp, 'used') and not bp.used:
                            if hasattr(bp, 'bind') and bp.bind >= 0:
                                draw_has_unused = True
                                unused_cbv_count += 1
                                draw_unused_details['cbv'].append(bp.bind)
                
                # 检查读写资源 (UAV)
                if hasattr(mapping, 'readWriteResources'):
                    for i, bp in enumerate(mapping.readWriteResources):
                        if hasattr(bp, 'used') and not bp.used:
                            if hasattr(bp, 'bind') and bp.bind >= 0:
                                draw_has_unused = True
                                unused_uav_count += 1
                                draw_unused_details['uav'].append(bp.bind)
            
            if draw_has_unused:
                draws_with_unused += 1
                if len(binding_details) < 10:
                    binding_details.append(draw_unused_details)
        
        for child in action.children:
            process_action(child)
    
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    return {
        'total_draws': total_draws,
        'draws_with_unused': draws_with_unused,
        'unused_ratio': draws_with_unused / total_draws * 100 if total_draws > 0 else 0,
        'unused_srv_count': unused_srv_count,
        'unused_cbv_count': unused_cbv_count,
        'unused_uav_count': unused_uav_count,
        'total_unused': unused_srv_count + unused_cbv_count + unused_uav_count,
        'binding_details': binding_details
    }


def print_summary_report(basic_stats, memory_stats, overdraw_stats, geometry_stats, elapsed_time):
    """打印综合摘要报告"""
    
    print("\n")
    print("=" * 80)
    print("               📊 RenderDoc Android 综合分析报告")
    print("=" * 80)
    print(f"  分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  耗时: {elapsed_time:.1f} 秒")
    
    # 基础统计
    print("\n" + "-" * 80)
    print("  📋 基础统计")
    print("-" * 80)
    if basic_stats:
        print(f"    Drawcall 总数:        {basic_stats['total_draws']:,}")
        print(f"    Dispatch 总数:        {basic_stats['total_dispatches']:,}")
        print(f"    Pass 数量:            {basic_stats['pass_count']}")
        print(f"    纹理数量:             {basic_stats['texture_count']}")
        print(f"    Buffer 数量:          {basic_stats['buffer_count']}")
    else:
        print("    ⚠️ 基础统计分析跳过或失败")
    
    # 内存统计
    print("\n" + "-" * 80)
    print("  💾 GPU 内存")
    print("-" * 80)
    if memory_stats:
        print(f"    总内存:               {format_size(memory_stats['total_memory'])}")
        print(f"    ├─ 纹理内存:          {format_size(memory_stats['texture_memory'])}")
        print(f"    └─ Buffer 内存:       {format_size(memory_stats['buffer_memory'])}")
    else:
        print("    ⚠️ 内存分析跳过或失败")
    
    # Overdraw 统计
    print("\n" + "-" * 80)
    print("  🎨 Overdraw")
    print("-" * 80)
    if overdraw_stats:
        print(f"    屏幕分辨率:           {overdraw_stats['screen_resolution']}")
        print(f"    平均 Overdraw:        {overdraw_stats['avg_overdraw']:.2f}x")
        
        # 评级
        avg = overdraw_stats['avg_overdraw']
        if avg < 2:
            rating = "✅ 优秀"
        elif avg < 3:
            rating = "👍 良好"
        elif avg < 5:
            rating = "⚠️ 一般"
        else:
            rating = "❌ 较差"
        print(f"    评级:                 {rating}")
    else:
        print("    ⚠️ Overdraw 分析跳过或失败")
    
    # 几何统计
    print("\n" + "-" * 80)
    print("  📐 几何复杂度")
    print("-" * 80)
    if geometry_stats:
        print(f"    总三角形数:           {format_number(geometry_stats['total_triangles'])}")
        print(f"    总实例数:             {geometry_stats['total_instances']:,}")
        print(f"    平均每 Draw 三角形:   {format_number(geometry_stats['avg_triangles_per_draw'])}")
    else:
        print("    ⚠️ 几何分析跳过或失败")
    
    # 优化建议
    print("\n" + "=" * 80)
    print("  💡 优化建议")
    print("=" * 80)
    
    suggestions = []
    
    if memory_stats and memory_stats['total_memory'] > 500 * 1024 * 1024:
        suggestions.append(f"  • GPU 内存较高 ({format_size(memory_stats['total_memory'])})，考虑纹理压缩")
    
    if overdraw_stats and overdraw_stats['avg_overdraw'] > 3:
        suggestions.append(f"  • Overdraw 较高 ({overdraw_stats['avg_overdraw']:.1f}x)，考虑 Z-Prepass")
    
    if geometry_stats and geometry_stats['total_triangles'] > 5_000_000:
        suggestions.append(f"  • 三角形数较多 ({format_number(geometry_stats['total_triangles'])})，考虑 LOD")
    
    if basic_stats and basic_stats['total_draws'] > 2000:
        suggestions.append(f"  • Drawcall 较多 ({basic_stats['total_draws']})，考虑批处理合并")
    
    if not suggestions:
        print("  ✅ 整体性能良好，没有明显问题")
    else:
        for s in suggestions:
            print(s)
    
    print("\n" + "=" * 80)


def print_full_report(basic_stats, memory_stats, overdraw_stats, geometry_stats, 
                      vertex_attrs_stats, shader_bindings_stats, elapsed_time):
    """打印完整综合报告"""
    
    print("\n")
    print("=" * 80)
    print("               📊 RenderDoc Android 综合分析报告")
    print("=" * 80)
    print("  分析时间: {}".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    print("  耗时: {:.1f} 秒".format(elapsed_time))
    
    # 基础统计
    print("\n" + "-" * 80)
    print("  📋 基础统计")
    print("-" * 80)
    if basic_stats:
        print("    Drawcall 总数:        {:,}".format(basic_stats['total_draws']))
        print("    Dispatch 总数:        {:,}".format(basic_stats['total_dispatches']))
        print("    Pass 数量:            {}".format(basic_stats['pass_count']))
        print("    纹理数量:             {}".format(basic_stats['texture_count']))
        print("    Buffer 数量:          {}".format(basic_stats['buffer_count']))
    else:
        print("    ⚠️ 基础统计分析跳过或失败")
    
    # 内存统计
    print("\n" + "-" * 80)
    print("  💾 GPU 内存")
    print("-" * 80)
    if memory_stats:
        print("    总内存:               {}".format(format_size(memory_stats['total_memory'])))
        print("    ├─ 纹理内存:          {}".format(format_size(memory_stats['texture_memory'])))
        print("    └─ Buffer 内存:       {}".format(format_size(memory_stats['buffer_memory'])))
    else:
        print("    ⚠️ 内存分析跳过或失败")
    
    # Overdraw 统计
    print("\n" + "-" * 80)
    print("  🎨 Overdraw (启发式估算)")
    print("-" * 80)
    if overdraw_stats:
        print("    屏幕分辨率:           {}".format(overdraw_stats['screen_resolution']))
        print("    平均 Overdraw:        {:.2f}x".format(overdraw_stats['avg_overdraw']))
        
        avg = overdraw_stats['avg_overdraw']
        if avg < 2:
            rating = "✅ 优秀"
        elif avg < 3:
            rating = "👍 良好"
        elif avg < 5:
            rating = "⚠️ 一般"
        else:
            rating = "❌ 较差"
        print("    评级:                 {}".format(rating))
    else:
        print("    ⚠️ Overdraw 分析跳过或失败")
    
    # 几何统计
    print("\n" + "-" * 80)
    print("  📐 几何复杂度")
    print("-" * 80)
    if geometry_stats:
        print("    总三角形数:           {}".format(format_number(geometry_stats['total_triangles'])))
        print("    总实例数:             {:,}".format(geometry_stats['total_instances']))
        print("    平均每 Draw 三角形:   {}".format(format_number(geometry_stats['avg_triangles_per_draw'])))
    else:
        print("    ⚠️ 几何分析跳过或失败")
    
    # 顶点属性浪费
    print("\n" + "-" * 80)
    print("  🔺 顶点属性浪费")
    print("-" * 80)
    if vertex_attrs_stats:
        print("    总 Draw 调用:         {:,}".format(vertex_attrs_stats['total_draws']))
        print("    存在浪费的 Draw:      {:,} ({:.1f}%)".format(
            vertex_attrs_stats['draws_with_waste'],
            vertex_attrs_stats['waste_ratio']))
        print("    浪费的带宽:           {}".format(format_size(vertex_attrs_stats['total_wasted_bytes'])))
        
        if vertex_attrs_stats['most_wasted_attrs']:
            attrs_str = ", ".join(["{}({}次)".format(n, c) for n, c in vertex_attrs_stats['most_wasted_attrs']])
            print("    最常浪费属性:         {}".format(attrs_str))
        
        if vertex_attrs_stats['waste_ratio'] > 20:
            print("    评级:                 ❌ 较差 - 大量顶点属性被浪费")
        elif vertex_attrs_stats['waste_ratio'] > 5:
            print("    评级:                 ⚠️ 一般 - 存在顶点属性浪费")
        else:
            print("    评级:                 ✅ 良好")
        
        # 打印 Top 10 浪费最多的 Draw Call
        if vertex_attrs_stats.get('waste_details'):
            print("\n    📋 顶点属性浪费 Top 10 Draw Calls:")
            print("    " + "-" * 70)
            print("    {:>8}  {:>12}  {:>14}  {}".format("EID", "顶点数", "浪费带宽", "浪费属性"))
            print("    " + "-" * 70)
            for detail in vertex_attrs_stats['waste_details'][:10]:
                attrs = ", ".join(detail['wasted_attrs'][:5])
                if len(detail['wasted_attrs']) > 5:
                    attrs += "..."
                print("    {:>8}  {:>12,}  {:>14}  {}".format(
                    detail['eid'],
                    detail['num_vertices'],
                    format_size(detail['wasted_bytes']),
                    attrs
                ))
            print("    " + "-" * 70)
    else:
        print("    ⚠️ 顶点属性分析跳过或失败")
    
    # Shader 资源绑定浪费
    print("\n" + "-" * 80)
    print("  🎯 Shader 资源绑定")
    print("-" * 80)
    if shader_bindings_stats:
        print("    总 Draw 调用:         {:,}".format(shader_bindings_stats['total_draws']))
        print("    存在未使用绑定的 Draw: {:,} ({:.1f}%)".format(
            shader_bindings_stats['draws_with_unused'],
            shader_bindings_stats['unused_ratio']))
        print("    未使用 SRV/纹理:      {}".format(shader_bindings_stats['unused_srv_count']))
        print("    未使用 CBV/常量:      {}".format(shader_bindings_stats['unused_cbv_count']))
        print("    未使用 UAV:           {}".format(shader_bindings_stats['unused_uav_count']))
        
        if shader_bindings_stats['unused_ratio'] > 30:
            print("    评级:                 ❌ 较差 - 大量资源绑定被浪费")
        elif shader_bindings_stats['unused_ratio'] > 10:
            print("    评级:                 ⚠️ 一般 - 存在资源绑定浪费")
        else:
            print("    评级:                 ✅ 良好")
    else:
        print("    ⚠️ Shader 绑定分析跳过或失败")
    
    # 优化建议
    print("\n" + "=" * 80)
    print("  💡 优化建议")
    print("=" * 80)
    
    suggestions = []
    
    if memory_stats and memory_stats['total_memory'] > 500 * 1024 * 1024:
        suggestions.append("  • GPU 内存较高 ({})，考虑纹理压缩".format(format_size(memory_stats['total_memory'])))
    
    if overdraw_stats and overdraw_stats['avg_overdraw'] > 3:
        suggestions.append("  • Overdraw 可能较高 ({:.1f}x)，考虑 Z-Prepass".format(overdraw_stats['avg_overdraw']))
    
    if geometry_stats and geometry_stats['total_triangles'] > 5_000_000:
        suggestions.append("  • 三角形数较多 ({})，考虑 LOD".format(format_number(geometry_stats['total_triangles'])))
    
    if basic_stats and basic_stats['total_draws'] > 2000:
        suggestions.append("  • Drawcall 较多 ({})，考虑批处理合并".format(basic_stats['total_draws']))
    
    if vertex_attrs_stats and vertex_attrs_stats['waste_ratio'] > 10:
        suggestions.append("  • 顶点属性浪费较多 ({:.1f}%)，考虑优化顶点布局".format(vertex_attrs_stats['waste_ratio']))
    
    if shader_bindings_stats and shader_bindings_stats['unused_ratio'] > 20:
        suggestions.append("  • 资源绑定浪费较多 ({:.1f}%)，考虑优化材质变体".format(shader_bindings_stats['unused_ratio']))
    
    if not suggestions:
        print("  ✅ 整体性能良好，没有明显问题")
    else:
        for s in suggestions:
            print(s)
    
    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(description='RenderDoc Android 综合分析')
    parser.add_argument('rdc_path', help='RDC 文件路径 (本地路径，会自动上传到 Android)')
    parser.add_argument('--host', default=DEFAULT_HOST, help='远程服务器地址 (默认: {})'.format(DEFAULT_HOST))
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='远程服务器端口 (默认: {})'.format(DEFAULT_PORT))
    parser.add_argument('--no-forward', action='store_true', help='跳过 ADB 端口转发设置')
    
    # 分析模块选项
    parser.add_argument('--all', action='store_true', help='执行所有分析 (默认)')
    parser.add_argument('--basic', action='store_true', help='基础统计')
    parser.add_argument('--memory', action='store_true', help='内存分析')
    parser.add_argument('--overdraw', action='store_true', help='Overdraw 分析')
    parser.add_argument('--geometry', action='store_true', help='几何复杂度分析')
    parser.add_argument('--vertex-attrs', action='store_true', help='顶点属性浪费分析')
    parser.add_argument('--shader-bindings', action='store_true', help='Shader 资源绑定分析')
    
    args = parser.parse_args()
    
    # 如果没有指定任何分析，默认执行全部
    run_all = args.all or not any([
        args.basic, args.memory, args.overdraw, args.geometry, 
        args.vertex_attrs, args.shader_bindings
    ])
    
    print("=" * 80)
    print("          RenderDoc Android 综合分析工具")
    print("=" * 80)
    
    start_time = time.time()
    
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
        try:
            remote.ShutdownAndDisconnect()
        except:
            pass
        sys.exit(1)
    
    # 执行分析
    basic_stats = None
    memory_stats = None
    overdraw_stats = None
    geometry_stats = None
    vertex_attrs_stats = None
    shader_bindings_stats = None
    
    try:
        if run_all or args.basic:
            print("\n📋 执行基础统计分析...", flush=True)
            try:
                basic_stats = analyze_basic_stats(controller)
                print("   ✅ 完成")
            except Exception as e:
                print("   ❌ 失败: {}".format(e))
        
        if run_all or args.memory:
            print("\n💾 执行内存分析...", flush=True)
            try:
                memory_stats = analyze_memory(controller)
                print("   ✅ 完成")
            except Exception as e:
                print("   ❌ 失败: {}".format(e))
        
        if run_all or args.overdraw:
            print("\n🎨 执行 Overdraw 分析...", flush=True)
            try:
                overdraw_stats = analyze_overdraw(controller)
                print("   ✅ 完成")
            except Exception as e:
                print("   ❌ 失败: {}".format(e))
        
        if run_all or args.geometry:
            print("\n📐 执行几何复杂度分析...", flush=True)
            try:
                geometry_stats = analyze_geometry(controller)
                print("   ✅ 完成")
            except Exception as e:
                print("   ❌ 失败: {}".format(e))
        
        if run_all or args.vertex_attrs:
            print("\n🔺 执行顶点属性浪费分析...", flush=True)
            try:
                vertex_attrs_stats = analyze_vertex_attributes(controller)
                print("   ✅ 完成")
            except Exception as e:
                print("   ❌ 失败: {}".format(e))
                import traceback
                traceback.print_exc()
        
        if run_all or args.shader_bindings:
            print("\n🎯 执行 Shader 资源绑定分析...", flush=True)
            try:
                shader_bindings_stats = analyze_shader_bindings(controller)
                print("   ✅ 完成")
            except Exception as e:
                print("   ❌ 失败: {}".format(e))
                import traceback
                traceback.print_exc()
        
        elapsed_time = time.time() - start_time
        
        # 打印完整报告
        print_full_report(basic_stats, memory_stats, overdraw_stats, geometry_stats,
                          vertex_attrs_stats, shader_bindings_stats, elapsed_time)
        
    finally:
        try:
            controller.Shutdown()
        except:
            pass
        try:
            remote.ShutdownAndDisconnect()
        except:
            pass
    
    print("\n分析完成!")


if __name__ == "__main__":
    main()
