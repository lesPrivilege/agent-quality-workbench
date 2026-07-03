"""Build static site — inject latest data snapshots into docs/index.html.

Reads:
  - reports/dashboard_<latest>.json  (quality metrics snapshot)
  - reports/history.jsonl            (trend data)
  - reports/complexity_scores_<latest>.json  (scorer results)

Writes:
  - docs/index.html  (with embedded <script id="snapshot"> block)
"""

import json
import re
import sys
from pathlib import Path

REPORTS_DIR = Path(__file__).parent.parent / "reports"
DOCS_DIR = Path(__file__).parent.parent / "docs"
HTML_PATH = DOCS_DIR / "index.html"


def _latest_file(pattern: str) -> Path | None:
    """Find the most recent file matching a glob pattern."""
    files = sorted(REPORTS_DIR.glob(pattern), reverse=True)
    return files[0] if files else None


def _load_json(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_history() -> list[dict]:
    path = REPORTS_DIR / "history.jsonl"
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def build_snapshot() -> dict:
    """Merge all data sources into a single snapshot object."""
    snapshot = {}

    # Dashboard metrics
    dash_file = _latest_file("dashboard_*.json")
    if dash_file:
        snapshot["dashboard"] = _load_json(dash_file)

    # Complexity scores
    scores_file = _latest_file("complexity_scores_*.json")
    if scores_file:
        snapshot["scores"] = _load_json(scores_file)

    # History (last 10 entries for trend)
    snapshot["history"] = _load_history()[-10:]

    # Summary stats for hero
    dashboard = snapshot.get("dashboard", {})
    agents = dashboard.get("agents", [])
    total_sf = sum(
        m["value"]
        for a in agents
        for m in a.get("metrics", [])
        if m["key"] == "silent_failure_rate" and m.get("value") is not None
    )
    snapshot["hero"] = {
        "dimensions": 6,
        "silent_failures": int(total_sf),
        "agent_count": len(agents),
        "date": dashboard.get("date", ""),
    }

    return snapshot


def inject_snapshot(html: str, snapshot: dict) -> str:
    """Replace or insert <script type="application/json" id="snapshot"> block."""
    json_str = json.dumps(snapshot, ensure_ascii=False, indent=2)
    script_block = f'<script type="application/json" id="snapshot">\n{json_str}\n</script>'

    pattern = r'<script type="application/json" id="snapshot">.*?</script>'
    if re.search(pattern, html, re.DOTALL):
        return re.sub(pattern, script_block, html, flags=re.DOTALL)
    else:
        # Insert before </body>
        return html.replace("</body>", f"{script_block}\n</body>")


def main():
    snapshot = build_snapshot()

    if not HTML_PATH.exists():
        print(f"错误：{HTML_PATH} 不存在", file=sys.stderr)
        sys.exit(1)

    html = HTML_PATH.read_text(encoding="utf-8")
    html = inject_snapshot(html, snapshot)
    HTML_PATH.write_text(html, encoding="utf-8")

    dash_date = snapshot.get("dashboard", {}).get("date", "N/A")
    agent_count = snapshot.get("hero", {}).get("agent_count", 0)
    print(f"站点已更新: {HTML_PATH}")
    print(f"  数据日期: {dash_date}")
    print(f"  Agent 数: {agent_count}")


if __name__ == "__main__":
    main()
