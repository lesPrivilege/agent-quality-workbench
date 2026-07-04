"""Regulation staleness — contract definition only, no real scan yet.

This module defines the data contract for detecting expired-regulation
citations (roadmap.md gap 1, item 4 / "Mode 3"). It is NOT implemented
against real data this round: compliance-review-agent's regulation
library (data/regulations/*.md) has no IDs or effective dates, and its
audit_log only records a hit count ("found N regulations"), not which
regulation was matched. Building a real scan against that would only
produce fake data.

To enable this contract:
  1. compliance-review-agent's regulation library gets structured IDs
     and effective_until dates (currently plain prose .md files)
  2. Its audit_log entries carry a `regulation_refs` field:
     {"regulation_refs": [{"id": "反垄断合规", "effective_until": "2027-01-01"}]}

Both are out of scope for this repo (workbench only reads agent repos)
and are tracked as a separate follow-on in docs/roadmap.md.

Until then, scan_regulation_staleness() returns {"instrumented": False}
for any audit_log where no entry carries regulation_refs — this is a
deliberately distinct state from "instrumented and clean" (0 stale
citations found). Collapsing "not instrumented" into "0 findings" would
be a false green light, the same class of error as the M013 confidence
calibration case documented in README.md.
"""

from __future__ import annotations

from datetime import date


def scan_regulation_staleness(
    audit_log_entries: list[dict],
    id_field: str,
    today: date,
) -> dict:
    if not any("regulation_refs" in entry for entry in audit_log_entries):
        return {"instrumented": False}

    stale_refs = []
    for entry in audit_log_entries:
        refs = entry.get("regulation_refs")
        if not refs:
            continue
        case_id = str(entry.get(id_field, ""))
        for ref in refs:
            effective_until = date.fromisoformat(ref["effective_until"])
            if effective_until < today:
                stale_refs.append({
                    "case_id": case_id,
                    "regulation_id": ref["id"],
                    "effective_until": ref["effective_until"],
                })

    return {"instrumented": True, "stale_count": len(stale_refs), "stale_refs": stale_refs}
