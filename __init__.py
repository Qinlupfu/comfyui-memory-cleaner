"""
ComfyUI Memory Cleaner — CPU WorkingSet Trim + GPU Cache Evict
"""

import gc
import os
import sys
import subprocess
import ctypes
import ctypes.wintypes
import psutil

try:
    import torch
    HAS_TORCH = True
except ImportError:
    torch = None
    HAS_TORCH = False


kernel32 = ctypes.windll.kernel32

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
OpenProcess.restype = ctypes.wintypes.HANDLE

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
CloseHandle.restype = ctypes.wintypes.BOOL

SetProcessWorkingSetSize = kernel32.SetProcessWorkingSetSize
SetProcessWorkingSetSize.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
SetProcessWorkingSetSize.restype = ctypes.wintypes.BOOL

PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_ACCESS = PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION


def trim_via_subprocess():
    pid = os.getpid()
    script = (
        "import ctypes;"
        "k32=ctypes.windll.kernel32;"
        f"h=k32.OpenProcess(0x0500,False,{pid});"
        "k32.SetProcessWorkingSetSize(h,ctypes.c_size_t(-1),ctypes.c_size_t(-1));"
        "k32.CloseHandle(h)"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", script],
            creationflags=0x08000000,
            timeout=10,
            capture_output=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def trim_subprocesses():
    try:
        current = psutil.Process()
        for child in current.children(recursive=True):
            _trim_external(child.pid)
    except Exception:
        pass


def _trim_external(pid: int) -> bool:
    try:
        h = OpenProcess(PROCESS_ACCESS, False, pid)
        if not h:
            return False
        ok = SetProcessWorkingSetSize(h, -1, -1)
        CloseHandle(h)
        return bool(ok)
    except Exception:
        return False


def get_memory_info():
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


class MemoryCleaner:
    MODES = ["cpu+gpu", "cpu_only", "gpu_only"]
    GC_OPTIONS = ["yes", "no"]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "run":        ("BOOLEAN", {"default": True,
                                           "label_on": "执行清理",
                                           "label_off": "待命",
                                           "tooltip": "切换此开关触发清理"}),
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

    def clean(self, run=True, anything=None, mode="cpu+gpu", gc_collect="yes"):
        if not run:
            return (anything, "⏸ 待命中，未执行清理")

        before = get_memory_info()

        if gc_collect == "yes":
            gc.collect(2)

        cpu_ok = False
        if mode in ("cpu+gpu", "cpu_only"):
            cpu_ok = trim_via_subprocess()
            trim_subprocesses()

        gpu_freed = []
        if mode in ("cpu+gpu", "gpu_only") and HAS_TORCH and torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                before_gpu = torch.cuda.memory_allocated(i)
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
                torch.cuda.synchronize(i)
                after_gpu = torch.cuda.memory_allocated(i)
                gpu_freed.append(round((before_gpu - after_gpu) / 1024 / 1024, 1))

        after = get_memory_info()
        freed_ram = round(before["rss_mb"] - after["rss_mb"], 1)

        lines = ["内存清理完成", "━━━━━━━━━━━━━━"]

        if mode in ("cpu+gpu", "cpu_only"):
            status = "✅" if cpu_ok else "❌"
            lines.append(f"CPU {status}: {before['rss_mb']}MB → {after['rss_mb']}MB  (释放 {freed_ram}MB)")
            if freed_ram < 0.1 and cpu_ok:
                lines.append("⚠️ 无更多可分页内存")

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


NODE_CLASS_MAPPINGS = {
    "MemoryCleaner": MemoryCleaner,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MemoryCleaner": "🧹 Memory Cleaner (CPU+GPU)",
}
