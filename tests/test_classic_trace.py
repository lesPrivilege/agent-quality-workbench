"""Tests for CLASSIC five-dimension tagging and trace evaluation."""

import json
import pytest
from eval.metric_registry import METRIC_REGISTRY, MetricEntry
from eval.metrics import AgentMetrics
from eval.snapshot import build_snapshot
from eval.trace import TraceStep, load_traces


# ── CLASSIC dimension completeness ──


class TestDimensionCompleteness:
    VALID_DIMENSIONS = {"cost", "latency", "accuracy", "stability", "security"}
    VALID_EVALUATORS = {"rule"}

    def test_all_metrics_have_valid_dimension(self):
        """Every metric in registry must have a valid CLASSIC dimension."""
        for entry in METRIC_REGISTRY:
            assert entry.dimension in self.VALID_DIMENSIONS, \
                f"Metric '{entry.key}' has invalid dimension '{entry.dimension}'"

    def test_all_metrics_have_valid_evaluator(self):
        """Every metric in registry must have a valid evaluator."""
        for entry in METRIC_REGISTRY:
            assert entry.evaluator in self.VALID_EVALUATORS, \
                f"Metric '{entry.key}' has invalid evaluator '{entry.evaluator}'"


# ── Five-dimension summary worst-case ──


class TestDimensionSummary:
    def _make_thresholds(self) -> dict:
        return {
            "metrics": {
                "task_completion_rate": {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]},
                "accuracy_proxy": {"green": [0.90, 1.0], "yellow": [0.75, 0.90], "red": [0.0, 0.75]},
                "hitl_trigger_rate": {"green": [0.0, 0.40], "yellow": [0.40, 0.60], "red": [0.60, 1.0]},
                "guardrail_block_rate": {"green": [0.0, 0.30], "yellow": [0.30, 0.50], "red": [0.50, 1.0]},
                "silent_failure_rate": {"green": [0.0, 0.05], "yellow": [0.05, 0.15], "red": [0.15, 1.0]},
                "cost_latency_proxy": {"green": [0, 500], "yellow": [500, 2000], "red": [2000, 999999]},
                "route_stability": {"green": [0.95, 1.0], "yellow": [0.85, 0.95], "red": [0.0, 0.85]},
            }
        }

    def test_worst_status_wins_in_dimension(self):
        """If a dimension has green + red metrics, summary should be red."""
        thresholds = self._make_thresholds()
        m = AgentMetrics(name="test")
        m.task_completion_rate = 1.0     # accuracy → green
        m.accuracy_proxy = 0.5           # accuracy → red (below 0.75)
        m.hitl_trigger_rate = 0.3        # cost → green
        m.guardrail_block_rate = 0.0     # security → green
        m.silent_failure_rate = 0.0      # security → green
        m.avg_latency_ms = 100           # latency → uncalibrated
        m.passed_tests = 10
        m.total_tests = 10
        m.details["silent_failure_note"] = "test"

        snapshot = build_snapshot([m], thresholds)
        dims = snapshot["agents"][0]["dimensions"]
        # accuracy has green (task_completion) + red (accuracy_proxy) → should be red
        assert dims["accuracy"] == "red"

    def test_all_uncalibrated_dimension(self):
        """If all metrics in a dimension are uncalibrated, summary is uncalibrated.

        Note: avg_latency_ms is now calibrated (upstream agents report real duration_ms).
        The 'cost' dimension has step_efficiency (uncalibrated) + hitl_trigger_rate (calibrated),
        so it won't be fully uncalibrated. This test verifies latency is now calibrated.
        """
        thresholds = self._make_thresholds()
        m = AgentMetrics(name="test")
        m.task_completion_rate = 1.0
        m.accuracy_proxy = 1.0
        m.hitl_trigger_rate = 0.3
        m.guardrail_block_rate = 0.0
        m.silent_failure_rate = 0.0
        m.avg_latency_ms = 100
        m.passed_tests = 10
        m.total_tests = 10
        m.details["silent_failure_note"] = "test"

        snapshot = build_snapshot([m], thresholds)
        dims = snapshot["agents"][0]["dimensions"]
        # latency now has calibrated avg_latency_ms → green (100 < 500)
        assert dims["latency"] == "green"


# ── Trace evaluation ──


