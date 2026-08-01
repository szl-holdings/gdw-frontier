from __future__ import annotations

import pytest

from ci.worker_calc import calculate_workers


def test_worker_count_is_bounded_by_cpu_memory_and_maximum() -> None:
    plan = calculate_workers(
        cpu=8,
        memory_gib=4,
        reserve_cpu=1,
        reserve_memory_gib=1,
        cpu_per_worker=1,
        memory_gib_per_worker=1,
        maximum=10,
    )
    assert plan.cpu_limit == 7
    assert plan.memory_limit == 3
    assert plan.workers == 1
    assert plan.state_coordination == "process_local_single_worker"
    assert plan.status == "MODELED"


def test_worker_count_never_drops_below_one() -> None:
    plan = calculate_workers(cpu=1, memory_gib=1, reserve_cpu=1, reserve_memory_gib=1)
    assert plan.workers == 1


def test_worker_count_rejects_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        calculate_workers(cpu=0, memory_gib=4)
