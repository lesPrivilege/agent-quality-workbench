"""Cross-agent quality metrics — parse audit logs and test results."""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

THRESHOLDS_PATH = Path(__file__).parent / "thresholds.yaml"
AGENTS_PATH = Path(__file__).parent / "agents.yaml"
CONTRACT_REPO = Path.home() / "Projects" / "contract-approval-agent"
COMPLIANCE_REPO = Path.home() / "Projects" / "compliance-review-agent"


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


def load_thresholds() -> dict:
    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_agents() -> list[dict]:
    """Load agent registrations from agents.yaml."""
    with open(AGENTS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    agents = data.get("agents", [])
    for a in agents:
        a["repo"] = Path(a["repo"]).expanduser()
    return agents


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


def _detect_silent_failures_contract(entries: list[dict], contracts: list[dict]) -> int:
    """Count contracts with risk signals that were auto-approved (no HITL/block).

    Risk signals for contract-approval-agent:
    - 关联方标记=true
    - 条款标记.不可逆=true
    - 条款标记.担保=true

    Note: extraction_low_confidence 检测已移除——audit_log 的 decision 字段
    不包含 low_confidence 值，无法可靠检测。若 agent 未来在 audit_log 输出
    risk_signals 字段，可重新接入。

    A silent failure = risk signal present + final decision is auto_approved.
    """
    contract_map = {c["id"]: c for c in contracts}

    # Index entries by case id, derive final routing per case
    case_routes: dict[str, set[str]] = {}
    for e in entries:
        cid = e.get("contract_id", "")
        d = e.get("decision", "")
        case_routes.setdefault(cid, set()).add(d)

    auto_approved = {cid for cid, decisions in case_routes.items() if "auto_approved" in decisions}
    hitl_triggered = {cid for cid, decisions in case_routes.items() if decisions & {"approved", "rejected"}}
    blocked = {cid for cid, decisions in case_routes.items() if "blocked" in decisions}

    count = 0
    for cid in auto_approved:
        if cid in hitl_triggered or cid in blocked:
            continue  # was also HITL'd or blocked, not silent
        c = contract_map.get(cid)
        if c is None:
            continue
        has_risk = False
        if c.get("关联方标记", False):
            has_risk = True
        if c.get("条款标记", {}).get("不可逆", False):
            has_risk = True
        if c.get("条款标记", {}).get("担保", False):
            has_risk = True
        if has_risk:
            count += 1

    return count


def _detect_silent_failures_compliance(entries: list[dict], materials: list[dict]) -> int:
    """Count materials with risk signals that were auto-approved (no HITL/block).

    Risk signals for compliance-review-agent:
    - 关联方标记=true
    - 涉及数据共享=true AND 涉及受监管业务=true
    - 条款标记.担保=true
    - 内容摘要 contains '反垄断' AND 金额>3000000

    A silent failure = risk signal present + final decision is auto_approved.
    """
    material_map = {m["id"]: m for m in materials}

    case_routes: dict[str, set[str]] = {}
    for e in entries:
        mid = e.get("material_id", "")
        d = e.get("decision", "")
        case_routes.setdefault(mid, set()).add(d)

    auto_approved = {mid for mid, decisions in case_routes.items() if "auto_approved" in decisions}
    hitl_triggered = {mid for mid, decisions in case_routes.items() if decisions & {"approved", "rejected"}}
    blocked = {mid for mid, decisions in case_routes.items() if "blocked" in decisions}

    count = 0
    for mid in auto_approved:
        if mid in hitl_triggered or mid in blocked:
            continue
        m = material_map.get(mid)
        if m is None:
            continue
        has_risk = False
        if m.get("关联方标记", False):
            has_risk = True
        if m.get("涉及数据共享", False) and m.get("涉及受监管业务", False):
            has_risk = True
        if m.get("条款标记", {}).get("担保", False):
            has_risk = True
        if "反垄断" in m.get("内容摘要", "") and m.get("金额", 0) > 3_000_000:
            has_risk = True
        if has_risk:
            count += 1

    return count


def parse_contract_audit(repo_path: Path) -> AgentMetrics:
    """Parse contract-approval-agent audit logs and test expectations."""
    m = AgentMetrics(name="contract-approval-agent")

    # --- Real pytest run ---
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
        m.total_tests = passed + failed  # completion rate denominator excludes skipped
        if m.total_tests > 0:
            m.task_completion_rate = m.passed_tests / m.total_tests

    # --- Audit log ---
    audit_path = repo_path / "data" / "audit_log.jsonl"
    entries = []
    if audit_path.exists():
        with open(audit_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))

    m.total_audit_entries = len(entries)
    m.total_cases = len(set(e.get("contract_id", "") for e in entries))

    hitl_contracts = set()
    blocked_contracts = set()
    auto_contracts = set()
    for e in entries:
        cid = e.get("contract_id", "")
        d = e.get("decision", "")
        if d in ("approved", "rejected"):
            hitl_contracts.add(cid)
        elif d == "blocked":
            blocked_contracts.add(cid)
        elif d == "auto_approved":
            auto_contracts.add(cid)

    m.hitl_count = len(hitl_contracts)
    m.blocked_count = len(blocked_contracts)

    durations = [e.get("duration_ms", 0) for e in entries]
    if durations:
        m.avg_latency_ms = sum(durations) / len(durations)
        m.max_latency_ms = max(durations)

    if m.total_cases > 0:
        m.hitl_trigger_rate = len(hitl_contracts) / m.total_cases
        m.guardrail_block_rate = len(blocked_contracts) / m.total_cases

    # Accuracy proxy
    expected_auto = {"C001", "C005", "C007"}
    expected_block = {"C003", "C004", "C009", "C011"}
    expected_hitl = {"C002", "C006", "C008", "C010"}
    correct = len(expected_auto & auto_contracts) + len(expected_block & blocked_contracts) + len(expected_hitl & hitl_contracts)
    total = len(expected_auto | expected_block | expected_hitl)
    m.accuracy_proxy = correct / total if total else 0

    # --- Real silent failure scan ---
    contracts = _load_source_data(repo_path, "contracts.jsonl")
    m.silent_failure_count = _detect_silent_failures_contract(entries, contracts)
    m.silent_failure_scanned = True
    m.silent_failure_rate = m.silent_failure_count / m.total_cases if m.total_cases > 0 else 0
    if m.silent_failure_count == 0:
        m.details["silent_failure_note"] = "已按规则扫描，当前无命中"
    else:
        m.details["silent_failure_note"] = f"扫描命中 {m.silent_failure_count} 个用例"

    return m