class TestTraceDegradation:
    def test_no_trace_file_returns_none(self, tmp_path):
        """When trace_log.jsonl doesn't exist, load_traces returns None."""
        agent_cfg = {"repo": tmp_path}
        assert load_traces(agent_cfg) is None

    def test_no_repo_returns_none(self):
        """When repo path is missing, load_traces returns None."""
        assert load_traces({}) is None

    def test_trace_metrics_uncalibrated_without_data(self):
        """step_efficiency and tool_arg_correctness return None without trace data."""
        from eval.metric_registry import _step_efficiency, _tool_arg_correctness

        m = AgentMetrics(name="test")
        m.details["_agent_cfg_ref"] = {"repo": "/nonexistent"}
        # Reset cache
        from eval import metric_registry
        metric_registry._TRACE_CACHE.clear()
        metric_registry._TRACE_LOADED.discard("test")

        assert _step_efficiency(m) is None
        assert _tool_arg_correctness(m) is None


class TestTraceWithData:
    def test_step_efficiency_computed(self, tmp_path, monkeypatch):
        """step_efficiency correctly computes from trace fixture."""
        trace_file = tmp_path / "data" / "trace_log.jsonl"
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"case_id": "C001", "step_idx": 0, "action": "reason"},
            {"case_id": "C001", "step_idx": 1, "action": "tool_call", "tool_name": "check", "args_ok": True},
            {"case_id": "C001", "step_idx": 2, "action": "route"},
            {"case_id": "C002", "step_idx": 0, "action": "reason"},
            {"case_id": "C002", "step_idx": 1, "action": "route"},
        ]
        trace_file.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        from eval import metric_registry
        metric_registry._TRACE_CACHE.clear()
        metric_registry._TRACE_LOADED.discard("test-trace")

        m = AgentMetrics(name="test-trace")
        m.details["_agent_cfg_ref"] = {"repo": tmp_path}

        from eval.metric_registry import _step_efficiency
        result = _step_efficiency(m)
        # C001: 3 steps, C002: 2 steps → avg 2.5, expected_max=5 → 0.5
        assert result == 0.5

    def test_tool_arg_correctness_computed(self, tmp_path):
        """tool_arg_correctness correctly computes args_ok ratio."""
        trace_file = tmp_path / "data" / "trace_log.jsonl"
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {"case_id": "C001", "step_idx": 0, "action": "tool_call", "tool_name": "a", "args_ok": True},
            {"case_id": "C001", "step_idx": 1, "action": "tool_call", "tool_name": "b", "args_ok": False},
            {"case_id": "C001", "step_idx": 2, "action": "tool_call", "tool_name": "c", "args_ok": True},
            {"case_id": "C001", "step_idx": 3, "action": "reason"},  # not a tool_call, ignored
        ]
        trace_file.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        from eval import metric_registry
        metric_registry._TRACE_CACHE.clear()
        metric_registry._TRACE_LOADED.discard("test-trace2")

        m = AgentMetrics(name="test-trace2")
        m.details["_agent_cfg_ref"] = {"repo": tmp_path}

        from eval.metric_registry import _tool_arg_correctness
        result = _tool_arg_correctness(m)
        # 3 tool_calls: 2 args_ok=True, 1 args_ok=False → 2/3
        assert abs(result - 2 / 3) < 0.001


class TestTraceFieldMapping:
    def test_custom_field_map(self, tmp_path):
        """trace_field_map remaps field names correctly."""
        trace_file = tmp_path / "data" / "trace_log.jsonl"
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        # Use custom field names
        entries = [
            {"cid": "X001", "idx": 0, "act": "tool_call", "tool": "check", "ok": True, "ms": 50},
            {"cid": "X001", "idx": 1, "act": "route"},
        ]
        trace_file.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        agent_cfg = {
            "repo": tmp_path,
            "trace_field_map": {
                "case_id": "cid",
                "step_idx": "idx",
                "action": "act",
                "tool_name": "tool",
                "args_ok": "ok",
                "duration_ms": "ms",
            },
        }
        result = load_traces(agent_cfg)
        assert result is not None
        assert len(result) == 2
        assert result[0].case_id == "X001"
        assert result[0].step_idx == 0
        assert result[0].action == "tool_call"
        assert result[0].tool_name == "check"
        assert result[0].args_ok is True
        assert result[0].duration_ms == 50.0
        assert result[1].case_id == "X001"
        assert result[1].action == "route"
