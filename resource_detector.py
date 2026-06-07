"""resource_detector.py — detect GPU/CPU/RAM and recommend a Whisper model."""

from __future__ import annotations
import os


def detect() -> dict:
    """Return hardware info dict: gpu_name, vram_gb, ram_gb, cpu_cores, has_cuda."""
    info = {
        "gpu_name": None,
        "vram_gb": 0,
        "ram_gb": _ram_gb(),
        "cpu_cores": os.cpu_count() or 1,
        "has_cuda": False,
    }

    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info["gpu_name"] = pynvml.nvmlDeviceGetName(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        info["vram_gb"] = mem.total / (1024 ** 3)
        info["has_cuda"] = True
        pynvml.nvmlShutdown()
    except Exception:
        pass

    return info


def cuda_available() -> bool:
    """True only if ctranslate2 can actually run on CUDA on this machine.

    Checks the inference engine itself (not just whether a GPU exists), so we catch
    missing CUDA/cuDNN libs or an unsupported GPU arch and can fall back to CPU.
    """
    try:
        import ctranslate2
        return bool(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        return False


def mem_used_mb(pid: int | None = None) -> int:
    """Resident memory (MiB) of a process — used for the CPU-mode status readout."""
    try:
        target = pid if pid is not None else os.getpid()
        with open(f"/proc/{target}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def vram_used_mb() -> int:
    """Currently-used VRAM on GPU 0 in MiB (0 if no GPU / pynvml unavailable)."""
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        pynvml.nvmlShutdown()
        return int(mem.used / (1024 ** 2))
    except Exception:
        return 0


def recommend(info: dict) -> tuple[str, str, str]:
    """Return (model, compute_type, device) based on hardware."""
    vram = info["vram_gb"]
    ram = info["ram_gb"]

    if info["has_cuda"] and vram > 0:
        device = "cuda"
        compute = "int8_float16"
        # Cap at 40% of available VRAM
        if vram >= 10:
            model = "large-v3-turbo"
        elif vram >= 5:
            model = "medium"
        elif vram >= 2:
            model = "small"
        else:
            model = "base"
    else:
        device = "cpu"
        compute = "int8"
        if ram >= 8:
            model = "small"
        elif ram >= 4:
            model = "base"
        else:
            model = "tiny"

    return model, compute, device


def _ram_gb() -> float:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    return kb / (1024 ** 2)
    except Exception:
        pass
    return 0.0
