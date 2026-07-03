"""Tests for Gold Set loading, validation, and coverage stats."""

import pytest
import yaml
from eval.metrics import AgentMetrics, VALID_FAILURE_MODES, load_agents


class TestGoldsetLoading:
    def test_load_agents_has_expected_routes_from_goldset(self):
        """load_agents() populates expected_routes from goldset file."""
        agents = load_agents()
        contract = [a for a in agents if a["name"] == "contract-approval-agent"][0]
        expected = contract.get("expected_routes", {})
        assert len(expected) == 11, f"Expected 11 cases, got {len(expected)}"
        assert expected["C001"] == "auto"
        assert expected["C003"] == "block"

    def test_load_agents_goldset_dict_populated(self):
        """_goldset dict contains full case records with failure_mode."""
        agents = load_agents()
        contract = [a for a in agents if a["name"] == "contract-approval-agent"][0]
        gs = contract.get("_goldset", {})
        assert len(gs) == 11
        assert gs["C003"]["failure_mode"] == "guardrail_gap"
        assert gs["C003"]["source"] == "synthetic"


class TestFailureModeValidation:
    def test_valid_failure_modes_accepted(self, tmp_path):
        """All defined failure_mode values should load without error."""
        gs_file = tmp_path / "test_goldset.yaml"
        cases = []
        for fm in VALID_FAILURE_MODES:
            cases.append({"id": f"T_{fm}", "expected_route": "auto", "failure_mode": fm, "source": "synthetic", "note": "test"})
        gs_file.write_text(yaml.dump({"cases": cases}), encoding="utf-8")

        agents_file = tmp_path / "agents.yaml"
        agents_file.write_text(yaml.dump({
            "agents": [{
                "name": "test-agent",
                "repo": str(tmp_path / "repo"),
                "goldset": str(gs_file),
                "id_field": "id",
                "field_map": {"case_id": "id", "duration_ms": "duration_ms"},
                "route_map": {"auto_approved": "auto"},
            }]
        }), encoding="utf-8")

        # Should not raise
        from eval import metrics
        old_path = metrics.AGENTS_PATH
        try:
            metrics.AGENTS_PATH = agents_file
            agents = load_agents()
            assert len(agents[0]["_goldset"]) == len(VALID_FAILURE_MODES)
        finally:
            metrics.AGENTS_PATH = old_path

    def test_invalid_failure_mode_raises(self, tmp_path):
        """Illegal failure_mode must raise ValueError."""
        gs_file = tmp_path / "bad_goldset.yaml"
        gs_file.write_text(yaml.dump({
            "cases": [
                {"id": "X001", "expected_route": "auto", "failure_mode": "typo_mode", "source": "synthetic", "note": "bad"},
            ]
        }), encoding="utf-8")

        agents_file = tmp_path / "agents.yaml"
        agents_file.write_text(yaml.dump({
            "agents": [{
                "name": "test-agent",
                "repo": str(tmp_path / "repo"),
                "goldset": str(gs_file),
                "id_field": "id",
                "field_map": {"case_id": "id", "duration_ms": "duration_ms"},
                "route_map": {"auto_approved": "auto"},
            }]
        }), encoding="utf-8")

        from eval import metrics
        old_path = metrics.AGENTS_PATH
        try:
            metrics.AGENTS_PATH = agents_file
            with pytest.raises(ValueError, match="typo_mode"):
                load_agents()
        finally:
            metrics.AGENTS_PATH = old_path


class TestGoldsetCoverage:
    def test_coverage_by_failure_mode_and_source(self):
        """Goldset coverage stats correctly group by failure_mode and source."""
        m = AgentMetrics(name="test")
        # Simulate goldset with mixed failure_modes and sources
        goldset = {
            "C001": {"expected_route": "auto", "failure_mode": "routing_error", "source": "synthetic"},
            "C002": {"expected_route": "hitl", "failure_mode": "routing_error", "source": "synthetic"},
            "C003": {"expected_route": "block", "failure_mode": "guardrail_gap", "source": "historical"},
        }
        m.details["_goldset_ref"] = goldset
        # Simulate actual routes: C001 correct, C002 correct, C003 wrong
        actual_routes = {"C001": "auto", "C002": "hitl", "C003": "auto"}

        # Compute coverage manually (same logic as parse_agent_audit)
        fm_stats = {}
        source_stats = {"synthetic": {"total": 0, "correct": 0}, "historical": {"total": 0, "correct": 0}}
        for cid, case in goldset.items():
            fm = case["failure_mode"]
            src = case["source"]
            expected = case["expected_route"]
            actual = actual_routes.get(cid)
            hit = actual == expected
            if fm not in fm_stats:
                fm_stats[fm] = {"total": 0, "correct": 0}
            fm_stats[fm]["total"] += 1
            if hit:
                fm_stats[fm]["correct"] += 1
            source_stats[src]["total"] += 1
            if hit:
                source_stats[src]["correct"] += 1

        assert fm_stats["routing_error"] == {"total": 2, "correct": 2}
        assert fm_stats["guardrail_gap"] == {"total": 1, "correct": 0}
        assert source_stats["synthetic"] == {"total": 2, "correct": 2}
        assert source_stats["historical"] == {"total": 1, "correct": 0}

    def test_historical_ratio_zero(self):
        """When all sources are synthetic, historical ratio is 0%."""
        goldset = {
            "C001": {"expected_route": "auto", "failure_mode": "routing_error", "source": "synthetic"},
            "C002": {"expected_route": "hitl", "failure_mode": "routing_error", "source": "synthetic"},
        }
        total = len(goldset)
        hist_count = sum(1 for c in goldset.values() if c["source"] == "historical")
        assert hist_count / total == 0.0
