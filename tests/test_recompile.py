"""Tests for the recompile trigger layer: threshold resolution, staleness,
drift, regulation_staleness, report."""

from datetime import date

from eval.threshold_resolution import resolve_threshold_cfg


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
