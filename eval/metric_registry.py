"""Metric registry — declarative metric definitions.

Each metric is a MetricEntry. The snapshot builder iterates the registry
to produce metric rows, so adding a new metric requires only:
  1. A pure compute function
  2. An entry in METRIC_REGISTRY

No changes to snapshot.py, render_markdown, or run_dashboard.py needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from eval.metrics import AgentMetrics


@dataclass(frozen=True)
class MetricEntry:
    key: str
    label: str
    thresholds_key: str
    compute: Callable[[AgentMetrics], float | None]
    uncalibrated: bool = False
    note: Callable[[AgentMetrics], str] | None = None
    is_error: Callable[[AgentMetrics], bool] = lambda m: False
    error_note: Callable[[AgentMetrics], str] = lambda m: ""
    lower_is_better: bool = False


def _task_completion(m: AgentMetrics) -> float | None:
    if m.pytest_error:
        return None
    return m.task_completion_rate


def _task_completion_note(m: AgentMetrics) -> str:
    if m.pytest_error:
        return f"pytest 未能执行: {m.pytest_error}"
    note = f"{m.passed_tests}/{m.total_tests} passed"
    if m.skipped_tests > 0:
        note += f"（{m.skipped_tests} 个因无 key 跳过）"
    return note


METRIC_REGISTRY: list[MetricEntry] = [
    MetricEntry(
        key="task_completion_rate",
        label="任务完成率",
        thresholds_key="task_completion_rate",
        compute=_task_completion,
        note=_task_completion_note,
        is_error=lambda m: m.pytest_error is not None,
        error_note=lambda m: f"pytest 未能执行: {m.pytest_error}",
    ),
    MetricEntry(
        key="accuracy_proxy",
        label="准确率代理",
        thresholds_key="accuracy_proxy",
        compute=lambda m: m.accuracy_proxy,
        note=lambda m: "路由准确率（期望 vs 实际）",
    ),
    MetricEntry(
        key="hitl_trigger_rate",
        label="HITL 触发率",
        thresholds_key="hitl_trigger_rate",
        compute=lambda m: m.hitl_trigger_rate,
        note=lambda m: f"{m.hitl_count} 次人工介入",
        lower_is_better=True,
    ),
    MetricEntry(
        key="guardrail_block_rate",
        label="Guardrail 拦截率",
        thresholds_key="guardrail_block_rate",
        compute=lambda m: m.guardrail_block_rate,
        note=lambda m: f"{m.blocked_count} 次阻断",
        lower_is_better=True,
    ),
    MetricEntry(
        key="silent_failure_rate",
        label="Silent Failure",
        thresholds_key="silent_failure_rate",
        compute=lambda m: m.silent_failure_rate,
        note=lambda m: m.details.get("silent_failure_note", "未扫描"),
        lower_is_better=True,
    ),
    MetricEntry(
        key="avg_latency_ms",
        label="平均延迟",
        thresholds_key="cost_latency_proxy",
        compute=lambda m: m.avg_latency_ms,
        uncalibrated=True,
        note=lambda m: "未校准：demo 数据，生产环境需替换为 token 计数",
        lower_is_better=True,
    ),
]
