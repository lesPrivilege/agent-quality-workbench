"""Snapshot builder — compute layer for dashboard data.

Produces a JSON-serializable dict from AgentMetrics + config.
No emoji, no markdown — semantic values only. Rendering happens in render_markdown().
"""

from __future__ import annotations

from datetime import datetime

from eval.metric_registry import METRIC_REGISTRY, MetricEntry
from eval.metrics import AgentMetrics
from eval.threshold_resolution import resolve_threshold_cfg


def threshold_status(value: float, metric_cfg: dict) -> str:
    """Return semantic status: green | yellow | red."""
    g_lo, g_hi = metric_cfg["green"]
    y_lo, y_hi = metric_cfg["yellow"]
    if g_lo <= value <= g_hi:
        return "green"
    elif y_lo <= value <= y_hi:
        return "yellow"
    return "red"


def _trend_semantic(current: float, previous: float | None, entry: MetricEntry) -> str:
    """Return semantic trend: up | down | flat | none."""
    if previous is None:
        return "none"
    diff = current - previous
    if abs(diff) < 0.001:
        return "flat"
    if entry.lower_is_better:
        return "down" if diff < 0 else "up"
    return "up" if diff > 0 else "down"


def build_snapshot(
    metrics_list: list[AgentMetrics],
    thresholds: dict,
    agent_cfgs: list[dict] | None = None,
    previous: dict | None = None,
) -> dict:
    """Build a complete dashboard snapshot as a JSON-serializable dict.

    Iterates METRIC_REGISTRY to produce metric rows — no hardcoded metric list.

    Returns:
        dict with keys: date, agents (list of agent snapshots)
    """
    cfg_map = {}
    if agent_cfgs:
        for cfg in agent_cfgs:
            cfg_map[cfg["name"]] = cfg

    date_str = datetime.now().strftime("%Y-%m-%d")
    t = thresholds["metrics"]
    agents = []

    for m in metrics_list:
        overrides = {}
        vertical = None
        vertical_presets = {}
        if m.name in cfg_map:
            agent_cfg = cfg_map[m.name]
            overrides = agent_cfg.get("thresholds_override", {})
            vertical = agent_cfg.get("vertical")
            profile = agent_cfg.get("_profile", {})
            vertical_presets = profile.get("threshold_presets", {})

        prev_metrics = previous.get(m.name) if previous else None

        def _get_cfg(thresholds_key: str) -> dict:
            return resolve_threshold_cfg(thresholds_key, t, overrides, vertical_presets)

        def _threshold_source(thresholds_key: str) -> str:
            if thresholds_key in overrides:
                return "agent"
            if thresholds_key in vertical_presets:
                return "vertical"
            return "global"

        metrics = []
        for entry in METRIC_REGISTRY:
            # Error check (e.g. pytest failed)
            if entry.is_error(m):
                metric_dict = {
                    "key": entry.key,
                    "value": None,
                    "status": "error",
                    "trend": "none",
                    "dimension": entry.dimension,
                    "threshold_source": _threshold_source(entry.thresholds_key),
                    "note": entry.error_note(m),
                }
            else:
                value = entry.compute(m)
                cfg = _get_cfg(entry.thresholds_key)
                if entry.uncalibrated and value is None:
                    status = "uncalibrated"
                elif value is None:
                    status = "error"
                else:
                    status = threshold_status(value, cfg)

                prev_val = prev_metrics.get(entry.key) if prev_metrics else None
                trend = _trend_semantic(value or 0, prev_val, entry)

                metric_dict = {
                    "key": entry.key,
                    "value": value,
                    "status": status,
                    "trend": trend,
                    "dimension": entry.dimension,
                    "threshold_source": _threshold_source(entry.thresholds_key),
                }
                if entry.note:
                    metric_dict["note"] = entry.note(m)

            # Accuracy mismatch detail
            if entry.key == "accuracy_proxy":
                metric_dict["mismatched"] = m.details.get("routing_mismatched", [])

            # Latency extra field
            if entry.key == "avg_latency_ms":
                metric_dict["max_ms"] = m.max_latency_ms

            metrics.append(metric_dict)

        # Dimension summary (CLASSIC five dimensions)
        dim_worst: dict[str, str] = {}
        _status_priority = {"error": 0, "red": 1, "yellow": 2, "green": 3, "uncalibrated": 4}
        for m_dict in metrics:
            dim = m_dict.get("dimension", "accuracy")
            status = m_dict.get("status", "green")
            if dim not in dim_worst or _status_priority.get(status, 4) < _status_priority.get(dim_worst[dim], 4):
                dim_worst[dim] = status

        agents.append({
            "name": m.name,
            "vertical": vertical,
            "metrics": metrics,
            "dimensions": dim_worst,
            "unmapped_decisions": m.details.get("unmapped_decisions", []),
            "goldset": {
                "total": m.details.get("goldset_total", 0),
                "failure_modes": m.details.get("goldset_failure_modes", {}),
                "source": m.details.get("goldset_source", {}),
            },
            "meta": {
                "total_cases": m.total_cases,
                "total_audit_entries": m.total_audit_entries,
                "passed_tests": m.passed_tests,
                "total_tests": m.total_tests,
                "skipped_tests": m.skipped_tests,
            },
        })

    return {"date": date_str, "agents": agents}
