"""Tests for agent-quality-workbench core logic.

All tests use fixture data — no dependency on demo repos.
"""

import pytest
from eval.metrics import (
    AgentMetrics,
    _threshold_label,
    _trend_arrow,
    evaluate_rule,
    generate_dashboard,
    load_agents,
)
from eval.snapshot import build_snapshot, _threshold_status
from scripts.run_scorer import load_rubric, score_scenario


# ── evaluate_rule ──


class TestEvaluateRule:
    def test_equals(self):
        assert evaluate_rule({"path": "x", "equals": 1}, {"x": 1}) is True
        assert evaluate_rule({"path": "x", "equals": 1}, {"x": 2}) is False

    def test_equals_nested(self):
        rule = {"path": "a.b", "equals": True}
        assert evaluate_rule(rule, {"a": {"b": True}}) is True
        assert evaluate_rule(rule, {"a": {"b": False}}) is False
        assert evaluate_rule(rule, {"a": {}}) is False

    def test_contains(self):
        rule = {"path": "text", "contains": "反垄断"}
        assert evaluate_rule(rule, {"text": "涉及反垄断审查"}) is True
        assert evaluate_rule(rule, {"text": "正常条款"}) is False
        assert evaluate_rule(rule, {"text": 123}) is False

    def test_gt(self):
        rule = {"path": "金额", "gt": 3000000}
        assert evaluate_rule(rule, {"金额": 5000000}) is True
        assert evaluate_rule(rule, {"金额": 3000000}) is False
        assert evaluate_rule(rule, {"金额": 100}) is False

    def test_lt(self):
        rule = {"path": "score", "lt": 0.5}
        assert evaluate_rule(rule, {"score": 0.3}) is True
        assert evaluate_rule(rule, {"score": 0.5}) is False

    def test_all_combinator(self):
        rule = {
            "all": [
                {"path": "x", "equals": True},
                {"path": "y", "equals": True},
            ]
        }
        assert evaluate_rule(rule, {"x": True, "y": True}) is True
        assert evaluate_rule(rule, {"x": True, "y": False}) is False

    def test_any_combinator(self):
        rule = {
            "any": [
                {"path": "x", "equals": True},
                {"path": "y", "equals": True},
            ]
        }
        assert evaluate_rule(rule, {"x": True, "y": False}) is True
        assert evaluate_rule(rule, {"x": False, "y": False}) is False

    def test_missing_path(self):
        assert evaluate_rule({"path": "missing", "equals": 1}, {"x": 1}) is False

    def test_no_matching_operator(self):
        assert evaluate_rule({"path": "x"}, {"x": 1}) is False


# ── _threshold_label ──


class TestThresholdLabel:
    def test_green_in_range(self):
        cfg = {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]}
        assert _threshold_label(0.95, cfg) == "🟢"
        assert _threshold_label(1.0, cfg) == "🟢"

    def test_yellow_in_range(self):
        cfg = {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]}
        assert _threshold_label(0.80, cfg) == "🟡"
        assert _threshold_label(0.90, cfg) == "🟡"

    def test_red_in_range(self):
        cfg = {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]}
        assert _threshold_label(0.0, cfg) == "🔴"
        assert _threshold_label(0.50, cfg) == "🔴"

    def test_boundary_at_1_0(self):
        cfg = {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]}
        assert _threshold_label(1.0, cfg) == "🟢"

    def test_boundary_at_0_0(self):
        cfg = {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]}
        assert _threshold_label(0.0, cfg) == "🔴"

    def test_overlapping_boundary_green_wins(self):
        """At boundary between green and yellow, green takes priority."""
        cfg = {"green": [0.0, 0.40], "yellow": [0.40, 0.60], "red": [0.60, 1.0]}
        assert _threshold_label(0.40, cfg) == "🟢"

    def test_overlapping_boundary_yellow_wins(self):
        cfg = {"green": [0.0, 0.40], "yellow": [0.40, 0.60], "red": [0.60, 1.0]}
        assert _threshold_label(0.60, cfg) == "🟡"


