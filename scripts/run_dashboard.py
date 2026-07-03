#!/usr/bin/env python3
"""Run cross-agent quality dashboard."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.metrics import (
    generate_dashboard,
    load_agents,
    load_previous_history,
    load_thresholds,
    parse_agent_audit,
    save_history,
)

REPORTS_DIR = Path(__file__).parent.parent / "reports"


def main():
    REPORTS_DIR.mkdir(exist_ok=True)

    thresholds = load_thresholds()
    agents = load_agents()

    metrics_list = []
    for agent_cfg in agents:
        m = parse_agent_audit(agent_cfg)
        metrics_list.append(m)

    # Load previous history for trend arrows
    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")
    previous = load_previous_history(date_str)

    dashboard = generate_dashboard(metrics_list, thresholds, agents, previous)

    # Save current run to history
    save_history(metrics_list)

    date_str_file = datetime.now().strftime("%Y%m%d")
    out_path = REPORTS_DIR / f"dashboard_{date_str_file}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(dashboard)

    print(dashboard)
    print(f"\n仪表盘已写入: {out_path}")


if __name__ == "__main__":
    main()
