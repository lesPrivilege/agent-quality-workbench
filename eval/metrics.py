"""Cross-agent quality metrics — parse audit logs and test results."""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

THRESHOLDS_PATH = Path(__file__).parent / "thresholds.yaml"
AGENTS_PATH = Path(__file__).parent / "agents.yaml"
VERTICALS_DIR = Path(__file__).parent.parent / "verticals"
HISTORY_PATH = Path(__file__).parent.parent / "reports" / "history.jsonl"
CASE_HISTORY_PATH = Path(__file__).parent.parent / "reports" / "case_history.jsonl"

VALID_FAILURE_MODES = frozenset({
    "routing_error",
    "guardrail_gap",
    "over_escalation",
    "silent_failure",
    "calibration",
})


@dataclass
class AgentMetrics:
    name: str
    task_completion_rate: float = 0.0
    accuracy_proxy: float = 0.0
    hitl_trigger_rate: float = 0.0
    guardrail_block_rate: float = 0.0
    silent_failure_rate: float = 0.0
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0
    total_cases: int = 0
    total_audit_entries: int = 0
    hitl_count: int = 0
    blocked_count: int = 0
    silent_failure_count: int = 0
    silent_failure_scanned: bool = False
    pytest_error: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class AuditEvent:
    """Canonical audit event — normalized from raw audit log entries."""
    case_id: str
    route: str        # auto | hitl | block | error
    duration_ms: float
    raw: dict         # original entry, used by risk_rules etc.


