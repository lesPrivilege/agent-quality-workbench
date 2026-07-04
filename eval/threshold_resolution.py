"""Threshold resolution — agent override > vertical preset > global priority chain.

Shared by snapshot.py (current dashboard state) and drift.py (historical
window comparison) so the two never disagree about what counts as
green/yellow/red for a given agent + metric.
"""

from __future__ import annotations

DEFAULT_THRESHOLDS = {"green": [0.0, 1.0], "yellow": [0.0, 1.0], "red": [0.0, 1.0]}


def resolve_threshold_cfg(
    thresholds_key: str,
    global_thresholds: dict,
    agent_overrides: dict,
    vertical_presets: dict,
) -> dict:
    """Priority: agent override > vertical preset > global."""
    if thresholds_key in agent_overrides:
        return agent_overrides[thresholds_key]
    if thresholds_key in vertical_presets:
        return vertical_presets[thresholds_key]
    return global_thresholds.get(thresholds_key, DEFAULT_THRESHOLDS)
