#!/usr/bin/env python3
"""
RenderDoc Shader 绑定使用分析脚本

用法: python analyze_shader_bindings.py <rdc_file_path>

功能:
- 检查每个 Draw/Dispatch 调用中绑定到 Shader 的资源
- 使用 RenderDoc 的 staticallyUnused 属性判断资源是否被着色器使用
- 识别绑定了但着色器代码静态分析认为不会使用的资源
- 统计浪费的绑定操作

原理:
- GetReadOnlyResources(stage) 返回的数组与 GetShaderReflection(stage).readOnlyResources 一一对应
- 每个绑定的 access.staticallyUnused 属性表示编译器静态分析认为该资源是否会被访问
- staticallyUnused=True 表示着色器代码中声明了该资源，但编译器认为不会被实际使用
"""

import sys
import os
import signal
import threading
from collections import defaultdict

# 超时设置 (秒)
TIMEOUT_SECONDS = 300  # 5分钟

class TimeoutError(Exception):
    pass

def timeout_handler():
    """超时处理函数"""
    print("\n" + "="*80)
    print("⚠️  脚本执行超时 (超过5分钟)，强制退出...")
    print("="*80)
    os._exit(1)

# 设置超时定时器
timeout_timer = None

def start_timeout():
    """启动超时定时器"""
    global timeout_timer
    timeout_timer = threading.Timer(TIMEOUT_SECONDS, timeout_handler)
    timeout_timer.daemon = True
    timeout_timer.start()

def cancel_timeout():
    """取消超时定时器"""
    global timeout_timer
    if timeout_timer:
        timeout_timer.cancel()

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

def get_shader_stage_name(stage):
    """获取 Shader 阶段名称"""
    import renderdoc as rd
    stage_names = {
        int(rd.ShaderStage.Vertex): "VS",
        int(rd.ShaderStage.Hull): "HS",
        int(rd.ShaderStage.Domain): "DS",
        int(rd.ShaderStage.Geometry): "GS",
        int(rd.ShaderStage.Pixel): "PS",
        int(rd.ShaderStage.Compute): "CS",
    }
    return stage_names.get(int(stage), f"Stage{int(stage)}")

