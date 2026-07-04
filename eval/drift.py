"""Metric drift detection — sliding-window comparison over reports/history.jsonl.

Only catches level-crossing drift (e.g. green -> yellow). A metric sliding
slowly within one color band (98% -> 96% -> 95.5%, always green) is a known
blind spot in v1 and is not detected until it actually crosses a boundary.
Acceptable for now — add slope detection only if real data shows in-band
drift is common; speculative to build before that's known.
"""

from __future__ import annotations

from eval.metric_registry import METRIC_REGISTRY
from eval.snapshot import threshold_status
from eval.threshold_resolution import resolve_threshold_cfg

_HISTORY_METRIC_KEYS = frozenset({
    "task_completion_rate",
    "accuracy_proxy",
    "hitl_trigger_rate",
    "guardrail_block_rate",
    "silent_failure_rate",
    "avg_latency_ms",
})

# metric key (as stored in history.jsonl) -> thresholds.yaml lookup key.
# These differ for avg_latency_ms (thresholds_key="cost_latency_proxy") —
# reuse METRIC_REGISTRY's mapping so this never drifts out of sync with it.
_THRESHOLDS_KEY_MAP = {
    entry.key: entry.thresholds_key
    for entry in METRIC_REGISTRY
    if entry.key in _HISTORY_METRIC_KEYS
}

_SEVERITY = {"green": 0, "yellow": 1, "red": 2}


def detect_metric_drift(
    history_entries: list[dict],
    agent_name: str,
    window_size: int,
    thresholds: dict,
    agent_cfg: dict,
) -> dict:
    agent_entries = [
        (e["date"], e["agents"][agent_name])
        for e in history_entries
        if agent_name in e.get("agents", {})
    ]
    k = len(agent_entries)
    required = 2 * window_size

    if k < required:
        return {
            "window_size": window_size,
            "insufficient_history": True,
            "history_count": k,
            "required_count": required,
            "events": [],
            "improvements": [],
        }

    old_slice = agent_entries[k - required : k - window_size]
    new_slice = agent_entries[k - window_size :]

    overrides = agent_cfg.get("thresholds_override", {})
    vertical_presets = agent_cfg.get("_profile", {}).get("threshold_presets", {})
    global_thresholds = thresholds["metrics"]

    events = []
    improvements = []
    for metric_key, thresholds_key in _THRESHOLDS_KEY_MAP.items():
        old_avg = sum(v[metric_key] for _, v in old_slice) / window_size
        new_avg = sum(v[metric_key] for _, v in new_slice) / window_size

        cfg = resolve_threshold_cfg(thresholds_key, global_thresholds, overrides, vertical_presets)
        old_status = threshold_status(old_avg, cfg)
        new_status = threshold_status(new_avg, cfg)

        if _SEVERITY[new_status] == _SEVERITY[old_status]:
            continue

        record = {
            "metric": metric_key,
            "old_status": old_status,
            "new_status": new_status,
            "old_avg": old_avg,
            "new_avg": new_avg,
            "old_window_span": {"from": old_slice[0][0], "to": old_slice[-1][0]},
            "new_window_span": {"from": new_slice[0][0], "to": new_slice[-1][0]},
        }
        if _SEVERITY[new_status] > _SEVERITY[old_status]:
            events.append(record)
        else:
            improvements.append(record)

    return {
        "window_size": window_size,
        "insufficient_history": False,
        "history_count": k,
        "required_count": required,
        "events": events,
        "improvements": improvements,
    }