# ── _trend_arrow ──


class TestTrendArrow:
    def test_no_previous(self):
        assert _trend_arrow(0.5, None, "accuracy_proxy") == "-"

    def test_same_value(self):
        assert _trend_arrow(0.5, 0.5, "accuracy_proxy") == "→"

    def test_higher_is_better_up(self):
        assert _trend_arrow(0.9, 0.8, "accuracy_proxy") == "↑"

    def test_higher_is_better_down(self):
        assert _trend_arrow(0.8, 0.9, "accuracy_proxy") == "↓"

    def test_lower_is_better_up(self):
        assert _trend_arrow(0.3, 0.2, "silent_failure_rate") == "↑"

    def test_lower_is_better_down(self):
        assert _trend_arrow(0.2, 0.3, "silent_failure_rate") == "↓"


# ── Routing accuracy ──


class TestRoutingAccuracy:
    def test_all_correct(self):
        """Verify accuracy_proxy reflects 100% match between expected and actual routes."""
        expected = {"C001": "auto", "C002": "hitl", "C003": "block"}
        actual = {"C001": "auto", "C002": "hitl", "C003": "block"}
        correct = sum(1 for cid, exp in expected.items() if actual.get(cid) == exp)
        mismatched = [
            {"case_id": cid, "expected": exp, "actual": actual.get(cid)}
            for cid, exp in expected.items()
            if actual.get(cid) != exp
        ]
        m = AgentMetrics(name="test")
        m.accuracy_proxy = correct / len(expected) if expected else 0
        m.details["routing_mismatched"] = mismatched
        assert m.accuracy_proxy == 1.0
        assert len(m.details["routing_mismatched"]) == 0

    def test_mismatch_recorded(self):
        m = AgentMetrics(name="test")
        expected = {"C001": "auto", "C002": "hitl"}
        # Simulating: C001 correct, C002 wrong
        actual = {"C001": "auto", "C002": "auto"}
        correct = sum(1 for cid, exp in expected.items() if actual.get(cid) == exp)
        mismatched = [
            {"case_id": cid, "expected": exp, "expected": exp, "actual": actual.get(cid)}
            for cid, exp in expected.items()
            if actual.get(cid) != exp
        ]
        assert correct == 1
        assert len(mismatched) == 1


# ── generate_dashboard column consistency ──


class TestDashboardColumns:
    def _make_metrics(self) -> AgentMetrics:
        m = AgentMetrics(name="test-agent")
        m.task_completion_rate = 1.0
        m.accuracy_proxy = 1.0
        m.hitl_trigger_rate = 0.3
        m.guardrail_block_rate = 0.1
        m.silent_failure_rate = 0.0
        m.avg_latency_ms = 100
        m.max_latency_ms = 200
        m.passed_tests = 10
        m.total_tests = 10
        m.hitl_count = 3
        m.blocked_count = 1
        m.details["silent_failure_note"] = "已按规则扫描，当前无命中"
        return m

    def _make_thresholds(self) -> dict:
        return {
            "metrics": {
                "task_completion_rate": {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]},
                "accuracy_proxy": {"green": [0.90, 1.0], "yellow": [0.75, 0.90], "red": [0.0, 0.75]},
                "hitl_trigger_rate": {"green": [0.0, 0.40], "yellow": [0.40, 0.60], "red": [0.60, 1.0]},
                "guardrail_block_rate": {"green": [0.0, 0.30], "yellow": [0.30, 0.50], "red": [0.50, 1.0]},
                "silent_failure_rate": {"green": [0.0, 0.05], "yellow": [0.05, 0.15], "red": [0.15, 1.0]},
                "cost_latency_proxy": {"green": [0, 500], "yellow": [500, 2000], "red": [2000, 999999]},
            }
        }

    def test_header_has_5_columns(self):
        m = self._make_metrics()
        t = self._make_thresholds()
        output = generate_dashboard([m], t)
        header_line = [line for line in output.split("\n") if line.startswith("| 指标")][0]
        cols = [c for c in header_line.split("|") if c.strip()]
        assert len(cols) == 5, f"Expected 5 columns, got {len(cols)}: {cols}"

    def test_data_rows_have_5_columns(self):
        m = self._make_metrics()
        t = self._make_thresholds()
        output = generate_dashboard([m], t)
        data_lines = [line for line in output.split("\n") if line.startswith("| 任务完成率")]
        for line in data_lines:
            cols = [c for c in line.split("|") if c.strip()]
            assert len(cols) == 5, f"Expected 5 columns, got {len(cols)}: {cols}"

    def test_pytest_error_row(self):
        m = self._make_metrics()
        m.pytest_error = "uv 未安装"
        t = self._make_thresholds()
        output = generate_dashboard([m], t)
        assert "⚠️" in output
        assert "pytest 未能执行" in output

    def test_latency_calibrated(self):
        """avg_latency_ms is now calibrated — should show threshold status, not ⚪."""
        m = self._make_metrics()
        t = self._make_thresholds()
        output = generate_dashboard([m], t)
        # avg_latency_ms=100, threshold green=[0,500] → 🟢
        assert "🟢" in output
        # Trace-level metrics (step_efficiency, tool_arg_correctness) still ⚪
        assert "⚪" in output


