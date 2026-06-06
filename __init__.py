"""
ComfyUI Memory Cleaner — 用 SetProcessWorkingSetSize 清内存
放到 ComfyUI/custom_nodes/comfyui-memory-cleaner/ 目录下即可
"""

import gc
import ctypes
import ctypes.wintypes
import psutil

# ── Win32 API ──────────────────────────────────────────
kernel32 = ctypes.windll.kernel32

# SIZE_T SetProcessWorkingSetSize(HANDLE, SIZE_T, SIZE_T)
# 传入 (hProcess, -1, -1) 即为 EmptyWorkingSet 效果
SetProcessWorkingSetSize = kernel32.SetProcessWorkingSetSize
SetProcessWorkingSetSize.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.c_size_t,
    ctypes.c_size_t,
]
SetProcessWorkingSetSize.restype = ctypes.wintypes.BOOL

# HANDLE GetCurrentProcess()
GetCurrentProcess = kernel32.GetCurrentProcess
GetCurrentProcess.restype = ctypes.wintypes.HANDLE


def trim_self() -> bool:
    """对当前进程执行 EmptyWorkingSet（直接用伪句柄，不需要 OpenProcess）"""
    try:
        h = GetCurrentProcess()          # 伪句柄，不用 CloseHandle
        return bool(SetProcessWorkingSetSize(h, -1, -1))
    except Exception:
        return False


def trim_process(pid: int) -> bool:
    """对指定 PID 进程执行 EmptyWorkingSet（用 OpenProcess）"""
    try:
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_QUERY_INFORMATION = 0x0400
        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        OpenProcess.restype = ctypes.wintypes.HANDLE
        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        CloseHandle.restype = ctypes.wintypes.BOOL

        h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
        if not h:
            return False
        ok = SetProcessWorkingSetSize(h, -1, -1)
        CloseHandle(h)
        return bool(ok)
    except Exception:
        return False


def trim_all_comfyui():
    """清理整个 ComfyUI 进程树的工作集"""
    current = psutil.Process()
    results = []

    # 清理主进程（用伪句柄）
    ok = trim_self()
    results.append((current.pid, ok, "self"))

    # 清理所有子进程
    try:
        children = current.children(recursive=True)
        for child in children:
            ok = trim_process(child.pid)
            results.append((child.pid, ok, "child"))
    except Exception:
        pass

    return results


def get_memory_info():
    """获取当前内存占用"""
    proc = psutil.Process()
    mem = proc.memory_info()
    return {
        "rss_mb": round(mem.rss / 1024 / 1024, 1),
        "vms_mb": round(mem.vms / 1024 / 1024, 1),
    }


class MemoryCleaner:
    """
    清内存节点 — 调用 Windows SetProcessWorkingSetSize(h, -1, -1)
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

        # 2. EmptyWorkingSet
        results = trim_all_comfyui()

        after = get_memory_info()
        freed = round(before["rss_mb"] - after["rss_mb"], 1)
        cleared = sum(1 for _, ok, _ in results if ok)

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
