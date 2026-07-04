"""Tests for the recompile trigger layer: threshold resolution, staleness,
drift, regulation_staleness, report."""

from datetime import date

from eval.threshold_resolution import resolve_threshold_cfg
from eval.staleness import compute_goldset_staleness


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
