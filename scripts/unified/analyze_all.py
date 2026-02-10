#!/usr/bin/env python3
"""
RenderDoc 综合分析调度器

用法: 
    python analyze_all.py <rdc_file_path> [选项]

选项:
    --output, -o <file>     输出报告到文件
    --software, -s          使用软件回放模式 (绕过 GPU 显存限制)
    --timeout <seconds>     设置超时时间 (默认 600 秒)
    --modules <mod1,mod2>   只运行指定的分析模块
    --list-modules          列出所有可用模块

特点:
    - 只执行 1 次 OpenCapture
    - 只遍历 1 次 DrawCall 树
    - 模块之间错误隔离，单个模块失败不影响其他模块
    - 支持选择性运行模块
"""

import sys
import os
import gc
import argparse
import threading
import traceback
from datetime import datetime

# 超时设置 (秒)
DEFAULT_TIMEOUT = 600  # 10分钟

# 内存优化
GC_INTERVAL = 300  # 每处理 N 个 DrawCall 执行一次 GC


class TimeoutError(Exception):
    """超时异常"""
    pass


class AnalysisScheduler:
    """分析调度器"""
    
    def __init__(self, rdc_path, use_software=False, timeout=DEFAULT_TIMEOUT):
        self.rdc_path = rdc_path
        self.use_software = use_software
        self.timeout = timeout
        self.rd = None
        self.cap = None
        self.controller = None
        self.timeout_timer = None
        self.timed_out = False
        
        # 分析结果
        self.results = {}
        self.errors = {}
    
    def start_timeout(self):
        """启动超时计时器"""
        def on_timeout():
            self.timed_out = True
            print("\n" + "!" * 60)
            print("  ⚠️  分析超时，正在尝试优雅退出...")
            print("!" * 60)
        
        self.timeout_timer = threading.Timer(self.timeout, on_timeout)
        self.timeout_timer.daemon = True
        self.timeout_timer.start()
    
    def cancel_timeout(self):
        """取消超时计时器"""
        if self.timeout_timer:
            self.timeout_timer.cancel()
    
    def init_renderdoc(self):
        """初始化 RenderDoc 模块"""
        # 尝试多个可能的路径
        possible_paths = [
            r"E:\code build\renderdoc-1.x\renderdoc-1.x\x64\Development\pymodules",
            r"C:\Program Files\RenderDoc\pymodules",
            os.environ.get('RENDERDOC_MODULE_PATH', ''),
        ]
        
        for path in possible_paths:
            if path and os.path.exists(path):
                if path not in sys.path:
                    sys.path.insert(0, path)
        
        try:
            import renderdoc as rd
            self.rd = rd
            return True
        except ImportError as e:
            print(f"错误: 无法导入 renderdoc 模块 - {e}")
            print("请确保 RenderDoc 已安装，或设置 RENDERDOC_MODULE_PATH 环境变量")
            return False
    
    def open_capture(self):
        """打开 RDC 文件"""
        rd = self.rd
        
        print(f"\n📂 正在打开: {self.rdc_path}")
        
        self.cap = rd.OpenCaptureFile()
        result = self.cap.OpenFile(self.rdc_path, '', None)
        
        if result != rd.ResultCode.Succeeded:
            print(f"错误: 无法打开文件 - {result}")
            return False
        
        # 设置回放选项
        opts = rd.ReplayOptions()
        if self.use_software:
            print("🖥️  使用软件回放模式 (CPU 渲染)")
            opts.forceGPUVendor = rd.GPUVendor.Software
        
        print("⏳ 正在创建回放控制器...")
        print("   (对于大文件，这可能需要几分钟)")
        
        result = self.cap.OpenCapture(opts, None)
        if isinstance(result, tuple):
            status, controller = result
            if status != rd.ResultCode.Succeeded:
                print(f"错误: 无法创建回放控制器 - {status}")
                self.cap.Shutdown()
                return False
            self.controller = controller
        else:
            self.controller = result
            if self.controller is None:
                print("错误: 无法创建回放控制器")
                self.cap.Shutdown()
                return False
        
        print("✅ 回放控制器创建成功!")
        return True
    
    def close_capture(self):
        """关闭捕获文件"""
        if self.controller:
            try:
                self.controller.Shutdown()
            except:
                pass
        if self.cap:
            try:
                self.cap.Shutdown()
            except:
                pass
    
    def create_analyzers(self, enabled_modules=None):
        """创建分析器实例"""
        from analyzers import ALL_ANALYZERS
        
        analyzers = []
        for mod_id, mod_name, AnalyzerClass, needs_iteration in ALL_ANALYZERS:
            if enabled_modules and mod_id not in enabled_modules:
                continue
            
            try:
                analyzer = AnalyzerClass(self.rd, self.controller)
                analyzers.append((mod_id, mod_name, analyzer, needs_iteration))
            except Exception as e:
                self.errors[mod_id] = f"初始化失败: {e}"
                print(f"  ⚠️ 模块 '{mod_name}' 初始化失败: {e}")
        
        return analyzers
    
    def run_static_analyzers(self, analyzers):
        """运行不需要遍历 DrawCall 的分析器"""
        print("\n" + "=" * 60)
        print("  📊 阶段 1: 静态资源分析")
        print("=" * 60)
        
        for mod_id, mod_name, analyzer, needs_iteration in analyzers:
            if needs_iteration:
                continue  # 跳过需要遍历的分析器
            
            if self.timed_out:
                self.errors[mod_id] = "超时跳过"
                continue
            
            print(f"  ▶ {mod_name}...", end=" ", flush=True)
            
            try:
                result = analyzer.analyze()
                self.results[mod_id] = {
                    'status': 'success',
                    'data': result,
                    'analyzer': analyzer,
                }
                print("✅")
            except Exception as e:
                self.errors[mod_id] = str(e)
                self.results[mod_id] = {
                    'status': 'error',
                    'error': str(e),
                }
                print(f"❌ ({e})")
    
    def run_iteration_analyzers(self, analyzers):
        """遍历 DrawCall 并运行需要遍历的分析器"""
        rd = self.rd
        controller = self.controller
        
        # 筛选需要遍历的分析器
        iter_analyzers = [(m, n, a) for m, n, a, needs in analyzers if needs]
        
        if not iter_analyzers:
            return
        
        print("\n" + "=" * 60)
        print("  🔍 阶段 2: DrawCall 遍历分析")
        print("=" * 60)
        print(f"     启用的模块: {', '.join(n for _, n, _ in iter_analyzers)}")
        
        processed = 0
        
        def process_action(action):
            nonlocal processed
            
            if self.timed_out:
                return
            
            flags = int(action.flags)
            is_draw = flags & int(rd.ActionFlags.Drawcall)
            is_dispatch = flags & int(rd.ActionFlags.Dispatch)
            
            # 只对 Draw/Dispatch 执行详细分析
            if is_draw or is_dispatch:
                processed += 1
                
                # 进度显示
                if processed % 500 == 0:
                    print(f"     已处理 {processed} 个 Draw/Dispatch...", flush=True)
                
                # 内存优化
                if processed % GC_INTERVAL == 0:
                    gc.collect()
                
                # 回放到这个状态
                try:
                    controller.SetFrameEvent(action.eventId, False)
                    pipe = controller.GetPipelineState()
                except Exception as e:
                    return  # 跳过这个 Draw
                
                # 调用每个分析器
                for mod_id, mod_name, analyzer in iter_analyzers:
                    if mod_id in self.errors:
                        continue  # 已经失败的模块跳过
                    
                    try:
                        analyzer.analyze_action(action, pipe)
                    except Exception as e:
                        # 记录错误但继续处理
                        if mod_id not in self.errors:
                            self.errors[mod_id] = f"analyze_action 失败: {e}"
            
            # 递归处理子 action
            for child in action.children:
                if self.timed_out:
                    return
                process_action(child)
        
        # 遍历所有 action
        for action in controller.GetRootActions():
            if self.timed_out:
                break
            process_action(action)
        
        print(f"     ✅ 共处理 {processed} 个 Draw/Dispatch")
        
        # 调用每个分析器的 finalize 和 analyze
        for mod_id, mod_name, analyzer in iter_analyzers:
            if mod_id in self.errors and 'analyze_action' in self.errors[mod_id]:
                self.results[mod_id] = {
                    'status': 'partial',
                    'error': self.errors[mod_id],
                }
                continue
            
            try:
                analyzer.finalize()
                result = analyzer.analyze()
                self.results[mod_id] = {
                    'status': 'success',
                    'data': result,
                    'analyzer': analyzer,
                }
            except Exception as e:
                self.errors[mod_id] = str(e)
                self.results[mod_id] = {
                    'status': 'error',
                    'error': str(e),
                }
    
    def generate_report(self):
        """生成综合报告"""
        lines = []
        
        lines.append("=" * 70)
        lines.append("              RenderDoc 综合分析报告")
        lines.append("=" * 70)
        lines.append(f"  文件: {self.rdc_path}")
        lines.append(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  模式: {'软件回放' if self.use_software else 'GPU 回放'}")
        lines.append("")
        
        # 汇总状态
        success_count = sum(1 for r in self.results.values() if r['status'] == 'success')
        error_count = len(self.errors)
        lines.append(f"  模块状态: {success_count} 成功, {error_count} 失败/部分失败")
        lines.append("")
        
        # 各模块报告
        for mod_id, result in self.results.items():
            if result['status'] == 'success' and 'analyzer' in result:
                try:
                    report = result['analyzer'].format_report()
                    lines.append(report)
                    lines.append("")
                except Exception as e:
                    lines.append(f"  [{mod_id}] 报告生成失败: {e}")
                    lines.append("")
            elif result['status'] == 'error':
                lines.append(f"  ❌ [{mod_id}] 执行失败: {result.get('error', '未知错误')}")
                lines.append("")
        
        # 错误汇总
        if self.errors:
            lines.append("=" * 70)
            lines.append("  ⚠️ 错误汇总")
            lines.append("=" * 70)
            for mod_id, error in self.errors.items():
                lines.append(f"    {mod_id}: {error}")
            lines.append("")
        
        lines.append("=" * 70)
        lines.append("  分析完成!")
        lines.append("=" * 70)
        
        return "\n".join(lines)
    
    def run(self, enabled_modules=None):
        """运行完整分析流程"""
        print("=" * 70)
        print("        RenderDoc 综合分析工具 v1.0")
        print("=" * 70)
        print(f"  ⏱️  超时设置: {self.timeout}秒 ({self.timeout//60}分钟)")
        
        self.start_timeout()
        
        try:
            # 初始化
            if not self.init_renderdoc():
                return None
            
            if not self.open_capture():
                return None
            
            # 创建分析器
            analyzers = self.create_analyzers(enabled_modules)
            
            if not analyzers:
                print("错误: 没有可用的分析模块")
                return None
            
            # 阶段 1: 静态分析
            self.run_static_analyzers(analyzers)
            
            # 阶段 2: 遍历分析
            if not self.timed_out:
                self.run_iteration_analyzers(analyzers)
            
            # 生成报告
            print("\n" + "=" * 60)
            print("  📝 生成分析报告...")
            print("=" * 60)
            
            report = self.generate_report()
            
            return report
            
        finally:
            self.cancel_timeout()
            self.close_capture()


def list_modules():
    """列出所有可用模块"""
    # 手动列出，避免导入错误
    modules = [
        ("basic_stats", "基础统计", "统计 Draw/Dispatch/Clear 等调用数量"),
        ("memory", "内存分析", "分析 GPU 内存占用，纹理和缓冲区统计"),
        ("vertex_attrs", "顶点属性浪费", "检测未使用的顶点属性"),
        ("shader_bindings", "Shader绑定浪费", "检测绑定但未使用的资源"),
        ("overdraw", "Overdraw估算", "估算屏幕 Overdraw 情况"),
    ]
    
    print("\n可用的分析模块:")
    print("-" * 60)
    for mod_id, mod_name, description in modules:
        print(f"  {mod_id:<18} {mod_name:<15} {description}")
    print("-" * 60)
    print("\n使用 --modules 选项指定要运行的模块，用逗号分隔")
    print("例如: --modules basic_stats,memory,vertex_attrs\n")


def main():
    parser = argparse.ArgumentParser(
        description='RenderDoc 综合分析工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python analyze_all.py capture.rdc
    python analyze_all.py capture.rdc -o report.txt
    python analyze_all.py capture.rdc --modules basic_stats,memory
    python analyze_all.py capture.rdc --software --timeout 300
        """
    )
    parser.add_argument('rdc_path', nargs='?', help='RDC 文件路径')
    parser.add_argument('--output', '-o', help='输出报告到文件')
    parser.add_argument('--software', '-s', action='store_true', 
                        help='使用软件回放模式')
    parser.add_argument('--timeout', '-t', type=int, default=DEFAULT_TIMEOUT,
                        help=f'超时时间 (秒, 默认 {DEFAULT_TIMEOUT})')
    parser.add_argument('--modules', '-m', 
                        help='只运行指定的模块 (逗号分隔)')
    parser.add_argument('--list-modules', '-l', action='store_true',
                        help='列出所有可用模块')
    
    args = parser.parse_args()
    
    if args.list_modules:
        list_modules()
        return
    
    if not args.rdc_path:
        parser.print_help()
        sys.exit(1)
    
    if not os.path.exists(args.rdc_path):
        print(f"错误: 文件不存在 - {args.rdc_path}")
        sys.exit(1)
    
    # 解析模块列表
    enabled_modules = None
    if args.modules:
        enabled_modules = [m.strip() for m in args.modules.split(',')]
    
    # 运行分析
    scheduler = AnalysisScheduler(
        args.rdc_path, 
        use_software=args.software,
        timeout=args.timeout
    )
    
    report = scheduler.run(enabled_modules)
    
    if report:
        print("\n" + report)
        
        if args.output:
            try:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(report)
                print(f"\n📄 报告已保存到: {args.output}")
            except Exception as e:
                print(f"\n⚠️ 保存报告失败: {e}")


if __name__ == "__main__":
    main()
