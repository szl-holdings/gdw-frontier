from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class WorkerPlan:
    workers: int
    cpu_limit: int
    memory_limit: int
    detected_cpu: float
    detected_memory_gib: float
    reserve_cpu: float
    reserve_memory_gib: float
    cpu_per_worker: float
    memory_gib_per_worker: float
    state_coordination: str = "process_local_single_worker"
    status: str = "MODELED"


def detected_memory_gib() -> float:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.total_physical / (1024**3)
    if Path("/proc/meminfo").exists():
        first = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()[0]
        return int(first.split()[1]) / (1024**2)
    pages = os.sysconf("SC_PHYS_PAGES")
    page_size = os.sysconf("SC_PAGE_SIZE")
    return pages * page_size / (1024**3)


def calculate_workers(
    *,
    cpu: float,
    memory_gib: float,
    reserve_cpu: float = 1.0,
    reserve_memory_gib: float = 1.0,
    cpu_per_worker: float = 1.0,
    memory_gib_per_worker: float = 0.5,
    maximum: int = 16,
) -> WorkerPlan:
    if min(cpu, memory_gib, cpu_per_worker, memory_gib_per_worker, maximum) <= 0:
        raise ValueError("capacity and per-worker values must be positive")
    usable_cpu = max(0.0, cpu - reserve_cpu)
    usable_memory = max(0.0, memory_gib - reserve_memory_gib)
    cpu_limit = max(1, math.floor(usable_cpu / cpu_per_worker))
    memory_limit = max(1, math.floor(usable_memory / memory_gib_per_worker))
    workers = 1
    return WorkerPlan(
        workers=workers,
        cpu_limit=cpu_limit,
        memory_limit=memory_limit,
        detected_cpu=round(cpu, 2),
        detected_memory_gib=round(memory_gib, 2),
        reserve_cpu=reserve_cpu,
        reserve_memory_gib=reserve_memory_gib,
        cpu_per_worker=cpu_per_worker,
        memory_gib_per_worker=memory_gib_per_worker,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report GDW capacity under the enforced single-worker state topology."
    )
    parser.add_argument("--cpu", type=float, default=float(os.cpu_count() or 1))
    parser.add_argument("--memory-gib", type=float, default=detected_memory_gib())
    parser.add_argument("--reserve-cpu", type=float, default=1.0)
    parser.add_argument("--reserve-memory-gib", type=float, default=1.0)
    parser.add_argument("--cpu-per-worker", type=float, default=1.0)
    parser.add_argument("--memory-gib-per-worker", type=float, default=0.5)
    parser.add_argument("--maximum", type=int, default=16)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = calculate_workers(
        cpu=args.cpu,
        memory_gib=args.memory_gib,
        reserve_cpu=args.reserve_cpu,
        reserve_memory_gib=args.reserve_memory_gib,
        cpu_per_worker=args.cpu_per_worker,
        memory_gib_per_worker=args.memory_gib_per_worker,
        maximum=args.maximum,
    )
    print(json.dumps(asdict(plan), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
