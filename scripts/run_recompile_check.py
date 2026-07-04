#!/usr/bin/env python3
"""Run recompile trigger check — goldset staleness + metric drift + regulation staleness."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.drift import detect_metric_drift
from eval.metrics import load_agents, load_thresholds
from eval.recompile_report import build_recompile_snapshot, render_recompile_markdown
from eval.regulation_staleness import scan_regulation_staleness
from eval.staleness import compute_goldset_staleness

REPORTS_DIR = Path(__file__).parent.parent / "reports"
HISTORY_PATH = REPORTS_DIR / "history.jsonl"


def _load_history_entries() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    entries = []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def _load_audit_log(agent_cfg: dict) -> list[dict]:
    audit_path = agent_cfg["repo"] / "data" / "audit_log.jsonl"
    if not audit_path.exists():
        return []
    entries = []
    with open(audit_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def main():
    today = date.today()
    thresholds = load_thresholds()
    agents = load_agents()
    history_entries = _load_history_entries()

    max_age_days = thresholds["staleness"]["goldset_case_max_age_days"]
    window_size = thresholds["drift"]["window_size"]

    agent_reports = []
    for agent_cfg in agents:
        goldset_cases = list(agent_cfg.get("_goldset", {}).values())
        staleness_result = compute_goldset_staleness(goldset_cases, max_age_days, today)

        drift_result = detect_metric_drift(
            history_entries, agent_cfg["name"], window_size, thresholds, agent_cfg
        )

        audit_entries = _load_audit_log(agent_cfg)
        id_field = agent_cfg.get("field_map", {}).get("case_id", "case_id")
        regulation_result = scan_regulation_staleness(audit_entries, id_field, today)

        agent_reports.append({
            "name": agent_cfg["name"],
            "goldset_staleness": staleness_result,
            "metric_drift": drift_result,
            "regulation_staleness": regulation_result,
        })

    snapshot = build_recompile_snapshot(agent_reports, today)
    md_output = render_recompile_markdown(snapshot)

    REPORTS_DIR.mkdir(exist_ok=True)
    date_str_file = datetime.now().strftime("%Y%m%d")
    json_path = REPORTS_DIR / f"recompile_triggers_{date_str_file}.json"
    md_path = REPORTS_DIR / f"recompile_triggers_{date_str_file}.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_output)

    print(md_output)
    print(f"\n重编译触发报告已写入: {md_path}")
    print(f"快照 JSON 已写入: {json_path}")


if __name__ == "__main__":
    main()
