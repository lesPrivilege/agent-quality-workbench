#!/usr/bin/env python3
"""Run cross-agent quality dashboard."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.metrics import (
    generate_dashboard,
    load_agents,
    load_thresholds,
    parse_agent_audit,
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

    dashboard = generate_dashboard(metrics_list, thresholds, agents)

    date_str = datetime.now().strftime("%Y%m%d")
    out_path = REPORTS_DIR / f"dashboard_{date_str}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(dashboard)

    print(dashboard)
    print(f"\n仪表盘已写入: {out_path}")


if __name__ == "__main__":
    main()