def load_thresholds() -> dict:
    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_agents() -> list[dict]:
    """Load agent registrations from agents.yaml, resolving vertical templates and goldsets."""
    with open(AGENTS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    agents = data.get("agents", [])
    for a in agents:
        a["repo"] = Path(a["repo"]).expanduser()
        # Load goldset if specified
        goldset_path = a.get("goldset")
        if goldset_path:
            gs_path = Path(goldset_path)
            if not gs_path.is_absolute():
                gs_path = Path(__file__).parent.parent / gs_path
            if gs_path.exists():
                with open(gs_path, encoding="utf-8") as f:
                    gs = yaml.safe_load(f)
                cases = gs.get("cases", [])
                # Validate failure_mode enum
                for c in cases:
                    fm = c.get("failure_mode", "")
                    if fm not in VALID_FAILURE_MODES:
                        raise ValueError(
                            f"goldset {gs_path}: case {c.get('id')} 有非法 failure_mode '{fm}'，"
                            f"合法值: {sorted(VALID_FAILURE_MODES)}"
                        )
                a["_goldset"] = {c["id"]: c for c in cases}
                # Also populate expected_routes from goldset for backward compat
                a["expected_routes"] = {c["id"]: c["expected_route"] for c in cases}
        # Load vertical profile if specified
        vertical_name = a.get("vertical")
        if vertical_name:
            profile_path = VERTICALS_DIR / vertical_name / "profile.yaml"
            if profile_path.exists():
                with open(profile_path, encoding="utf-8") as f:
                    a["_profile"] = yaml.safe_load(f)
                _resolve_template_refs(a)
    return agents


def _resolve_template_refs(agent_cfg: dict) -> None:
    """Resolve {template: name} references in risk_rules using vertical profile."""
    profile = agent_cfg.get("_profile", {})
    templates = profile.get("risk_rule_templates", {})
    if not templates:
        return
    rules = agent_cfg.get("risk_rules", [])
    resolved = []
    for rule in rules:
        if "template" in rule:
            tpl_name = rule["template"]
            if tpl_name in templates:
                resolved.append(templates[tpl_name])
            else:
                # Keep as-is with warning note
                resolved.append(rule)
        else:
            resolved.append(rule)
    agent_cfg["risk_rules"] = resolved


def load_vertical_profile(name: str) -> dict | None:
    """Load a vertical profile by name."""
    profile_path = VERTICALS_DIR / name / "profile.yaml"
    if not profile_path.exists():
        return None
    with open(profile_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_nested(data: dict, path: str):
    """Get a nested field value using dot notation (e.g. '条款标记.担保')."""
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def evaluate_rule(rule: dict, record: dict) -> bool:
    """Evaluate a single risk rule against a data record.

    Supported operators: equals, contains, gt, lt.
    Combinators: all (all must match), any (at least one must match).
    """
    if "all" in rule:
        return all(evaluate_rule(sub, record) for sub in rule["all"])
    if "any" in rule:
        return any(evaluate_rule(sub, record) for sub in rule["any"])

    path = rule.get("path", "")
    value = _get_nested(record, path)

    if "equals" in rule:
        return value == rule["equals"]
    if "contains" in rule:
        return isinstance(value, str) and rule["contains"] in value
    if "gt" in rule:
        return isinstance(value, (int, float)) and value > rule["gt"]
    if "lt" in rule:
        return isinstance(value, (int, float)) and value < rule["lt"]

    return False


def normalize_events(entries: list[dict], agent_cfg: dict) -> tuple[list[AuditEvent], list[str]]:
    """Normalize raw audit log entries into canonical AuditEvents.

    Uses field_map and route_map from agent_cfg to map raw fields/values
    to the canonical schema. Returns (events, unmapped_decisions).

    unmapped_decisions lists any raw decision values not covered by route_map —
    these indicate config drift when integrating a new agent.
    """
    field_map = agent_cfg.get("field_map", {})
    route_map = agent_cfg.get("route_map", {})
    id_field = field_map.get("case_id", agent_cfg.get("id_field", "id"))
    duration_field = field_map.get("duration_ms", "duration_ms")

    events = []
    unmapped = set()

    for entry in entries:
        case_id = str(entry.get(id_field, ""))
        raw_decision = str(entry.get("decision", ""))
        duration_ms = float(entry.get(duration_field, 0) or 0)

        route = route_map.get(raw_decision)
        if route is None:
            route = "error"
            if raw_decision:
                unmapped.add(raw_decision)

        events.append(AuditEvent(
            case_id=case_id,
            route=route,
            duration_ms=duration_ms,
            raw=entry,
        ))

    return events, sorted(unmapped)


def save_history(metrics_list: list[AgentMetrics]) -> None:
    """Append a history record to reports/history.jsonl."""
    from datetime import datetime

    date_str = datetime.now().strftime("%Y-%m-%d")
    record = {"date": date_str, "agents": {}}
    for m in metrics_list:
        record["agents"][m.name] = {
            "task_completion_rate": m.task_completion_rate,
            "accuracy_proxy": m.accuracy_proxy,
            "hitl_trigger_rate": m.hitl_trigger_rate,
            "guardrail_block_rate": m.guardrail_block_rate,
            "silent_failure_rate": m.silent_failure_rate,
            "avg_latency_ms": m.avg_latency_ms,
        }
    HISTORY_PATH.parent.mkdir(exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        import json
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_previous_history(current_date: str) -> dict | None:
    """Load the most recent history entry before the current run.

    Called before save_history, so the last entry in the file is from the
    previous run. Returns dict of {agent_name: {metric: value}} or None.
    """
    if not HISTORY_PATH.exists():
        return None

    import json
    entries = []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entries.append(json.loads(line))

    if not entries:
        return None

    # The last entry is from the previous run (current run not yet saved)
    return entries[-1]["agents"]


def _trend_arrow(current: float, previous: float | None, metric_key: str) -> str:
    """Return trend arrow for a metric compared to previous value.

    For 'lower is better' metrics (silent_failure_rate, hitl_trigger_rate, etc.),
    ↓ is good and ↑ is bad. For 'higher is better' metrics (task_completion_rate,
    accuracy_proxy), ↑ is good and ↓ is bad.

    Returns: ↑, ↓, →, or - (no previous data).
    """
    if previous is None:
        return "-"
    diff = current - previous
    if abs(diff) < 0.001:
        return "→"
    # Metrics where lower is better
    lower_is_better = {"hitl_trigger_rate", "guardrail_block_rate", "silent_failure_rate", "avg_latency_ms"}
    if metric_key in lower_is_better:
        return "↓" if diff < 0 else "↑"
    else:
        return "↑" if diff > 0 else "↓"


def _threshold_label(value: float, metric_cfg: dict) -> str:
    """Return green/yellow/red for a value given metric config.

    Boundary convention: all ranges are inclusive on both ends (lo <= x <= hi).
    """
    g_lo, g_hi = metric_cfg["green"]
    y_lo, y_hi = metric_cfg["yellow"]
    r_lo, r_hi = metric_cfg["red"]
    if g_lo <= value <= g_hi:
        return "🟢"
    elif y_lo <= value <= y_hi:
        return "🟡"
    else:
        return "🔴"


def _run_pytest(repo_path: Path) -> tuple[int, int, int] | None:
    """Run pytest in the given repo and return (passed, failed, skipped).

    Parses the pytest summary line like '43 passed, 7 skipped'.
    Returns None if pytest could not be executed (e.g. uv missing, repo not found).
    """
    if not repo_path.exists():
        return None
    try:
        result = subprocess.run(
            ["uv", "run", "pytest", "tests/", "-v", "--tb=no", "-q"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
    except FileNotFoundError:
        return None
    except Exception:
        return None

    passed = failed = skipped = 0
    # Match patterns like "43 passed", "7 skipped", "2 failed"
    m = re.search(r"(\d+) passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+) skipped", output)
    if m:
        skipped = int(m.group(1))

    return (passed, failed, skipped)


def _load_source_data(repo_path: Path, data_file: str) -> list[dict]:
    """Load a JSONL data file (contracts.jsonl or materials.jsonl)."""
    path = repo_path / "data" / data_file
    items = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
    return items


def parse_agent_audit(agent_cfg: dict) -> AgentMetrics:
    """Parse an agent's audit logs and test results from config.

    Unified replacement for parse_contract_audit / parse_compliance_audit.
    All agent-specific differences come from agent_cfg (loaded from agents.yaml).
    """
    name = agent_cfg["name"]
    repo_path = agent_cfg["repo"]
    source_data_file = agent_cfg.get("source_data", "")
    expected_routes = agent_cfg.get("expected_routes", {})
    risk_rules = agent_cfg.get("risk_rules", [])

    m = AgentMetrics(name=name)
    m.details["_agent_cfg_ref"] = agent_cfg

    # --- Pytest ---
    pytest_result = _run_pytest(repo_path)
    if pytest_result is None:
        if not repo_path.exists():
            m.pytest_error = f"仓库路径不存在: {repo_path}"
        else:
            m.pytest_error = "uv 未安装或执行失败"
    else:
        passed, failed, skipped = pytest_result
        m.passed_tests = passed
        m.failed_tests = failed
        m.skipped_tests = skipped
        m.total_tests = passed + failed
        if m.total_tests > 0:
            m.task_completion_rate = m.passed_tests / m.total_tests

    # --- Audit log → canonical events ---
    audit_path = repo_path / "data" / "audit_log.jsonl"
    raw_entries = []
    if audit_path.exists():
        with open(audit_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    raw_entries.append(json.loads(line))

    events, unmapped = normalize_events(raw_entries, agent_cfg)
    m.total_audit_entries = len(events)

    # --- Routing classification from canonical routes ---
    case_routes: dict[str, set[str]] = {}
    for ev in events:
        case_routes.setdefault(ev.case_id, set()).add(ev.route)

    m.total_cases = len(case_routes)

    hitl_cases = {cid for cid, routes in case_routes.items() if "hitl" in routes}
    blocked_cases = {cid for cid, routes in case_routes.items() if "block" in routes}
    auto_cases = {cid for cid, routes in case_routes.items() if "auto" in routes}

    m.hitl_count = len(hitl_cases)
    m.blocked_count = len(blocked_cases)

    if m.total_cases > 0:
        m.hitl_trigger_rate = m.hitl_count / m.total_cases
        m.guardrail_block_rate = m.blocked_count / m.total_cases

    # --- Routing accuracy (bidirectional) ---
    # Derive actual routing per case: block > hitl > auto
    actual_routes: dict[str, str] = {}
    for cid, routes in case_routes.items():
        if "block" in routes:
            actual_routes[cid] = "block"
        elif "hitl" in routes:
            actual_routes[cid] = "hitl"
        elif "auto" in routes:
            actual_routes[cid] = "auto"

    correct = 0
    mismatched = []
    for cid, expected in expected_routes.items():
        actual = actual_routes.get(cid)
        if actual == expected:
            correct += 1
        else:
            mismatched.append({"case_id": cid, "expected": expected, "actual": actual})

    total_expected = len(expected_routes)
    m.accuracy_proxy = correct / total_expected if total_expected else 0
    m.details["routing_mismatched"] = mismatched

    # --- Gold set coverage ---
    goldset = agent_cfg.get("_goldset", {})
    if goldset:
        m.details["_goldset_ref"] = goldset
    goldset = agent_cfg.get("_goldset", {})
    if goldset:
        # Coverage by failure_mode
        fm_stats: dict[str, dict] = {}
        source_stats = {"synthetic": {"total": 0, "correct": 0}, "historical": {"total": 0, "correct": 0}}
        for cid, case in goldset.items():
            fm = case.get("failure_mode", "routing_error")
            src = case.get("source", "synthetic")
            expected = case["expected_route"]
            actual = actual_routes.get(cid)
            hit = actual == expected
            if fm not in fm_stats:
                fm_stats[fm] = {"total": 0, "correct": 0}
            fm_stats[fm]["total"] += 1
            if hit:
                fm_stats[fm]["correct"] += 1
            if src in source_stats:
                source_stats[src]["total"] += 1
                if hit:
                    source_stats[src]["correct"] += 1
        m.details["goldset_failure_modes"] = fm_stats
        m.details["goldset_source"] = source_stats
        m.details["goldset_total"] = len(goldset)

        # Save case-level history
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        CASE_HISTORY_PATH.parent.mkdir(exist_ok=True)
        with open(CASE_HISTORY_PATH, "a", encoding="utf-8") as f:
            for cid, case in goldset.items():
                record = {
                    "date": date_str,
                    "agent": name,
                    "case_id": cid,
                    "expected_route": case["expected_route"],
                    "actual_route": actual_routes.get(cid),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- Unmapped decisions warning ---
    if unmapped:
        m.details["unmapped_decisions"] = unmapped

    # --- Latency from canonical events ---
    durations = [ev.duration_ms for ev in events if ev.duration_ms > 0]
    if durations:
        m.avg_latency_ms = sum(durations) / len(durations)
        m.max_latency_ms = max(durations)

    # --- Silent failure scan (config-driven risk rules) ---
    if risk_rules and source_data_file:
        records = _load_source_data(repo_path, source_data_file)
        record_map = {r["id"]: r for r in records}

        count = 0
        for cid in auto_cases:
            if cid in hitl_cases or cid in blocked_cases:
                continue
            rec = record_map.get(cid)
            if rec is None:
                continue
            if any(evaluate_rule(rule, rec) for rule in risk_rules):
                count += 1

        m.silent_failure_count = count
        m.silent_failure_scanned = True
        m.silent_failure_rate = count / m.total_cases if m.total_cases > 0 else 0
        if count == 0:
            m.details["silent_failure_note"] = "已按规则扫描，当前无命中"
        else:
            m.details["silent_failure_note"] = f"扫描命中 {count} 个用例"

    return m


def generate_dashboard(metrics_list: list[AgentMetrics], thresholds: dict, agent_cfgs: list[dict] | None = None, previous: dict | None = None) -> str:
    """Generate markdown dashboard report.

    Convenience wrapper: build_snapshot → render_markdown.
    """
    from eval.snapshot import build_snapshot

    snapshot = build_snapshot(metrics_list, thresholds, agent_cfgs, previous)
    return render_markdown(snapshot)


def render_markdown(snapshot: dict) -> str:
    """Render a dashboard snapshot to markdown.

    Args:
        snapshot: dict produced by build_snapshot()
    """
    date_str = snapshot["date"]
    lines = [f"# Agent 质量仪表盘 {date_str}\n"]

    _status_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴", "uncalibrated": "⚪", "error": "⚠️"}
    _trend_sym = {"up": "↑", "down": "↓", "flat": "→", "none": "-"}
    _metric_labels = {
        "task_completion_rate": "任务完成率",
        "accuracy_proxy": "准确率代理",
        "hitl_trigger_rate": "HITL 触发率",
        "guardrail_block_rate": "Guardrail 拦截率",
        "silent_failure_rate": "Silent Failure",
        "avg_latency_ms": "平均延迟",
        "route_stability": "路由稳定性",
        "step_efficiency": "步骤效率",
        "tool_arg_correctness": "工具参数正确率",
    }

    for agent in snapshot["agents"]:
        name = agent["name"]
        vertical = agent.get("vertical")
        section_title = name
        if vertical:
            section_title += f"（{vertical}）"
        lines.append(f"## {section_title}\n")

        # CLASSIC five-dimension summary
        dims = agent.get("dimensions", {})
        dim_order = ["cost", "latency", "accuracy", "stability", "security"]
        dim_parts = []
        for d in dim_order:
            status = dims.get(d)
            if status is None:
                dim_parts.append(f"{d} ⚪")
            else:
                dim_parts.append(f"{d} {_status_emoji.get(status, '⚪')}")
        lines.append(f"**{' | '.join(dim_parts)}**\n")

        lines.append(f"| 指标 | 值 | 状态 | 趋势 | 说明 |")
        lines.append(f"|------|-----|------|------|------|")

        for m in agent["metrics"]:
            key = m["key"]
            label = _metric_labels.get(key, key)
            status = _status_emoji.get(m["status"], m["status"])
            trend = _trend_sym.get(m["trend"], m["trend"])
            note = m.get("note", "")

            # Format value
            if m["value"] is None:
                value_str = "—"
            elif key == "avg_latency_ms":
                value_str = f"{m['value']:.0f}ms"
            else:
                value_str = f"{m['value']:.1%}"

            # Threshold source annotation
            ts = m.get("threshold_source", "global")
            if ts == "agent":
                ts_note = "（agent 阈值）"
            elif ts == "vertical":
                ts_note = "（垂类阈值）"
            else:
                ts_note = ""

            # Mismatch details for accuracy
            if key == "accuracy_proxy" and m.get("mismatched"):
                mismatch_str = ", ".join(
                    f"{d['case_id']}: 期望 {d['expected']} → 实际 {d.get('actual') or '无'}"
                    for d in m["mismatched"]
                )
                if note:
                    note += f"；不匹配: {mismatch_str}"
                else:
                    note = f"不匹配: {mismatch_str}"

            lines.append(f"| {label} | {value_str} | {status} | {trend} | {note}{ts_note} |")

        # Unmapped decisions warning
        unmapped = agent.get("unmapped_decisions", [])
        if unmapped:
            lines.append(f"| 路由映射 | — | ⚠️ | — | 未映射的 decision 值: {', '.join(unmapped)}；请在 agents.yaml route_map 中补充 |")

        # Gold set coverage
        gs = agent.get("goldset", {})
        gs_total = gs.get("total", 0)
        if gs_total > 0:
            fm_stats = gs.get("failure_modes", {})
            fm_parts = []
            for fm, stats in sorted(fm_stats.items()):
                rate = stats["correct"] / stats["total"] if stats["total"] > 0 else 0
                fm_parts.append(f"{fm}: {stats['correct']}/{stats['total']} ({rate:.0%})")
            source_stats = gs.get("source", {})
            hist = source_stats.get("historical", {})
            hist_pct = hist.get("total", 0) / gs_total if gs_total > 0 else 0
            lines.append(f"| Gold Set 覆盖 | {gs_total} case | — | — | {'；'.join(fm_parts)}；historical 占比: {hist_pct:.0%} |")

        lines.append("")

    return "\n".join(lines)
