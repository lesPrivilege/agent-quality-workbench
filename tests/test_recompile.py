"""Tests for the recompile trigger layer: threshold resolution, staleness,
drift, regulation_staleness, report."""

from datetime import date

from eval.threshold_resolution import resolve_threshold_cfg
from eval.staleness import compute_goldset_staleness
from eval.drift import detect_metric_drift


class TestThresholdResolution:
    GLOBAL = {"hitl_trigger_rate": {"green": [0.0, 0.4], "yellow": [0.4, 0.6], "red": [0.6, 1.0]}}
    VERTICAL = {"hitl_trigger_rate": {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]}}
    AGENT = {"hitl_trigger_rate": {"green": [0.0, 0.9], "yellow": [0.9, 0.95], "red": [0.95, 1.0]}}

    def test_falls_back_to_global_when_no_override_or_preset(self):
        cfg = resolve_threshold_cfg("hitl_trigger_rate", self.GLOBAL, {}, {})
        assert cfg == self.GLOBAL["hitl_trigger_rate"]

    def test_vertical_preset_wins_over_global(self):
        cfg = resolve_threshold_cfg("hitl_trigger_rate", self.GLOBAL, {}, self.VERTICAL)
        assert cfg == self.VERTICAL["hitl_trigger_rate"]

    def test_agent_override_wins_over_vertical_preset(self):
        cfg = resolve_threshold_cfg("hitl_trigger_rate", self.GLOBAL, self.AGENT, self.VERTICAL)
        assert cfg == self.AGENT["hitl_trigger_rate"]

    def test_unknown_key_falls_back_to_default_thresholds(self):
        cfg = resolve_threshold_cfg("nonexistent_metric", self.GLOBAL, {}, {})
        assert cfg == {"green": [0.0, 1.0], "yellow": [0.0, 1.0], "red": [0.0, 1.0]}


class TestGoldsetStaleness:
    def test_fresh_case_not_stale(self):
        cases = [{"id": "C001", "last_verified": "2026-06-01"}]
        result = compute_goldset_staleness(cases, max_age_days=90, today=date(2026, 7, 4))
        assert result["stale_count"] == 0
        assert result["stale_cases"] == []

    def test_old_case_is_stale(self):
        cases = [{"id": "C002", "last_verified": "2026-01-01"}]
        result = compute_goldset_staleness(cases, max_age_days=90, today=date(2026, 7, 4))
        assert result["stale_count"] == 1
        assert result["stale_cases"] == [{"id": "C002", "reason": "age", "age_days": 184}]

    def test_missing_last_verified_is_stale(self):
        cases = [{"id": "C003"}]
        result = compute_goldset_staleness(cases, max_age_days=90, today=date(2026, 7, 4))
        assert result["stale_cases"] == [{"id": "C003", "reason": "missing_last_verified"}]

    def test_ratio_and_metadata(self):
        cases = [
            {"id": "C001", "last_verified": "2026-06-01"},
            {"id": "C002", "last_verified": "2026-01-01"},
        ]
        result = compute_goldset_staleness(cases, max_age_days=90, today=date(2026, 7, 4))
        assert result["total_cases"] == 2
        assert result["stale_count"] == 1
        assert result["stale_ratio"] == 0.5
        assert result["max_age_days"] == 90
        assert result["method"] == "age_proxy"

    def test_empty_goldset(self):
        result = compute_goldset_staleness([], max_age_days=90, today=date(2026, 7, 4))
        assert result["total_cases"] == 0
        assert result["stale_ratio"] == 0.0

    def test_unquoted_yaml_date_object_also_works(self):
        """PyYAML auto-parses an unquoted last_verified: 2026-06-01 into a real
        date object instead of a string (confirmed via yaml.safe_load) — a likely
        mistake for anyone hand-editing goldset YAML without knowing that quirk.
        Must not crash."""
        cases = [{"id": "C004", "last_verified": date(2026, 6, 1)}]
        result = compute_goldset_staleness(cases, max_age_days=90, today=date(2026, 7, 4))
        assert result["stale_count"] == 0