def parse_compliance_audit(repo_path: Path) -> AgentMetrics:
    """Parse compliance-review-agent audit logs and test expectations."""
    m = AgentMetrics(name="compliance-review-agent")

    # --- Real pytest run ---
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

    # --- Audit log ---
    audit_path = repo_path / "data" / "audit_log.jsonl"
    entries = []
    if audit_path.exists():
        with open(audit_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))

    m.total_audit_entries = len(entries)
    m.total_cases = len(set(e.get("material_id", "") for e in entries))

    hitl_materials = set()
    auto_materials = set()
    blocked_materials = set()
    for e in entries:
        mid = e.get("material_id", "")
        d = e.get("decision", "")
        if d in ("approved", "rejected"):
            hitl_materials.add(mid)
        elif d == "auto_approved":
            auto_materials.add(mid)
        elif d == "blocked":
            blocked_materials.add(mid)

    m.hitl_count = len(hitl_materials)
    m.blocked_count = len(blocked_materials)

    durations = [e.get("duration_ms", 0) for e in entries]
    if durations:
        m.avg_latency_ms = sum(durations) / len(durations)
        m.max_latency_ms = max(durations)

    if m.total_cases > 0:
        m.hitl_trigger_rate = len(hitl_materials) / m.total_cases
        m.guardrail_block_rate = len(blocked_materials) / m.total_cases

    # Accuracy proxy
    expected_auto = {"M001", "M005", "M007", "M009"}
    expected_hitl = {"M002", "M006", "M008", "M010"}
    correct = len(expected_auto & auto_materials) + len(expected_hitl & hitl_materials)
    total = len(expected_auto | expected_hitl)
    m.accuracy_proxy = correct / total if total else 0

    # --- Real silent failure scan ---
    materials = _load_source_data(repo_path, "materials.jsonl")
    m.silent_failure_count = _detect_silent_failures_compliance(entries, materials)
    m.silent_failure_scanned = True
    m.silent_failure_rate = m.silent_failure_count / m.total_cases if m.total_cases > 0 else 0
    if m.silent_failure_count == 0:
        m.details["silent_failure_note"] = "已按规则扫描，当前无命中"
    else:
        m.details["silent_failure_note"] = f"扫描命中 {m.silent_failure_count} 个用例"

    return m


