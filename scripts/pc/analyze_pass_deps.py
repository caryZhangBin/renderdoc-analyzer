#!/usr/bin/env python3
"""
RenderDoc Pass 依赖分析脚本

用法: python analyze_pass_deps.py <rdc_file_path>

功能:
- 分析每个 Pass 最后输出的 RT（包括 ColorTarget 和 CS UAV）
- 检查这些 RT 是否被后续 Pass 使用
- 检测"死渲染"（产生但未被后续使用的 RT）
"""

import sys
import os
from collections import defaultdict

def analyze_pass_deps(rdc_path):
    """分析 Pass 依赖关系"""
    
    # 设置 RenderDoc 模块路径 (优先使用系统安装的稳定版)
    RENDERDOC_PATHS = [
        r"C:\Program Files\RenderDoc\pymodules",  # 优先: 系统安装的稳定版
        r"E:\code build\renderdoc-1.x\renderdoc-1.x\x64\Development\pymodules",  # 备选: 本地源码编译版
        os.environ.get('RENDERDOC_MODULE_PATH', ''),
    ]
    
    for p in RENDERDOC_PATHS:
        if p and os.path.exists(p) and p not in sys.path:
            sys.path.insert(0, p)
    
    try:
        import renderdoc as rd
        print(f"✅ renderdoc 模块导入成功")
        print(f"   模块路径: {rd.__file__}")
    except ImportError as e:
        print(f"错误: 无法导入 renderdoc 模块: {e}")
        sys.exit(1)
    
    if not os.path.exists(rdc_path):
        print(f"错误: 文件不存在 - {rdc_path}")
        sys.exit(1)
    
    print(f"正在分析 Pass 依赖: {rdc_path}")
    print(f"文件大小: {os.path.getsize(rdc_path) / 1024 / 1024:.1f} MB")
    print("=" * 70)
    
    # 初始化 RenderDoc 回放环境
    print("初始化 RenderDoc...")
    rd.InitialiseReplay(rd.GlobalEnvironment(), [])
    
    cap = rd.OpenCaptureFile()
    result = cap.OpenFile(rdc_path, '', None)
    
    if result != rd.ResultCode.Succeeded:
        print(f"错误: 无法打开文件 - {result}")
        sys.exit(1)
    
    print(f"文件 API: {cap.DriverName()}")
    print(f"本地回放支持: {cap.LocalReplaySupport()}")
    
    if not cap.LocalReplaySupport():
        print("错误: 当前系统不支持本地回放")
        cap.Shutdown()
        sys.exit(1)
    
    print("正在创建回放控制器 (大文件可能需要1-2分钟)...")
    sys.stdout.flush()
    
    def progress_cb(p):
        if int(p * 100) % 25 == 0:
            print(f"  加载进度: {p*100:.0f}%", flush=True)
    
    try:
        result = cap.OpenCapture(rd.ReplayOptions(), progress_cb)
    except Exception as e:
        print(f"\n❌ OpenCapture 异常: {e}")
        import traceback
        traceback.print_exc()
        cap.Shutdown()
        sys.exit(1)
    
    print()  # 换行
    print(f"OpenCapture 返回类型: {type(result)}")
    sys.stdout.flush()
    
    if result is None:
        print("错误: OpenCapture 返回 None")
        cap.Shutdown()
        sys.exit(1)
    
    controller = None
    if isinstance(result, tuple):
        print(f"返回元组, 长度={len(result)}")
        sys.stdout.flush()
        status, controller = result
        if status != rd.ResultCode.Succeeded:
            print(f"错误: 无法创建回放控制器 - {status}")
            cap.Shutdown()
            sys.exit(1)
    else:
        controller = result
    
    if controller is None:
        print("错误: controller 为 None")
        cap.Shutdown()
        sys.exit(1)
    
    print("✅ 回放控制器创建成功")
    sys.stdout.flush()
    
    # 获取正确的 ActionFlags 值
    DRAWCALL_FLAG = int(rd.ActionFlags.Drawcall)  # = 2
    DISPATCH_FLAG = int(rd.ActionFlags.Dispatch)  # = 4
    
    root_actions = controller.GetRootActions()
    
    passes = []
    for action in root_actions:
        name = action.customName or ""
        if name and not name.startswith("=>") and not name.isdigit():
            passes.append({'name': name, 'event_id': action.eventId, 'action': action})
    
    print(f"\n共 {len(passes)} 个 Pass")
    
    def get_last_event_id(action):
        if action.children:
            return get_last_event_id(action.children[-1])
        return action.eventId
    
    def find_last_draw_or_dispatch(action):
        """递归查找 Pass 内最后一个 Draw/Dispatch，返回 (eid, is_dispatch)"""
        last_result = None
        flags = int(action.flags)
        
        if flags & DRAWCALL_FLAG:
            last_result = (action.eventId, False)  # Draw
        elif flags & DISPATCH_FLAG:
            last_result = (action.eventId, True)   # Dispatch
        
        for c in action.children:
            child_result = find_last_draw_or_dispatch(c)
            if child_result:
                last_result = child_result
        
        return last_result
    
    # 收集每个 Pass 的输出 RT
    print("\n收集每个 Pass 的输出 RT...")
    
    pass_outputs = {}
    rt_producer = {}  # rt_id -> (pass_idx, pass_name, draw_eid, last_eid)
    
    for idx, p in enumerate(passes):
        # 找到 Pass 内最后一个 Draw/Dispatch
        result = find_last_draw_or_dispatch(p['action'])
        if not result:
            # 没有 Draw/Dispatch，跳过
            pass_outputs[idx] = set()
            continue
        
        draw_eid, is_dispatch = result
        last_eid = get_last_event_id(p['action'])
        
        controller.SetFrameEvent(draw_eid, False)
        pipe = controller.GetPipelineState()
        
        outputs = set()
        
        try:
            if is_dispatch:
                # Compute Shader: 获取 UAV 输出
                rw_resources = pipe.GetReadWriteResources(rd.ShaderStage.Compute, False)
                for rw in rw_resources:
                    try:
                        res_id = rw.descriptor.resource
                        if res_id and res_id != rd.ResourceId.Null():
                            outputs.add(str(res_id))
                    except:
                        pass
            else:
                # Graphics Pass: 获取 ColorTarget 和 DepthTarget
                for rt in pipe.GetOutputTargets():
                    res_id = rt.resource if hasattr(rt, 'resource') else getattr(rt, 'resourceId', None)
                    if res_id and res_id != rd.ResourceId.Null():
                        outputs.add(str(res_id))
                
                depth = pipe.GetDepthTarget()
                if depth:
                    res_id = depth.resource if hasattr(depth, 'resource') else getattr(depth, 'resourceId', None)
                    if res_id and res_id != rd.ResourceId.Null():
                        outputs.add(str(res_id))
        except:
            pass
        
        pass_outputs[idx] = outputs
        for rt_id in outputs:
            rt_producer[rt_id] = (idx, p['name'], draw_eid, last_eid)
        
        if (idx + 1) % 50 == 0:
            print(f"  已处理 {idx + 1}/{len(passes)}...")
    
    print(f"  发现 {len(rt_producer)} 个 RT")
    
    # 构建所有"读取"类型的 Usage
    READ_USAGES = set()
    for name in ['VS_Resource', 'HS_Resource', 'DS_Resource', 'GS_Resource', 'PS_Resource', 
                 'CS_Resource', 'MS_Resource', 'TS_Resource', 'All_Resource',
                 'VS_RWResource', 'HS_RWResource', 'DS_RWResource', 'GS_RWResource', 
                 'PS_RWResource', 'CS_RWResource', 'MS_RWResource', 'TS_RWResource', 'All_RWResource',
                 'InputTarget', 'CopySrc', 'ResolveSrc']:
        if hasattr(rd.ResourceUsage, name):
            READ_USAGES.add(getattr(rd.ResourceUsage, name))
    
    # 分析 RT 使用
    print("\n分析 RT 使用情况...")
    
    textures = controller.GetTextures()
    tex_info = {str(t.resourceId): {'w': t.width, 'h': t.height, 'fmt': t.format.Name() if hasattr(t.format, 'Name') else str(t.format)} for t in textures}
    
    rt_consumers = defaultdict(list)
    
    for rt_id in rt_producer.keys():
        for tex in textures:
            if str(tex.resourceId) == rt_id:
                try:
                    usage = controller.GetUsage(tex.resourceId)
                    prod_idx, prod_name, prod_draw_eid, prod_last_eid = rt_producer[rt_id]
                    
                    for u in usage:
                        is_read = u.usage in READ_USAGES
                        
                        # 只检查 Pass 结束后的读取
                        if is_read and u.eventId > prod_last_eid:
                            for i, pp in enumerate(passes):
                                if i <= prod_idx:
                                    continue
                                pp_last = get_last_event_id(pp['action'])
                                if pp['event_id'] <= u.eventId <= pp_last:
                                    rt_consumers[rt_id].append(pp['name'])
                                    break
                except:
                    pass
                break
    
    # 输出结果
    print("\n" + "=" * 70)
    print("                       分析结果")
    print("=" * 70)
    
    total = len(rt_producer)
    used = sum(1 for r in rt_producer if rt_consumers[r])
    
    print(f"\n  RT 总数:         {total}")
    print(f"  被使用的 RT:     {used}")
    print(f"  未使用的 RT:     {total - used}")
    if total > 0:
        print(f"  使用效率:        {used/total*100:.1f}%")
    
    # 每个 Pass 详情
    print(f"\n每个 Pass 的 RT 使用情况:")
    print("-" * 70)
    
    for idx, p in enumerate(passes):
        outputs = pass_outputs.get(idx, set())
        if not outputs:
            continue
        
        print(f"\n[{idx:3}] {p['name']}")
        for rt_id in outputs:
            info = tex_info.get(rt_id, {})
            size = f"{info.get('w', '?')}x{info.get('h', '?')}"
            consumers = list(set(rt_consumers.get(rt_id, [])))
            
            if consumers:
                c_str = ", ".join(consumers[:2])
                if len(consumers) > 2:
                    c_str += f" +{len(consumers)-2}"
                print(f"      OK  {size:<12} -> {c_str}")
            else:
                print(f"      XX  {size:<12} -> 未被使用!")
    
    # 死渲染
    dead = [(rt_producer[r][1], tex_info.get(r, {})) for r in rt_producer if not rt_consumers[r]]
    
    if dead:
        print(f"\n" + "=" * 70)
        print(f"              死渲染列表 ({len(dead)} 个)")
        print("=" * 70)
        for name, info in dead:
            print(f"  {name:<45} {info.get('w','?')}x{info.get('h','?')}")
    
    controller.Shutdown()
    cap.Shutdown()
    print("\n分析完成!")


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_pass_deps.py <rdc_file_path>")
        sys.exit(1)
    analyze_pass_deps(sys.argv[1])

if __name__ == "__main__":
    main()
