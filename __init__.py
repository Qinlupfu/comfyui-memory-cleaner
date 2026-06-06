"""
ComfyUI Memory Cleaner — 跟 PCL 一样用 EmptyWorkingSet 清内存
放到 ComfyUI/custom_nodes/comfyui-memory-cleaner/ 目录下即可
"""

import gc
import ctypes
import ctypes.wintypes
import psutil

# ── Win32 API 定义 ────────────────────────────────────
kernel32 = ctypes.windll.kernel32

# HANDLE OpenProcess(DWORD dwDesiredAccess, BOOL bInheritHandle, DWORD dwProcessId)
OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
OpenProcess.restype = ctypes.wintypes.HANDLE

# BOOL EmptyWorkingSet(HANDLE hProcess)
EmptyWorkingSet = kernel32.EmptyWorkingSet
EmptyWorkingSet.argtypes = [ctypes.wintypes.HANDLE]
EmptyWorkingSet.restype = ctypes.wintypes.BOOL

# BOOL CloseHandle(HANDLE hObject)
CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
CloseHandle.restype = ctypes.wintypes.BOOL

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100


def trim_process(pid: int) -> bool:
    """对单个进程执行 EmptyWorkingSet"""
    try:
        h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
        if not h:
            return False
        result = EmptyWorkingSet(h)
        CloseHandle(h)
        return bool(result)
    except Exception:
        return False


def trim_all_comfyui():
    """清理整个 ComfyUI 进程树的工作集"""
    current = psutil.Process()
    results = []

    # 清理主进程
    ok = trim_process(current.pid)
    results.append((current.pid, ok))

    # 清理所有子进程（如果有 spawn 的子进程）
    try:
        children = current.children(recursive=True)
        for child in children:
            ok = trim_process(child.pid)
            results.append((child.pid, ok))
    except Exception:
        pass

    return results


def get_memory_info():
    """获取当前内存占用，用于节点输出展示"""
    proc = psutil.Process()
    mem = proc.memory_info()
    return {
        "rss_mb": round(mem.rss / 1024 / 1024, 1),
        "vms_mb": round(mem.vms / 1024 / 1024, 1),
    }


class MemoryCleaner:
    """
    PCL 同款清内存节点 — 调用 Windows EmptyWorkingSet API
    把 ComfyUI 闲置内存页踢出物理内存，释放给系统。

    不影响模型加载 / 生成过程，只是把"站着坑不拉屎"的页清掉。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "anything": ("*", {"tooltip": "接任意输入，触发清除"}),
                "gc_collect": (["yes", "no"], {"default": "yes", "tooltip": "额外执行 Python gc.collect()"}),
            },
        }

    RETURN_TYPES = ("*", "STRING")
    RETURN_NAMES = ("pass_through", "report")
    OUTPUT_TOOLTIPS = ("原样输出输入", "内存变化报告")
    FUNCTION = "clean"
    CATEGORY = "utils/memory"

    def clean(self, anything=None, gc_collect="yes"):
        before = get_memory_info()

        # 1. Python GC
        if gc_collect == "yes":
            gc.collect()

        # 2. EmptyWorkingSet — PCL 同款核心操作
        results = trim_all_comfyui()

        after = get_memory_info()
        freed = round(before["rss_mb"] - after["rss_mb"], 1)
        cleared = sum(1 for _, ok in results if ok)

        report = (
            f"内存清理完成\n"
            f"━━━━━━━━━━━━━━\n"
            f"RSS: {before['rss_mb']}MB → {after['rss_mb']}MB\n"
            f"释放: {freed}MB\n"
            f"进程清理: {cleared}/{len(results)} 成功"
        )

        print(f"[MemoryCleaner] {report}")
        return (anything, report)


# ComfyUI 节点注册
NODE_CLASS_MAPPINGS = {"MemoryCleaner": MemoryCleaner}
NODE_DISPLAY_NAME_MAPPINGS = {"MemoryCleaner": "🧹 Memory Cleaner"}
