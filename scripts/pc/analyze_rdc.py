#!/usr/bin/env python3
"""
RenderDoc 捕获文件基础分析脚本

用法: python analyze_rdc.py <rdc_file_path>

功能:
- 统计 Drawcall 和 Dispatch 数量
- 查找最大纹理尺寸
- 统计资源类型分布
"""

import sys
import os

def analyze_rdc(rdc_path):
    """分析 RDC 文件并输出统计信息"""
    
    # 导入 renderdoc 模块
    try:
        import renderdoc as rd
    except ImportError:
        print("错误: 无法导入 renderdoc 模块")
        print("请确保已设置环境变量:")
        print("  set PATH=%PATH%;C:\\Program Files\\RenderDoc")
        print("  set PYTHONPATH=%PYTHONPATH%;C:\\Program Files\\RenderDoc\\pymodules")
        sys.exit(1)
    
    # 检查文件是否存在
    if not os.path.exists(rdc_path):
        print(f"错误: 文件不存在 - {rdc_path}")
        sys.exit(1)
    
    print(f"正在分析: {rdc_path}")
    print("=" * 60)
    
    # 打开捕获文件
    cap = rd.OpenCaptureFile()
    result = cap.OpenFile(rdc_path, '', None)
    
    if result != rd.ResultCode.Succeeded:
        print(f"错误: 无法打开文件 - {result}")
        sys.exit(1)
    
    # 打开回放控制器
    result = cap.OpenCapture(rd.ReplayOptions(), None)
    # OpenCapture 返回 (ResultCode, ReplayController) 元组
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
    drawcall_count = 0
    dispatch_count = 0
    clear_count = 0
    copy_count = 0
    marker_count = 0
    
    def count_actions(action):
        """递归统计 Action 类型"""
        nonlocal drawcall_count, dispatch_count, clear_count, copy_count, marker_count
        
        if action.flags & rd.ActionFlags.Drawcall:
            drawcall_count += 1
        if action.flags & rd.ActionFlags.Dispatch:
            dispatch_count += 1
        if action.flags & rd.ActionFlags.Clear:
            clear_count += 1
        if action.flags & rd.ActionFlags.Copy:
            copy_count += 1
        if action.flags & (rd.ActionFlags.PushMarker | rd.ActionFlags.SetMarker):
            marker_count += 1
        
        for child in action.children:
            count_actions(child)
    
    # 遍历所有 Action
    root_actions = controller.GetRootActions()
    for action in root_actions:
        count_actions(action)
    
    print("\n📊 Action 统计")
    print("-" * 40)
    print(f"  Drawcall 数量:  {drawcall_count}")
    print(f"  Dispatch 数量:  {dispatch_count}")
    print(f"  Clear 数量:     {clear_count}")
    print(f"  Copy 数量:      {copy_count}")
    print(f"  Marker 数量:    {marker_count}")
    
    # 分析纹理
    textures = controller.GetTextures()
    max_texture = None
    max_pixels = 0
    
    texture_stats = {
        'total': 0,
        'render_target': 0,
        'depth_target': 0,
        'shader_read': 0,
    }
    
    for tex in textures:
        texture_stats['total'] += 1
        
        pixels = tex.width * tex.height * max(tex.depth, 1)
        if pixels > max_pixels:
            max_pixels = pixels
            max_texture = tex
        
        # 统计纹理类型
        if hasattr(tex, 'creationFlags'):
            flags = tex.creationFlags
            if hasattr(rd, 'TextureCategory'):
                if flags & rd.TextureCategory.ColorTarget:
                    texture_stats['render_target'] += 1
                if flags & rd.TextureCategory.DepthTarget:
                    texture_stats['depth_target'] += 1
                if flags & rd.TextureCategory.ShaderRead:
                    texture_stats['shader_read'] += 1
    
    print("\n🖼️ 纹理统计")
    print("-" * 40)
    print(f"  纹理总数:       {texture_stats['total']}")
    print(f"  Render Target:  {texture_stats['render_target']}")
    print(f"  Depth Target:   {texture_stats['depth_target']}")
    print(f"  Shader Read:    {texture_stats['shader_read']}")
    
    if max_texture:
        print("\n📐 最大纹理")
        print("-" * 40)
        print(f"  尺寸: {max_texture.width} x {max_texture.height} x {max_texture.depth}")
        print(f"  Mips: {max_texture.mips}")
        print(f"  数组大小: {max_texture.arraysize}")
        print(f"  格式: {max_texture.format.Name() if hasattr(max_texture.format, 'Name') else max_texture.format}")
        print(f"  字节大小: {max_texture.byteSize:,} bytes")
    
    # 分析缓冲区
    buffers = controller.GetBuffers()
    max_buffer = None
    max_buffer_size = 0
    total_buffer_size = 0
    
    for buf in buffers:
        total_buffer_size += buf.length
        if buf.length > max_buffer_size:
            max_buffer_size = buf.length
            max_buffer = buf
    
    print("\n💾 缓冲区统计")
    print("-" * 40)
    print(f"  缓冲区总数:     {len(buffers)}")
    print(f"  总大小:         {total_buffer_size:,} bytes ({total_buffer_size / (1024*1024):.2f} MB)")
    
    if max_buffer:
        print(f"  最大缓冲区:     {max_buffer_size:,} bytes ({max_buffer_size / (1024*1024):.2f} MB)")
    
    # 获取帧信息
    frame_info = controller.GetFrameInfo()
    if frame_info:
        print("\n🎬 帧信息")
        print("-" * 40)
        print(f"  帧号:           {frame_info.frameNumber}")
        print(f"  文件大小(压缩): {frame_info.compressedFileSize:,} bytes")
        print(f"  文件大小(解压): {frame_info.uncompressedFileSize:,} bytes")
    
    # 清理
    controller.Shutdown()
    cap.Shutdown()
    
    print("\n" + "=" * 60)
    print("分析完成!")


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_rdc.py <rdc_file_path>")
        print("示例: python analyze_rdc.py C:\\captures\\frame.rdc")
        sys.exit(1)
    
    rdc_path = sys.argv[1]
    analyze_rdc(rdc_path)


if __name__ == "__main__":
    main()