def parse_agent_audit(agent_cfg: dict) -> AgentMetrics:
    """Parse an agent's audit logs and test results from config.

    Unified replacement for parse_contract_audit / parse_compliance_audit.
    All agent-specific differences come from agent_cfg (loaded from agents.yaml).
    """
    name = agent_cfg["name"]
    repo_path = agent_cfg["repo"]
    id_field = agent_cfg["id_field"]
    source_data_file = agent_cfg.get("source_data", "")
    expected_routes = agent_cfg.get("expected_routes", {})
    risk_rules = agent_cfg.get("risk_rules", [])

    m = AgentMetrics(name=name)

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

    # --- Audit log ---
    audit_path = repo_path / "data" / "audit_log.jsonl"
    entries = []
    if audit_path.exists():
        with open(audit_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))

    m.total_audit_entries = len(entries)
    m.total_cases = len(set(e.get(id_field, "") for e in entries))

    # --- Routing classification ---
    case_routes: dict[str, set[str]] = {}
    for e in entries:
        cid = e.get(id_field, "")
        d = e.get("decision", "")
        case_routes.setdefault(cid, set()).add(d)

    hitl_cases = set()
    blocked_cases = set()
    auto_cases = set()
    for cid, decisions in case_routes.items():
        if "blocked" in decisions:
            blocked_cases.add(cid)
        elif decisions & {"approved", "rejected"}:
            hitl_cases.add(cid)
        elif "auto_approved" in decisions:
            auto_cases.add(cid)

    m.hitl_count = len(hitl_cases)
    m.blocked_count = len(blocked_cases)

    if m.total_cases > 0:
        m.hitl_trigger_rate = m.hitl_count / m.total_cases
        m.guardrail_block_rate = m.blocked_count / m.total_cases

    # --- Routing accuracy (P1-2: bidirectional) ---
    # Derive actual routing per case: blocked > hitl > auto
    actual_routes: dict[str, str] = {}
    for cid, decisions in case_routes.items():
        if "blocked" in decisions:
            actual_routes[cid] = "block"
        elif decisions & {"approved", "rejected"}:
            actual_routes[cid] = "hitl"
        elif "auto_approved" in decisions:
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


def generate_dashboard(metrics_list: list[AgentMetrics], thresholds: dict, agent_cfgs: list[dict] | None = None) -> str:
    """Generate markdown dashboard report.

    Args:
        metrics_list: list of parsed agent metrics
        thresholds: global thresholds dict from thresholds.yaml
        agent_cfgs: optional list of agent configs (for per-agent threshold overrides)
    """
    from datetime import datetime

    date_str = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Agent 质量仪表盘 {date_str}\n"]

    cfg_map = {}
    if agent_cfgs:
        for cfg in agent_cfgs:
            cfg_map[cfg["name"]] = cfg

    for m in metrics_list:
        lines.append(f"## {m.name}\n")
        lines.append(f"| 指标 | 值 | 状态 | 说明 |")
        lines.append(f"|------|-----|------|------|")

        t = thresholds["metrics"]
        overrides = {}
        if m.name in cfg_map:
            overrides = cfg_map[m.name].get("thresholds_override", {})

        def _get_cfg(metric_key: str) -> dict:
            return overrides.get(metric_key, t[metric_key])

        def _is_override(metric_key: str) -> bool:
            return metric_key in overrides

        # Task completion
        cfg = _get_cfg("task_completion_rate")
        status = _threshold_label(m.task_completion_rate, cfg)
        skip_note = f"（{m.skipped_tests} 个因无 key 跳过）" if m.skipped_tests > 0 else ""
        if m.pytest_error:
            lines.append(f"| 任务完成率 | — | ⚠️ | pytest 未能执行: {m.pytest_error} |")
        else:
            lines.append(f"| 任务完成率 | {m.task_completion_rate:.1%} | {status} | {m.passed_tests}/{m.total_tests} passed{skip_note} |")

        # Accuracy proxy (routing accuracy)
        cfg = _get_cfg("accuracy_proxy")
        status = _threshold_label(m.accuracy_proxy, cfg)
        note = "路由准确率（期望 vs 实际）"
        mismatched = m.details.get("routing_mismatched", [])
        if mismatched:
            mismatch_str = ", ".join(f"{d['case_id']}: 期望 {d['expected']} → 实际 {d['actual'] or '无'}" for d in mismatched)
            note += f"；不匹配: {mismatch_str}"
        override_note = "（agent 阈值）" if _is_override("accuracy_proxy") else ""
        lines.append(f"| 准确率代理 | {m.accuracy_proxy:.1%} | {status} | {note}{override_note} |")

        # HITL rate
        cfg = _get_cfg("hitl_trigger_rate")
        status = _threshold_label(m.hitl_trigger_rate, cfg)
        override_note = "（agent 阈值）" if _is_override("hitl_trigger_rate") else ""
        lines.append(f"| HITL 触发率 | {m.hitl_trigger_rate:.1%} | {status} | {m.hitl_count} 次人工介入{override_note} |")

        # Guardrail block rate
        cfg = _get_cfg("guardrail_block_rate")
        status = _threshold_label(m.guardrail_block_rate, cfg)
        override_note = "（agent 阈值）" if _is_override("guardrail_block_rate") else ""
        lines.append(f"| Guardrail 拦截率 | {m.guardrail_block_rate:.1%} | {status} | {m.blocked_count} 次阻断{override_note} |")

        # Silent failure
        cfg = _get_cfg("silent_failure_rate")
        status = _threshold_label(m.silent_failure_rate, cfg)
        sf_note = m.details.get("silent_failure_note", "未扫描")
        lines.append(f"| Silent Failure | {m.silent_failure_rate:.1%} | {status} | {sf_note} |")

        # Latency (P1-4: placeholder — demo data uncalibrated)
        lines.append(f"| 平均延迟 | {m.avg_latency_ms:.0f}ms | ⚪ | 未校准：demo 数据，生产环境需替换为 token 计数 |")

        lines.append("")

    return "\n".join(lines)
