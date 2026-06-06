"""
ComfyUI Memory Cleaner — CPU 工作集清理 + GPU 显存清理
放到 ComfyUI/custom_nodes/comfyui-memory-cleaner/ 目录下即可
"""

import gc
import ctypes
import ctypes.wintypes
import psutil

# ── torch 可选（无 GPU 环境不报错） ────────────────────
try:
    import torch
    HAS_TORCH = True
except ImportError:
    torch = None
    HAS_TORCH = False


# ══════════════════════════════════════════════════════════
#  Win32 API — 模块级别一次性定义
# ══════════════════════════════════════════════════════════
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

# HANDLE GetCurrentProcess()  → 伪句柄，不用 CloseHandle
GetCurrentProcess = kernel32.GetCurrentProcess
GetCurrentProcess.restype = ctypes.wintypes.HANDLE

# HANDLE OpenProcess(DWORD dwDesiredAccess, BOOL bInheritHandle, DWORD dwProcessId)
OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
]
OpenProcess.restype = ctypes.wintypes.HANDLE

# BOOL CloseHandle(HANDLE hObject)
CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
CloseHandle.restype = ctypes.wintypes.BOOL

# ── 常量 ──────────────────────────────────────────────
PROCESS_SET_QUOTA       = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400


# ══════════════════════════════════════════════════════════
#  底层操作
# ══════════════════════════════════════════════════════════

def trim_self() -> bool:
    """对当前进程执行 EmptyWorkingSet（伪句柄，无需 OpenProcess）"""
    try:
        h = GetCurrentProcess()
        return bool(SetProcessWorkingSetSize(h, -1, -1))
    except Exception:
        return False


def trim_process(pid: int) -> bool:
    """对指定 PID 执行 EmptyWorkingSet（通过 OpenProcess 获取句柄）"""
    try:
        h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
        if not h:
            return False
        ok = SetProcessWorkingSetSize(h, -1, -1)
        CloseHandle(h)
        return bool(ok)
    except Exception:
        return False


def trim_all_comfyui():
    """清理整个 ComfyUI 进程树的 CPU 工作集"""
    current = psutil.Process()
    results = []

    # 主进程（伪句柄）
    ok = trim_self()
    results.append((current.pid, ok, "self"))

    # 所有子进程
    try:
        children = current.children(recursive=True)
        for child in children:
            ok = trim_process(child.pid)
            results.append((child.pid, ok, "child" if ok else "child (failed)"))
    except Exception:
        pass

    return results


# ══════════════════════════════════════════════════════════
#  内存信息采集
# ══════════════════════════════════════════════════════════

def get_memory_info():
    """获取 CPU + GPU 内存当前占用"""
    proc = psutil.Process()
    mem = proc.memory_info()
    info = {
        "rss_mb":  round(mem.rss / 1024 / 1024, 1),
        "vms_mb":  round(mem.vms / 1024 / 1024, 1),
        "gpu":     [],
    }

    if HAS_TORCH and torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            torch.cuda.synchronize(i)
            free, total = torch.cuda.mem_get_info(i)
            allocated = torch.cuda.memory_allocated(i)
            reserved  = torch.cuda.memory_reserved(i)
            info["gpu"].append({
                "device":   torch.cuda.get_device_name(i),
                "total_mb": round(total / 1024 / 1024, 1),
                "used_mb":  round(allocated / 1024 / 1024, 1),
                "reserved_mb": round(reserved / 1024 / 1024, 1),
                "free_mb":  round(free / 1024 / 1024, 1),
            })

    return info


# ══════════════════════════════════════════════════════════
#  ComfyUI 节点
# ══════════════════════════════════════════════════════════

class MemoryCleaner:
    """
    清内存节点

    CPU 模式 — 调用 EmptyWorkingSet (SetProcessWorkingSetSize) 把闲置
              物理内存页踢出 RAM，释放给系统。
    GPU 模式 — 调用 torch.cuda.empty_cache() + ipc_collect() 释放
              PyTorch 缓存的未使用显存块。
    CPU+GPU  —— 两件事都做。

    不影响模型加载/生成过程，只是把"站着坑不拉屎"的页清掉。
    """

    MODES = ["cpu+gpu", "cpu_only", "gpu_only"]
    GC_OPTIONS = ["yes", "no"]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode":       (cls.MODES, {"default": "cpu+gpu",
                                           "tooltip": "清理模式: CPU工作集 / GPU显存 / 两者"}),
                "gc_collect": (cls.GC_OPTIONS, {"default": "yes",
                                                "tooltip": "额外执行 Python gc.collect(2)"}),
            },
            "optional": {
                "anything":   ("*", {"tooltip": "可接任意节点，接入时原样透传"}),
            },
        }

    RETURN_TYPES = ("*", "STRING")
    RETURN_NAMES = ("pass_through", "report")
    OUTPUT_TOOLTIPS = ("原样输出输入", "内存变化报告")
    FUNCTION = "clean"
    CATEGORY = "utils/memory"

    def clean(self, anything=None, mode="cpu+gpu", gc_collect="yes"):
        before = get_memory_info()

        # ── 1. Python GC ──────────────────────────────────
        if gc_collect == "yes":
            gc.collect(2)

        # ── 2. CPU 工作集清理 ─────────────────────────────
        cpu_results = []
        if mode in ("cpu+gpu", "cpu_only"):
            cpu_results = trim_all_comfyui()

        # ── 3. GPU 显存清理 ───────────────────────────────
        gpu_freed = []
        if mode in ("cpu+gpu", "gpu_only") and HAS_TORCH and torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                before_gpu = torch.cuda.memory_allocated(i)
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
                torch.cuda.synchronize(i)
                after_gpu = torch.cuda.memory_allocated(i)
                gpu_freed.append(round((before_gpu - after_gpu) / 1024 / 1024, 1))

        # ── 4. 采集清理后的状态 ───────────────────────────
        after = get_memory_info()
        freed_ram = round(before["rss_mb"] - after["rss_mb"], 1)
        cpu_cleared = sum(1 for _, ok, _ in cpu_results if ok)

        # ── 5. 组装报告 ──────────────────────────────────
        lines = ["内存清理完成", "━━━━━━━━━━━━━━"]

        # CPU 部分
        if mode in ("cpu+gpu", "cpu_only"):
            lines.append(f"RSS  : {before['rss_mb']}MB → {after['rss_mb']}MB  (释放 {freed_ram}MB)")
            lines.append(f"进程 : {cpu_cleared}/{len(cpu_results or [])} 成功")

        # GPU 部分
        if mode in ("cpu+gpu", "gpu_only") and before["gpu"]:
            for idx, g in enumerate(before["gpu"]):
                gf = gpu_freed[idx] if idx < len(gpu_freed) else 0
                lines.append(
                    f"GPU{idx} [{g['device']}]: "
                    f"{g['used_mb']}MB → {after['gpu'][idx]['used_mb']}MB  "
                    f"(释放 {gf}MB, 总 {g['total_mb']}MB)"
                )

        report = "\n".join(lines)
        print(f"[MemoryCleaner] {report}")
        return (anything, report)


# ══════════════════════════════════════════════════════════
#  ComfyUI 节点注册
# ══════════════════════════════════════════════════════════

NODE_CLASS_MAPPINGS = {
    "MemoryCleaner": MemoryCleaner,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MemoryCleaner": "🧹 Memory Cleaner (CPU+GPU)",
}