def analyze_shader_bindings(rdc_path):
    """分析 Shader 绑定使用情况"""
    
    try:
        import renderdoc as rd
    except ImportError:
        print("错误: 无法导入 renderdoc 模块")
        sys.exit(1)
    
    if not os.path.exists(rdc_path):
        print(f"错误: 文件不存在 - {rdc_path}")
        sys.exit(1)
    
    print(f"正在分析 Shader 绑定使用情况: {rdc_path}")
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
    total_bindings = 0
    unused_bindings = 0
    unused_binding_details = []  # 记录未使用绑定的详情
    
    # 按类型统计
    binding_stats = defaultdict(lambda: {'total': 0, 'unused': 0})
    
    print("\n正在扫描所有 Draw/Dispatch 调用...", flush=True)
    
    def check_bindings(bindings, bind_type, stage_name, action, refl_resources=None):
        """
        检查绑定列表，使用 staticallyUnused 属性判断是否被使用
        
        bindings: GetReadOnlyResources/GetConstantBlocks 等返回的绑定列表
        bind_type: 绑定类型名称 (SRV, ConstantBuffer, UAV)
        stage_name: 着色器阶段名称
        action: 当前 action
        refl_resources: 反射信息中的资源列表（用于获取资源名称）
        """
        nonlocal total_bindings, unused_bindings
        
        for i, binding in enumerate(bindings):
            # 获取资源 ID
            if hasattr(binding, 'descriptor'):
                res_id = binding.descriptor.resource
            else:
                continue
            
            if res_id == rd.ResourceId.Null():
                continue
            
            total_bindings += 1
            binding_stats[bind_type]['total'] += 1
            
            # 使用 access.staticallyUnused 判断是否被使用
            is_unused = False
            if hasattr(binding, 'access'):
                is_unused = getattr(binding.access, 'staticallyUnused', False)
            
            # 获取反射中的资源名称和槽位
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
                    'index': i,
                    'slot': slot_num,
                    'name': res_name,
                    'resource': str(res_id)
                })
    
    # 遍历所有 action
    def process_action(action):
        nonlocal total_draws
        
        # 每100个draw打印进度
        if total_draws > 0 and total_draws % 100 == 0:
            print(f"  已处理 {total_draws} 个 Draw/Dispatch...", flush=True)
        
        flags = int(action.flags)
        is_draw = flags & int(rd.ActionFlags.Drawcall)
        is_dispatch = flags & int(rd.ActionFlags.Dispatch)
        
        if is_draw or is_dispatch:
            total_draws += 1
            
            # 移动到这个 event
            controller.SetFrameEvent(action.eventId, False)
            
            # 获取 pipeline state
            pipe = controller.GetPipelineState()
            
            # 确定要检查的 shader stages
            if is_dispatch:
                stages = [rd.ShaderStage.Compute]
            else:
                stages = [
                    rd.ShaderStage.Vertex,
                    rd.ShaderStage.Pixel,
                    rd.ShaderStage.Geometry,
                    rd.ShaderStage.Hull,
                    rd.ShaderStage.Domain
                ]
            
            for stage in stages:
                shader = pipe.GetShader(stage)
                if shader == rd.ResourceId.Null():
                    continue
                
                # 获取 shader 反射信息
                refl = pipe.GetShaderReflection(stage)
                if refl is None:
                    continue
                
                stage_name = get_shader_stage_name(stage)
                
                # 检查 Constant Buffers
                try:
                    cb_bindings = pipe.GetConstantBlocks(stage, False)
                    refl_cbs = refl.constantBlocks if hasattr(refl, 'constantBlocks') else None
                    check_bindings(cb_bindings, 'ConstantBuffer', stage_name, action, refl_cbs)
                except Exception as e:
                    pass
                
                # 检查 SRVs (Shader Resource Views)
                try:
                    ro_resources = pipe.GetReadOnlyResources(stage)
                    refl_srvs = refl.readOnlyResources if hasattr(refl, 'readOnlyResources') else None
                    check_bindings(ro_resources, 'SRV', stage_name, action, refl_srvs)
                except Exception as e:
                    pass
                
                # 检查 UAVs
                try:
                    rw_resources = pipe.GetReadWriteResources(stage)
                    refl_uavs = refl.readWriteResources if hasattr(refl, 'readWriteResources') else None
                    check_bindings(rw_resources, 'UAV', stage_name, action, refl_uavs)
                except:
                    pass
        
        # 递归处理子 action
        for child in action.children:
            process_action(child)
    
    # 处理所有 root actions
    root_actions = controller.GetRootActions()
    for action in root_actions:
        process_action(action)
    
    # 输出报告
    print(f"\n{'='*80}")
    print("                         分析结果汇总")
    print("=" * 80)
    
    print(f"\n  总 Draw/Dispatch 调用数: {total_draws}")
    print(f"  总绑定数量: {total_bindings}")
    print(f"  未使用绑定数量: {unused_bindings}")
    
    if total_bindings > 0:
        waste_ratio = unused_bindings / total_bindings * 100
        print(f"\n  📊 绑定利用率: {100 - waste_ratio:.1f}%")
        print(f"  ⚠️  未使用绑定: {unused_bindings} ({waste_ratio:.1f}%)")
    
    # 按类型统计
    print(f"\n{'='*80}")
    print("                      按绑定类型统计")
    print("=" * 80)
    
    print(f"\n  {'类型':<20} {'总绑定':<12} {'未使用':<12} {'未使用率':<12}")
    print(f"  {'-'*55}")
    
    for bind_type in ['ConstantBuffer', 'SRV', 'UAV', 'Sampler']:
        stats = binding_stats[bind_type]
        if stats['total'] > 0:
            ratio = stats['unused'] / stats['total'] * 100
            print(f"  {bind_type:<20} {stats['total']:<12} {stats['unused']:<12} {ratio:.1f}%")
    
    # 显示部分未使用绑定详情
    if unused_binding_details:
        print(f"\n{'='*80}")
        print("                    未使用绑定详情 (前 30 个)")
        print("=" * 80)
        
        print(f"\n  {'EID':<8} {'Stage':<6} {'类型':<15} {'槽位':<6} {'ResourceId':<25}")
        print(f"  {'-'*65}")
        
        for detail in unused_binding_details[:30]:
            print(f"  {detail['eid']:<8} {detail['stage']:<6} {detail['type']:<15} {detail['slot']:<6} {detail['resource']:<25}")
        
        if len(unused_binding_details) > 30:
            print(f"\n  ... 还有 {len(unused_binding_details) - 30} 个未显示")
    
    # 按 Pass 统计未使用绑定
    if unused_binding_details:
        print(f"\n{'='*80}")
        print("                 按 Action 统计未使用绑定 (前 20 个)")
        print("=" * 80)
        
        action_stats = defaultdict(lambda: {'count': 0, 'types': defaultdict(int)})
        for detail in unused_binding_details:
            key = f"EID {detail['eid']}"
            action_stats[key]['count'] += 1
            action_stats[key]['types'][detail['type']] += 1
        
        sorted_actions = sorted(action_stats.items(), key=lambda x: x[1]['count'], reverse=True)
        
        print(f"\n  {'EID':<12} {'未使用绑定数':<15} {'详情':<40}")
        print(f"  {'-'*65}")
        
        for action_key, stats in sorted_actions[:20]:
            types_str = ", ".join([f"{t}:{c}" for t, c in stats['types'].items()])
            print(f"  {action_key:<12} {stats['count']:<15} {types_str:<40}")
    
    # 输出总结
    if unused_bindings == 0:
        print(f"\n{'='*80}")
        print("  ✅ 未发现静态未使用的绑定！")
        print("     所有绑定的资源都被着色器代码使用。")
        print("=" * 80)
    
    controller.Shutdown()
    cap.Shutdown()
    
    print("\n分析完成!")
    print("\n" + "="*80)
    print("说明:")
    print("  - 此分析基于着色器编译时的静态分析 (staticallyUnused 属性)")
    print("  - staticallyUnused=True 表示编译器认为该资源声明了但不会被访问")
    print("  - 动态分支可能导致运行时实际不使用某些资源，但静态分析无法检测")
    print("  - GetReadOnlyResources() 返回与 ShaderReflection.readOnlyResources 一一对应的绑定")
    print("=" * 80)


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_shader_bindings.py <rdc_file_path>")
        sys.exit(1)
    
    # 启动5分钟超时定时器
    start_timeout()
    print(f"⏱️  超时设置: {TIMEOUT_SECONDS}秒 ({TIMEOUT_SECONDS//60}分钟)")
    
    try:
        analyze_shader_bindings(sys.argv[1])
    finally:
        cancel_timeout()

if __name__ == "__main__":
    main()