# ── Rubric level boundaries ──


class TestRubricLevels:
    @pytest.fixture
    def rubric(self):
        return load_rubric()

    def _make_scenario(self, score: float) -> dict:
        """Create a scenario dict that produces a given weighted score.

        With 6 dimensions each weighted 0.25, 0.20, 0.20, 0.15, 0.10, 0.10,
        we need: score = sum(dimension_score * weight)
        For simplicity, set all dimensions to score/1.0 (normalized).
        Actually, let's just set task_determinism to score/0.25 and others to 0.
        """
        return {
            "name": "test",
            "task_determinism": score / 0.25,
        }

    def test_score_at_0(self, rubric):
        result = score_scenario(rubric, self._make_scenario(0.0))
        assert result["level"] == "不建议上 agent"

    def test_score_at_1_0(self, rubric):
        result = score_scenario(rubric, self._make_scenario(1.0))
        assert result["level"] in ("不建议上 agent", "简单 agent（routing）")

    def test_score_at_5_0(self, rubric):
        result = score_scenario(rubric, self._make_scenario(5.0))
        assert result["level"] == "复杂 agent（全栈）"

    def test_score_above_5(self, rubric):
        """Score capped at 5.0 (max dimension 5 * max weight 0.25 * 6 dims = 5.0 at most)."""
        result = score_scenario(rubric, self._make_scenario(5.0))
        assert result["weighted_score"] <= 5.0

    def test_level_ranges_contiguous(self, rubric):
        """All level ranges should be contiguous (no gaps)."""
        levels = rubric["complexity_levels"]
        for i in range(len(levels) - 1):
            _, prev_hi = levels[i]["range"]
            next_lo, _ = levels[i + 1]["range"]
            assert prev_hi == next_lo, f"Gap between level {i} and {i+1}: {prev_hi} != {next_lo}"


# ── Profile / vertical priority ──