FIXTURE_THRESHOLDS = {
    "metrics": {
        "task_completion_rate": {"green": [0.95, 1.0], "yellow": [0.80, 0.95], "red": [0.0, 0.80]},
        "accuracy_proxy": {"green": [0.90, 1.0], "yellow": [0.75, 0.90], "red": [0.0, 0.75]},
        "hitl_trigger_rate": {"green": [0.0, 0.40], "yellow": [0.40, 0.60], "red": [0.60, 1.0]},
        "guardrail_block_rate": {"green": [0.0, 0.30], "yellow": [0.30, 0.50], "red": [0.50, 1.0]},
        "silent_failure_rate": {"green": [0.0, 0.05], "yellow": [0.05, 0.15], "red": [0.15, 1.0]},
        "cost_latency_proxy": {"green": [0, 500], "yellow": [500, 2000], "red": [2000, 999999]},
    }
}


def _history(dates_and_hitl):
    return [
        {
            "date": d,
            "agents": {
                "test-agent": {
                    "task_completion_rate": 1.0,
                    "accuracy_proxy": 1.0,
                    "hitl_trigger_rate": hitl,
                    "guardrail_block_rate": 0.0,
                    "silent_failure_rate": 0.0,
                    "avg_latency_ms": 100.0,
                }
            },
        }
        for d, hitl in dates_and_hitl
    ]


