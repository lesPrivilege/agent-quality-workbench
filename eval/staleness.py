"""Goldset case staleness — pure age-based proxy.

No regulation-change-date comparison: no such tracking data source exists
yet in this repo. If last_verified is missing entirely, the case is
treated as stale — absence of the field must not be read as freshness.
"""

from __future__ import annotations

from datetime import date


def compute_goldset_staleness(
    goldset_cases: list[dict], max_age_days: int, today: date
) -> dict:
    stale_cases = []
    for case in goldset_cases:
        last_verified = case.get("last_verified")
        if not last_verified:
            stale_cases.append({"id": case["id"], "reason": "missing_last_verified"})
            continue
        # last_verified is normally a quoted YAML string ("2026-07-04"), but an
        # unquoted date literal in goldset YAML auto-parses via PyYAML into a
        # real date object — accept both rather than crash on that easy mistake.
        verified_date = last_verified if isinstance(last_verified, date) else date.fromisoformat(last_verified)
        age_days = (today - verified_date).days
        if age_days > max_age_days:
            stale_cases.append({"id": case["id"], "reason": "age", "age_days": age_days})

    total_cases = len(goldset_cases)
    stale_count = len(stale_cases)
    return {
        "max_age_days": max_age_days,
        "method": "age_proxy",
        "total_cases": total_cases,
        "stale_count": stale_count,
        "stale_ratio": stale_count / total_cases if total_cases > 0 else 0.0,
        "stale_cases": stale_cases,
    }
