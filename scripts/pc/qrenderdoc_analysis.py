"""
RenderDoc Analysis Script (qrenderdoc.exe --python mode)
Usage: qrenderdoc.exe --python this_script.py capture.rdc
"""

import renderdoc as rd
import os
import sys
from datetime import datetime
from collections import defaultdict

# ============= Config =============
OUTPUT_FILE = r"C:\Users\zhangbin24\Downloads\renderdoc_analysis_result.txt"
MAX_ACTIONS = 500

# ============= Utils =============
def get_all_draw_actions(action, result=None):
    if result is None:
        result = []
    flags = int(action.flags)
    if flags & int(rd.ActionFlags.Drawcall):
        result.append(action)
    elif flags & int(rd.ActionFlags.Dispatch):
        result.append(action)
    for child in action.children:
        get_all_draw_actions(child, result)
    return result

def get_byte_size(comp_type, comp_count):
    type_sizes = {
        rd.CompType.Float: 4, rd.CompType.UInt: 4, rd.CompType.SInt: 4, rd.CompType.Double: 8,
    }
    return type_sizes.get(comp_type, 4) * comp_count

# ============= Analysis Functions =============
def analyze_shader_bindings(controller, actions, report):
    waste_count = 0
    waste_by_type = defaultdict(int)
    
    for action in actions[:MAX_ACTIONS]:
        controller.SetFrameEvent(action.eventId, False)
        try:
            state = controller.GetPipelineState()
            for stage in [rd.ShaderStage.Vertex, rd.ShaderStage.Pixel, rd.ShaderStage.Compute]:
                mapping = state.GetBindpointMapping(stage)
                if mapping is None:
                    continue
                for bind in mapping.readOnlyResources:
                    if not bind.used and bind.arraySize > 0:
                        waste_count += 1
                        waste_by_type['SRV'] += 1
                for bind in mapping.constantBlocks:
                    if not bind.used and bind.arraySize > 0:
                        waste_count += 1
                        waste_by_type['CBV'] += 1
        except:
            pass
    
    report.append("=" * 50)
    report.append("Shader Binding Waste Analysis")
    report.append("=" * 50)
    report.append(f"Total wasted bindings: {waste_count}")
    for t, count in waste_by_type.items():
        report.append(f"  {t}: {count}")
    report.append("")

def analyze_vertex_attributes(controller, actions, report):
    total_wasted_bytes = 0
    semantic_counts = defaultdict(int)
    waste_draw_count = 0
    
    for action in actions[:MAX_ACTIONS]:
        if action.numIndices == 0:
            continue
        controller.SetFrameEvent(action.eventId, False)
        try:
            state = controller.GetPipelineState()
            vs_refl = state.GetShaderReflection(rd.ShaderStage.Vertex)
            if vs_refl is None:
                continue
            has_waste = False
            for sig in vs_refl.inputSignature:
                if sig.channelUsedMask == 0:
                    has_waste = True
                    byte_size = get_byte_size(sig.compType, sig.compCount)
                    total_wasted_bytes += byte_size * action.numIndices
                    semantic_counts[f"{sig.semanticName}{sig.semanticIndex}"] += 1
            if has_waste:
                waste_draw_count += 1
        except:
            pass
    
    report.append("=" * 50)
    report.append("Vertex Attribute Waste Analysis")
    report.append("=" * 50)
    report.append(f"Draw calls with waste: {waste_draw_count}")
    report.append(f"Total wasted bandwidth: {total_wasted_bytes / 1024 / 1024:.2f} MB")
    report.append("Unused attributes ranking:")
    for sem, count in sorted(semantic_counts.items(), key=lambda x: -x[1])[:10]:
        report.append(f"  {sem}: {count} times")
    report.append("")

def analyze_overdraw(controller, actions, report):
    rt_draw_counts = defaultdict(list)
    
    for action in actions[:MAX_ACTIONS]:
        controller.SetFrameEvent(action.eventId, False)
        try:
            state = controller.GetPipelineState()
            for rt in state.GetOutputTargets():
                res_id = rt.resource if hasattr(rt, 'resource') else getattr(rt, 'resourceId', None)
                if res_id and res_id != rd.ResourceId.Null():
                    rt_draw_counts[str(res_id)].append(action.eventId)
        except:
            pass
    
    results = sorted([{'rt': rid, 'count': len(eids), 'range': f"{min(eids)}-{max(eids)}"} 
                      for rid, eids in rt_draw_counts.items()], key=lambda x: -x['count'])
    
    report.append("=" * 50)
    report.append("Overdraw Analysis (by RenderTarget)")
    report.append("=" * 50)
    for r in results[:10]:
        report.append(f"  RT {r['rt'][:25]}: {r['count']} draws (EID {r['range']})")
    report.append("")

def analyze_pass_deps(controller, root_actions, report):
    DRAWCALL_FLAG = int(rd.ActionFlags.Drawcall)
    DISPATCH_FLAG = int(rd.ActionFlags.Dispatch)
    
    passes = [{'name': a.customName or "", 'action': a} for a in root_actions 
              if (a.customName or "") and not (a.customName or "").startswith("=>")]
    
    def find_last_draw(action):
        last = None
        flags = int(action.flags)
        if flags & DRAWCALL_FLAG or flags & DISPATCH_FLAG:
            last = action.eventId
        for c in action.children:
            r = find_last_draw(c)
            if r: last = r
        return last
    
    rt_producer = {}
    for idx, p in enumerate(passes[:100]):
        eid = find_last_draw(p['action'])
        if not eid:
            continue
        controller.SetFrameEvent(eid, False)
        try:
            pipe = controller.GetPipelineState()
            for rt in pipe.GetOutputTargets():
                res_id = rt.resource if hasattr(rt, 'resource') else getattr(rt, 'resourceId', None)
                if res_id and res_id != rd.ResourceId.Null():
                    rt_producer[str(res_id)] = p['name']
        except:
            pass
    
    report.append("=" * 50)
    report.append("Pass Dependency Analysis")
    report.append("=" * 50)
    report.append(f"Passes analyzed: {len(passes[:100])}")
    report.append(f"RenderTargets detected: {len(rt_producer)}")
    report.append("")

# ============= Main =============
def run_analysis(ctx):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def callback(controller):
        print("=" * 60)
        print("RenderDoc Analysis")
        print("=" * 60)
        
        root_actions = controller.GetRootActions()
        all_draws = []
        for action in root_actions:
            get_all_draw_actions(action, all_draws)
        
        print(f"Total Draw/Dispatch: {len(all_draws)}")
        
        report = []
        report.append("=" * 60)
        report.append(f"RenderDoc Analysis Report - {timestamp}")
        report.append(f"Total Draw/Dispatch: {len(all_draws)}")
        report.append("=" * 60)
        report.append("")
        
        print("[1/4] Pass deps...")
        analyze_pass_deps(controller, root_actions, report)
        
        print("[2/4] Shader bindings...")
        analyze_shader_bindings(controller, all_draws, report)
        
        print("[3/4] Vertex attrs...")
        analyze_vertex_attributes(controller, all_draws, report)
        
        print("[4/4] Overdraw...")
        analyze_overdraw(controller, all_draws, report)
        
        report_text = "\n".join(report)
        print(report_text)
        
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\nResult saved: {OUTPUT_FILE}")
    
    ctx.Replay().BlockInvoke(callback)

# ============= Entry =============
try:
    run_analysis(pyrenderdoc)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()