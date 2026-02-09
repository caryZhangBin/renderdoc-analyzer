---
name: renderdoc-analyzer
description: |
  RenderDoc GPU 帧分析与优化工具集。用于分析 RDC 抓帧文件，检测渲染管线中的性能问题与资源浪费。
  
  触发场景:
  - 分析 RenderDoc 抓帧文件 (.rdc)
  - 检测 Shader 资源绑定浪费 (SRV/UAV/CBV 未使用)
  - 检测顶点属性浪费 (下发但未被 VS 使用的属性，如 TANGENT/BINORMAL)
  - 分析 Draw Call 带宽消耗
  - Android 设备远程分析 (通过 ADB + Remote Server)
  - GPU 过度绘制检测
  - Render Pass 依赖分析
  - 内存使用统计
---

# RenderDoc Analyzer

GPU 帧分析与渲染优化工具集，基于 RenderDoc Python API。

## 目录结构

```
renderdoc_tools/renderdoc-analyzer/scripts/
├── pc/                              # PC 本地分析脚本
│   ├── analyze_shader_bindings.py   # Shader 资源绑定浪费检测
│   ├── analyze_vertex_attributes.py # 顶点属性浪费检测
│   ├── analyze_overdraw.py          # 过度绘制分析
│   ├── analyze_pass_deps.py         # Pass 依赖分析
│   ├── analyze_memory.py            # 内存统计
│   ├── analyze_geometry.py          # 几何复杂度分析
│   ├── analyze_unused_resources.py  # 未使用资源检测
│   └── analyze_rdc.py               # 综合分析入口
└── android/                         # Android 远程分析脚本
    └── analyze_android_remote.py    # Android 设备远程分析
```

## 核心检测逻辑

### 1. Shader 资源绑定浪费检测

检测绑定了资源但 Shader 编译器标记为静态未使用的槽位。

**关键 API**:
```python
refl = controller.GetShaderReflection(stage)
mapping = controller.GetBindpointMapping(stage)
# 检查: mapping.readOnlyResources[i].used == False 且 binding.arraySize > 0
```

### 2. 顶点属性浪费检测

检测 Input Assembly 下发了数据，但 Vertex Shader 未读取的属性。

**关键 API**:
```python
refl = controller.GetShaderReflection(rd.ShaderStage.Vertex)
for sig in refl.inputSignature:
    if sig.channelUsedMask == 0:  # 属性未被使用
        # 计算浪费带宽: num_vertices * byte_size
```

**常见浪费**:
- `TANGENT` / `BINORMAL` - 切线空间数据未用于法线贴图
- `TEXCOORD6` / `TEXCOORD7` - 高索引纹理坐标
- `COLOR1` - 额外顶点颜色

### 3. Android 远程分析

通过 ADB 端口转发连接 Android 设备上的 `remoteserver`。

**工作流**:
1. 启动设备端: `adb shell am start -n org.renderdoc.renderdoccmd/.Loader -e renderdoccmd "remoteserver"`
2. 端口转发: `adb forward tcp:38920 tcp:38920`
3. Python 连接: `rd.CreateRemoteServerConnection("localhost", 38920)`

## 使用示例

### PC 本地分析
```bash
# 顶点属性浪费分析
python scripts/pc/analyze_vertex_attributes.py C:\captures\frame.rdc

# Shader 绑定浪费分析  
python scripts/pc/analyze_shader_bindings.py C:\captures\frame.rdc
```

### Android 远程分析
```bash
# 确保 Android 设备已启动 remoteserver
python scripts/android/analyze_android_remote.py /sdcard/captures/frame.rdc
```

## 输出指标

| 指标 | 说明 |
|------|------|
| Total Wasted Bandwidth | 未使用顶点属性的带宽总量 (MB) |
| Unused Binding Count | 绑定但未使用的资源槽位数 |
| Draw Calls with Waste | 存在浪费的 Draw Call 百分比 |

## RenderDoc 模块路径

脚本会自动搜索以下位置的 `renderdoc.pyd`:
- `E:\code build\renderdoc-1.x\renderdoc-1.x\x64\Development\pymodules`
- `C:\Program Files\RenderDoc\pymodules`
- 自定义 `RENDERDOC_MODULE_PATH` 环境变量

## 参考资料

- [RenderDoc Python API](https://renderdoc.org/docs/python_api/index.html)
- `SigParameter.channelUsedMask` - 4-bit mask 表示 xyzw 分量使用情况
- `Bindpoint.used` - 编译器静态分析的资源使用标记