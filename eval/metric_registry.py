"""Metric registry — declarative metric definitions.

Each metric is a MetricEntry. The snapshot builder iterates the registry
to produce metric rows, so adding a new metric requires only:
  1. A pure compute function
  2. An entry in METRIC_REGISTRY

No changes to snapshot.py, render_markdown, or run_dashboard.py needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from eval.metrics import AgentMetrics

CASE_HISTORY_PATH = Path(__file__).parent.parent / "reports" / "case_history.jsonl"
STABILITY_K = 5


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


def _compute_route_stability(m: AgentMetrics) -> float | None:
    """Compute pass^k stability from case_history.jsonl.

    For each gold case, check if the last k runs all have actual == expected.
    Returns stable_case_count / total_gold_cases.
    """
    goldset = m.details.get("_goldset_ref")
    if not goldset:
        return None

    if not CASE_HISTORY_PATH.exists():
        return None

    # Load case history for this agent
    case_runs: dict[str, list[dict]] = {}
    with open(CASE_HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("agent") == m.name:
                cid = entry.get("case_id", "")
                case_runs.setdefault(cid, []).append(entry)

    stable_count = 0
    total = 0
    for cid, case in goldset.items():
        total += 1
        expected = case["expected_route"]
        runs = case_runs.get(cid, [])
        recent = runs[-STABILITY_K:]
        if len(recent) < 1:
            continue
        if all(r.get("actual_route") == expected for r in recent):
            stable_count += 1

    return stable_count / total if total > 0 else None


def _route_stability_note(m: AgentMetrics) -> str:
    goldset = m.details.get("_goldset_ref", {})
    if not goldset:
        return "无 goldset 数据"
    if not CASE_HISTORY_PATH.exists():
        return f"k={STABILITY_K}，窗口不足（首次运行）"
    # Count available history entries
    count = 0
    with open(CASE_HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("agent") == m.name:
                count += 1
    window = min(STABILITY_K, count // max(len(goldset), 1))
    return f"k={STABILITY_K}，窗口={window} 次运行；demo agent 恒为 100%，接入 LLM agent 后生效"


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
    MetricEntry(
        key="route_stability",
        label="路由稳定性",
        thresholds_key="route_stability",
        compute=_compute_route_stability,
        note=_route_stability_note,
    ),
]