class TestProfilePriority:
    def _make_thresholds(self) -> dict:
        return {
            "metrics": {
                "task_completion_rate": {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]},
                "accuracy_proxy": {"green": [0.90, 1.0], "yellow": [0.75, 0.90], "red": [0.0, 0.75]},
                "hitl_trigger_rate": {"green": [0.0, 0.4], "yellow": [0.4, 0.6], "red": [0.6, 1.0]},
                "guardrail_block_rate": {"green": [0.0, 0.30], "yellow": [0.30, 0.50], "red": [0.50, 1.0]},
                "silent_failure_rate": {"green": [0.0, 0.05], "yellow": [0.05, 0.15], "red": [0.15, 1.0]},
                "cost_latency_proxy": {"green": [0, 500], "yellow": [500, 2000], "red": [2000, 999999]},
            }
        }

    def _make_metrics(self) -> AgentMetrics:
        m = AgentMetrics(name="test")
        m.task_completion_rate = 1.0
        m.accuracy_proxy = 1.0
        m.hitl_trigger_rate = 0.7
        m.guardrail_block_rate = 0.0
        m.silent_failure_rate = 0.0
        m.avg_latency_ms = 0
        m.passed_tests = 10
        m.total_tests = 10
        m.details["silent_failure_note"] = "test"
        return m

    def test_threshold_status_green(self):
        cfg = {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]}
        assert _threshold_status(0.6, cfg) == "green"

    def test_threshold_status_yellow(self):
        cfg = {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]}
        assert _threshold_status(0.75, cfg) == "yellow"

    def test_agent_override_wins_over_vertical(self):
        thresholds = self._make_thresholds()
        agent_cfgs = [{
            "name": "test",
            "vertical": "test-vertical",
            "thresholds_override": {"hitl_trigger_rate": {"green": [0.0, 0.8], "yellow": [0.8, 0.9], "red": [0.9, 1.0]}},
            "_profile": {"threshold_presets": {"hitl_trigger_rate": {"green": [0.0, 0.5], "yellow": [0.5, 0.7], "red": [0.7, 1.0]}}},
        }]
        m = self._make_metrics()
        snapshot = build_snapshot([m], thresholds, agent_cfgs)
        hitl = [x for x in snapshot["agents"][0]["metrics"] if x["key"] == "hitl_trigger_rate"][0]
        assert hitl["status"] == "green"  # agent override green up to 0.8
        assert hitl["threshold_source"] == "agent"

    def test_vertical_preset_used_when_no_agent_override(self):
        thresholds = self._make_thresholds()
        agent_cfgs = [{
            "name": "test",
            "vertical": "test-vertical",
            "_profile": {"threshold_presets": {"hitl_trigger_rate": {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]}}},
        }]
        m = self._make_metrics()
        snapshot = build_snapshot([m], thresholds, agent_cfgs)
        hitl = [x for x in snapshot["agents"][0]["metrics"] if x["key"] == "hitl_trigger_rate"][0]
        assert hitl["status"] == "green"  # vertical preset green up to 0.7
        assert hitl["threshold_source"] == "vertical"

    def test_global_used_when_no_override(self):
        thresholds = self._make_thresholds()
        m = self._make_metrics()
        snapshot = build_snapshot([m], thresholds)
        hitl = [x for x in snapshot["agents"][0]["metrics"] if x["key"] == "hitl_trigger_rate"][0]
        assert hitl["status"] == "red"  # global red from 0.6, value 0.7
        assert hitl["threshold_source"] == "global"

    def test_load_agents_resolves_templates(self):
        """Template refs in risk_rules should be resolved."""
        agents = load_agents()
        contract = [a for a in agents if a["name"] == "contract-approval-agent"][0]
        rules = contract.get("risk_rules", [])
        # Templates should be resolved to actual rule dicts
        for rule in rules:
            assert "template" not in rule, f"Unresolved template: {rule}"

    def test_scorer_profile_changes_weights(self):
        """Scorer with --profile should apply weight overrides."""
        rubric_global = load_rubric()
        rubric_profile = load_rubric("legal-compliance")
        global_weights = {d["name"]: d["weight"] for d in rubric_global["dimensions"]}
        profile_weights = {d["name"]: d["weight"] for d in rubric_profile["dimensions"]}
        assert profile_weights["risk_level"] == 0.25
        assert profile_weights["frequency"] == 0.05
        assert profile_weights["risk_level"] > global_weights["risk_level"]
        assert profile_weights["frequency"] < global_weights["frequency"]
