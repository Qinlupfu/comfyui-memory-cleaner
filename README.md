# 🧹 ComfyUI Memory Cleaner

一键清理 ComfyUI 内存，调用 Windows `EmptyWorkingSet` API 把闲置内存页踢出物理内存。

## 安装

把本文件夹复制到 ComfyUI 的 `custom_nodes` 目录：

```
ComfyUI/custom_nodes/comfyui-memory-cleaner/
```

重启 ComfyUI。

## 使用

节点名称：**🧹 Memory Cleaner**（分类 `utils/memory`）

- **anything** — 接任意输入触发清理
- **gc_collect** — `yes` 额外执行 Python GC，双管齐下

输出原样吐出输入，同时打印内存变化报告。

## 原理

1. `gc.collect()` — 清理 Python 无用对象
2. `EmptyWorkingSet()` — Windows API，把闲置内存页移出物理内存

纯软件操作，对硬件零损害。
