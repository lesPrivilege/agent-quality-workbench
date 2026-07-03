"""Cross-agent quality metrics — parse audit logs and test results."""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

THRESHOLDS_PATH = Path(__file__).parent / "thresholds.yaml"
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


def generate_dashboard(metrics_list: list[AgentMetrics], thresholds: dict) -> str:
    """Generate markdown dashboard report."""
    from datetime import datetime

    date_str = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Agent 质量仪表盘 {date_str}\n"]

    for m in metrics_list:
        lines.append(f"## {m.name}\n")
        lines.append(f"| 指标 | 值 | 状态 | 说明 |")
        lines.append(f"|------|-----|------|------|")

        t = thresholds["metrics"]

        # Task completion
        cfg = t["task_completion_rate"]
        status = _threshold_label(m.task_completion_rate, cfg)
        skip_note = f"（{m.skipped_tests} 个因无 key 跳过）" if m.skipped_tests > 0 else ""
        if m.pytest_error:
            lines.append(f"| 任务完成率 | — | ⚠️ | pytest 未能执行: {m.pytest_error} |")
        else:
            lines.append(f"| 任务完成率 | {m.task_completion_rate:.1%} | {status} | {m.passed_tests}/{m.total_tests} passed{skip_note} |")

        # Accuracy proxy
        cfg = t["accuracy_proxy"]
        status = _threshold_label(m.accuracy_proxy, cfg)
        lines.append(f"| 准确率代理 | {m.accuracy_proxy:.1%} | {status} | 审计日志 decision 命中预期路径 |")

        # HITL rate
        cfg = t["hitl_trigger_rate"]
        status = _threshold_label(m.hitl_trigger_rate, cfg)
        lines.append(f"| HITL 触发率 | {m.hitl_trigger_rate:.1%} | {status} | {m.hitl_count} 次人工介入 |")

        # Guardrail block rate
        cfg = t["guardrail_block_rate"]
        status = _threshold_label(m.guardrail_block_rate, cfg)
        lines.append(f"| Guardrail 拦截率 | {m.guardrail_block_rate:.1%} | {status} | {m.blocked_count} 次阻断 |")

        # Silent failure
        cfg = t["silent_failure_rate"]
        status = _threshold_label(m.silent_failure_rate, cfg)
        sf_note = m.details.get("silent_failure_note", "未扫描")
        lines.append(f"| Silent Failure | {m.silent_failure_rate:.1%} | {status} | {sf_note} |")

        # Latency
        cfg = t["cost_latency_proxy"]
        status = _threshold_label(m.avg_latency_ms, cfg)
        lines.append(f"| 平均延迟 | {m.avg_latency_ms:.0f}ms | {status} | 最大 {m.max_latency_ms:.0f}ms |")

        lines.append("")

    return "\n".join(lines)