class TestMetricDrift:
    def test_insufficient_history(self):
        history = _history([("2026-07-01", 0.2), ("2026-07-02", 0.2), ("2026-07-03", 0.2)])
        result = detect_metric_drift(history, "test-agent", window_size=2,
                                      thresholds=FIXTURE_THRESHOLDS, agent_cfg={})
        assert result["insufficient_history"] is True
        assert result["history_count"] == 3
        assert result["required_count"] == 4
        assert result["events"] == []
        assert result["improvements"] == []

    def test_drift_event_green_to_yellow(self):
        history = _history([
            ("2026-07-01", 0.2), ("2026-07-02", 0.2),
            ("2026-07-03", 0.5), ("2026-07-04", 0.5),
        ])
        result = detect_metric_drift(history, "test-agent", window_size=2,
                                      thresholds=FIXTURE_THRESHOLDS, agent_cfg={})
        assert result["insufficient_history"] is False
        events = [e for e in result["events"] if e["metric"] == "hitl_trigger_rate"]
        assert len(events) == 1
        assert events[0]["old_status"] == "green"
        assert events[0]["new_status"] == "yellow"
        assert events[0]["old_avg"] == 0.2
        assert events[0]["new_avg"] == 0.5
        assert events[0]["old_window_span"] == {"from": "2026-07-01", "to": "2026-07-02"}
        assert events[0]["new_window_span"] == {"from": "2026-07-03", "to": "2026-07-04"}
        assert result["improvements"] == []

    def test_improvement_yellow_to_green(self):
        history = _history([
            ("2026-07-01", 0.5), ("2026-07-02", 0.5),
            ("2026-07-03", 0.2), ("2026-07-04", 0.2),
        ])
        result = detect_metric_drift(history, "test-agent", window_size=2,
                                      thresholds=FIXTURE_THRESHOLDS, agent_cfg={})
        improvements = [i for i in result["improvements"] if i["metric"] == "hitl_trigger_rate"]
        assert len(improvements) == 1
        assert improvements[0]["old_status"] == "yellow"
        assert improvements[0]["new_status"] == "green"
        assert result["events"] == []

    def test_flat_no_drift(self):
        history = _history([
            ("2026-07-01", 0.2), ("2026-07-02", 0.2),
            ("2026-07-03", 0.2), ("2026-07-04", 0.2),
        ])
        result = detect_metric_drift(history, "test-agent", window_size=2,
                                      thresholds=FIXTURE_THRESHOLDS, agent_cfg={})
        assert result["events"] == []
        assert result["improvements"] == []

    def test_agent_override_respected(self):
        """Same raw numbers as test_drift_event_green_to_yellow, but an agent
        override widens the green band — must NOT fire, proving drift.py uses
        the same override-aware resolution as the dashboard."""
        history = _history([
            ("2026-07-01", 0.2), ("2026-07-02", 0.2),
            ("2026-07-03", 0.5), ("2026-07-04", 0.5),
        ])
        agent_cfg = {
            "thresholds_override": {
                "hitl_trigger_rate": {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]},
            }
        }
        result = detect_metric_drift(history, "test-agent", window_size=2,
                                      thresholds=FIXTURE_THRESHOLDS, agent_cfg=agent_cfg)
        assert result["events"] == []
        assert result["improvements"] == []

    def test_vertical_preset_respected(self):
        """Same raw numbers again, but this time the widened green band comes
        from agent_cfg['_profile']['threshold_presets'] (vertical tier), not
        agent_cfg['thresholds_override'] (agent tier) — proves drift.py actually
        reads the vertical-preset path, not just the agent-override path tested
        above. Uses the real verticals/legal-compliance/profile.yaml shape
        (confirmed in Step 1), not an invented number."""
        history = _history([
            ("2026-07-01", 0.2), ("2026-07-02", 0.2),
            ("2026-07-03", 0.5), ("2026-07-04", 0.5),
        ])
        agent_cfg = {
            "_profile": {
                "threshold_presets": {
                    "hitl_trigger_rate": {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]},
                }
            }
        }
        result = detect_metric_drift(history, "test-agent", window_size=2,
                                      thresholds=FIXTURE_THRESHOLDS, agent_cfg=agent_cfg)
        assert result["events"] == []
        assert result["improvements"] == []

    def test_missing_agent_in_some_entries_does_not_crash(self):
        history = [
            {"date": "2026-07-01", "agents": {"other-agent": {"hitl_trigger_rate": 0.9}}},
            {"date": "2026-07-02", "agents": {"test-agent": {
                "task_completion_rate": 1.0, "accuracy_proxy": 1.0, "hitl_trigger_rate": 0.2,
                "guardrail_block_rate": 0.0, "silent_failure_rate": 0.0, "avg_latency_ms": 100.0,
            }}},
        ]
        result = detect_metric_drift(history, "test-agent", window_size=2,
                                      thresholds=FIXTURE_THRESHOLDS, agent_cfg={})
        assert result["history_count"] == 1
        assert result["insufficient_history"] is True

    def test_old_window_is_immediately_preceding_not_earliest_ever(self):
        """With k=6 > required=4 (window_size=2), the OLD window must be the
        2 entries immediately preceding the newest 2 — not the earliest 2 ever
        recorded. Anchoring to the earliest N is a real bug this feature's
        design phase already rejected once (see design spec 'Detailed Design
        > drift.py' section) — this is the only test shape that can catch a
        regression back to it, since entries[:N] == entries[k-2N:k-N] when
        k == 2N exactly, which is all the other tests use."""
        history = _history([
            ("2026-06-01", 0.9), ("2026-06-02", 0.9),   # earliest N — must be ignored
            ("2026-07-01", 0.2), ("2026-07-02", 0.2),   # correct OLD window
            ("2026-07-03", 0.5), ("2026-07-04", 0.5),   # correct NEW window
        ])
        result = detect_metric_drift(history, "test-agent", window_size=2,
                                      thresholds=FIXTURE_THRESHOLDS, agent_cfg={})
        events = [e for e in result["events"] if e["metric"] == "hitl_trigger_rate"]
        assert len(events) == 1
        assert events[0]["old_status"] == "green"   # 0.2 avg, NOT 0.9 (which would be red)
        assert events[0]["new_status"] == "yellow"
        assert events[0]["old_window_span"] == {"from": "2026-07-01", "to": "2026-07-02"}
