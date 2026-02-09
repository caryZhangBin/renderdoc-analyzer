#!/usr/bin/env python3
"""
RenderDoc 顶点属性浪费分析脚本

用法: python analyze_vertex_attributes.py <rdc_file_path>

功能:
- 检查每个 Draw 调用中的顶点输入配置
- 比较 Input Layout 中提供的顶点属性 vs Vertex Shader 实际需要的属性
- 识别传给 Shader 但未被使用的顶点属性（如 Normal、Color、Tangent 等）
- 估算因顶点属性浪费造成的带宽损失

原理:
- Input Layout 定义了顶点缓冲区中包含哪些属性（position, normal, uv 等）
- Vertex Shader 的 inputSignature 定义了着色器实际读取哪些属性
- 如果 Input Layout 中的某个属性不在 inputSignature 中，说明该数据被传输但未使用
"""

import sys
import os
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
    import renderdoc as rd
    fmt_str = str(fmt).lower()
    
    # 常见格式的字节大小
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

def analyze_vertex_attributes(rdc_path):
    """分析顶点属性使用情况"""
    
    # 导入 renderdoc
    try:
        import renderdoc as rd
    except ImportError:
        print("错误: 无法导入 renderdoc 模块")
        sys.exit(1)
    
    if not os.path.exists(rdc_path):
        print(f"错误: 文件不存在 - {rdc_path}")
        sys.exit(1)
    
    print(f"正在分析顶点属性使用情况: {rdc_path}")
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
    
    # 统计数据
    total_draws = 0
    draws_with_waste = 0
    total_wasted_bytes_per_vertex = 0
    total_vertices_drawn = 0
    waste_details = []
    
    # 按语义统计
    semantic_stats = defaultdict(lambda: {'provided': 0, 'used': 0, 'wasted': 0})
    
    print("\n正在扫描所有 Draw 调用...", flush=True)
    
    def process_action(action):
        nonlocal total_draws, draws_with_waste, total_wasted_bytes_per_vertex, total_vertices_drawn
        
        flags = int(action.flags)
        is_draw = flags & int(rd.ActionFlags.Drawcall)
        
        if is_draw:
            total_draws += 1
            
            if total_draws % 100 == 0:
                print(f"  已处理 {total_draws} 个 Draw...", flush=True)
            
            # 移动到这个 event
            controller.SetFrameEvent(action.eventId, False)
            
            # 获取 pipeline state
            pipe = controller.GetPipelineState()
            
            # 获取顶点着色器
            vs_shader = pipe.GetShader(rd.ShaderStage.Vertex)
            if vs_shader == rd.ResourceId.Null():
                # 递归处理子 action
                for child in action.children:
                    process_action(child)
                return
            
            # 获取顶点着色器反射
            vs_refl = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            if vs_refl is None:
                for child in action.children:
                    process_action(child)
                return
            
            # 获取着色器实际使用的输入语义
            # channelUsedMask > 0 表示着色器代码实际读取了该输入的某些通道
            shader_inputs = set()
            shader_input_details = {}
            for sig in vs_refl.inputSignature:
                semantic_name = sig.semanticName if hasattr(sig, 'semanticName') else ''
                semantic_index = sig.semanticIndex if hasattr(sig, 'semanticIndex') else 0
                semantic_key = f"{semantic_name}{semantic_index}"
                
                # channelUsedMask == 0 表示声明了但没有实际使用
                channel_used_mask = getattr(sig, 'channelUsedMask', 0xF)
                is_actually_used = channel_used_mask > 0
                
                shader_input_details[semantic_key] = {
                    'used': is_actually_used,
                    'mask': channel_used_mask
                }
                
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
            
            # 比较：哪些属性提供了但未被使用
            provided_attrs = []
            wasted_attrs = []
            wasted_bytes = 0
            
            for attr in vertex_inputs:
                # 获取属性信息
                semantic_name = attr.name if hasattr(attr, 'name') else ''
                # 有些 API 返回的是完整语义如 "POSITION0"
                # 需要解析出基础名称
                base_name = semantic_name.rstrip('0123456789')
                semantic_index = ''
                for c in reversed(semantic_name):
                    if c.isdigit():
                        semantic_index = c + semantic_index
                    else:
                        break
                semantic_index = int(semantic_index) if semantic_index else 0
                semantic_key = f"{base_name}{semantic_index}"
                
                # 获取格式和大小
                fmt = attr.format if hasattr(attr, 'format') else None
                byte_size = get_format_byte_size(fmt) if fmt else 4
                
                provided_attrs.append({
                    'name': semantic_name,
                    'key': semantic_key,
                    'format': str(fmt) if fmt else 'Unknown',
                    'size': byte_size
                })
                
                semantic_stats[base_name]['provided'] += 1
                
                # 检查是否被着色器使用
                if semantic_key not in shader_inputs:
                    wasted_attrs.append({
                        'name': semantic_name,
                        'key': semantic_key,
                        'format': str(fmt) if fmt else 'Unknown',
                        'size': byte_size
                    })
                    wasted_bytes += byte_size
                    semantic_stats[base_name]['wasted'] += 1
            
            if wasted_attrs:
                draws_with_waste += 1
                
                # 估算顶点数
                num_vertices = action.numIndices if hasattr(action, 'numIndices') else 0
                if num_vertices == 0:
                    num_vertices = action.numVertices if hasattr(action, 'numVertices') else 0
                
                total_vertices_drawn += num_vertices
                total_wasted_bytes_per_vertex += wasted_bytes * num_vertices
                
                waste_details.append({
                    'eid': action.eventId,
                    'num_vertices': num_vertices,
                    'provided': [a['name'] for a in provided_attrs],
                    'shader_needs': list(shader_inputs),
                    'wasted': wasted_attrs,
                    'wasted_bytes_per_vertex': wasted_bytes,
                    'total_wasted_bytes': wasted_bytes * num_vertices
                })
        
        # 递归处理子 action
        for child in action.children:
            process_action(child)
    
    # 处理所有 root actions
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    # 输出报告
    print(f"\n{'='*80}")
    print("                    顶点属性浪费分析结果")
    print("=" * 80)
    
    print(f"\n  总 Draw 调用数: {total_draws}")
    print(f"  存在属性浪费的 Draw 数: {draws_with_waste}")
    
    if total_draws > 0:
        waste_ratio = draws_with_waste / total_draws * 100
        print(f"  浪费率: {waste_ratio:.1f}%")
    
    print(f"\n  📊 带宽浪费估算:")
    print(f"     总顶点数: {total_vertices_drawn:,}")
    print(f"     浪费的带宽: {format_size(total_wasted_bytes_per_vertex)}")
    
    # 按语义统计
    print(f"\n{'='*80}")
    print("                    按语义统计")
    print("=" * 80)
    
    print(f"\n  {'语义名称':<20} {'提供次数':<12} {'使用次数':<12} {'浪费次数':<12}")
    print(f"  {'-'*55}")
    
    sorted_semantics = sorted(semantic_stats.items(), key=lambda x: x[1]['wasted'], reverse=True)
    for semantic_name, stats in sorted_semantics:
        if stats['provided'] > 0 or stats['used'] > 0:
            print(f"  {semantic_name:<20} {stats['provided']:<12} {stats['used']:<12} {stats['wasted']:<12}")
    
    # 显示浪费最严重的 Draw 调用
    if waste_details:
        print(f"\n{'='*80}")
        print("                浪费最严重的 Draw 调用 (前 20 个)")
        print("=" * 80)
        
        sorted_waste = sorted(waste_details, key=lambda x: x['total_wasted_bytes'], reverse=True)
        
        for detail in sorted_waste[:20]:
            print(f"\n  EID {detail['eid']}:")
            print(f"    顶点数: {detail['num_vertices']:,}")
            print(f"    着色器需要: {', '.join(detail['shader_needs'])}")
            print(f"    浪费的属性:")
            for attr in detail['wasted']:
                print(f"      - {attr['name']} ({attr['format']}, {attr['size']} bytes)")
            print(f"    每顶点浪费: {detail['wasted_bytes_per_vertex']} bytes")
            print(f"    总浪费: {format_size(detail['total_wasted_bytes'])}")
    
    # 输出总结和建议
    print(f"\n{'='*80}")
    print("                         分析总结")
    print("=" * 80)
    
    if draws_with_waste == 0:
        print("\n  ✅ 未发现顶点属性浪费！")
        print("     所有顶点缓冲区中的数据都被着色器使用。")
    else:
        print(f"\n  ⚠️  发现 {draws_with_waste} 个 Draw 调用存在顶点属性浪费")
        print(f"     总计浪费带宽: {format_size(total_wasted_bytes_per_vertex)}")
        print("\n  💡 优化建议:")
        print("     1. 为不同的 Shader 创建专用的顶点布局")
        print("     2. 移除 Mesh 中着色器不需要的属性数据")
        print("     3. 考虑使用顶点压缩技术减少不必要的数据传输")
        
        # 找出最常被浪费的属性
        most_wasted = sorted([(k, v['wasted']) for k, v in semantic_stats.items() if v['wasted'] > 0], 
                            key=lambda x: x[1], reverse=True)
        if most_wasted:
            print(f"\n     最常被浪费的属性: {', '.join([f'{n}({c}次)' for n, c in most_wasted[:5]])}")
    
    print("=" * 80)
    
    controller.Shutdown()
    cap.Shutdown()
    
    print("\n分析完成!")


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_vertex_attributes.py <rdc_file_path>")
        sys.exit(1)
    analyze_vertex_attributes(sys.argv[1])


if __name__ == "__main__":
    main()
