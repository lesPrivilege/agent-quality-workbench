# 重编译触发层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `docs/roadmap.md` 缺口一的 1-3 项——goldset age-based 过期检测、`history.jsonl` 滑动窗口漂移检测、聚合两者的重编译触发报告；缺口一第 4 项（模式三·法规时效失效）只做契约定义，不做真实扫描。

**Architecture:** 四个新的 `eval/` 计算模块（`threshold_resolution.py` / `staleness.py` / `drift.py` / `regulation_staleness.py`）+ 一个报告聚合模块（`recompile_report.py`，计算/渲染分离，同 `snapshot.py` + `render_markdown` 的既有模式）+ 一个编排脚本（`scripts/run_recompile_check.py`，同 `run_dashboard.py` 的既有模式）。`eval/snapshot.py` 做一处行为不变的重构：把内嵌的阈值优先级解析逻辑抽到 `threshold_resolution.py`，供 dashboard 和 drift 两处复用，避免"什么算绿"在两处各自维护而分叉。

**Tech Stack:** Python 3、pytest、PyYAML（既有依赖，无新增）。

**关联设计文档：** [`docs/superpowers/specs/2026-07-04-recompile-trigger-layer-design.md`](../specs/2026-07-04-recompile-trigger-layer-design.md)

**已确认参数**：`staleness.max_age_days=90`，`drift.window_size=5`，goldset 21 条 case 的 `last_verified` 统一回填 `2026-07-04`（字段引入日，非验证日）。

**计划相对设计文档的一处实现级细化**（非结构性偏离，供审阅者知悉）：设计文档把"汇总 + 渲染报告"的逻辑描述为 `run_recompile_check.py` 内部步骤；本计划为其单开一个 `eval/recompile_report.py` 模块（`build_recompile_snapshot()` + `render_recompile_markdown()`），理由是这能让聚合/渲染逻辑像 `snapshot.py`/`metrics.render_markdown` 一样被单独单元测试，脚本本身保持和 `run_dashboard.py` 一样"只编排、不含逻辑"的瘦身状态。

**执行前置条件**：本计划文件自身必须先被 commit（独立一个提交，在 Task 1 之前），再开始执行 Task 1。和 `docs/roadmap.md` → spec 的顺序同一逻辑——决策记录（先有计划）不该被实现进度（后有代码）扣押，git 历史要能读出"计划先于执行"。

---

### Task 1: `eval/thresholds.yaml` — 新增 staleness + drift 配置节

**Files:**
- Modify: `eval/thresholds.yaml` (在文件末尾追加)

- [ ] **Step 1: 在文件末尾追加两个新的顶层节**

在 `eval/thresholds.yaml` 末尾（`route_stability` 那一节之后）追加：

```yaml

staleness:
  goldset_case_max_age_days: 90
  rationale: "goldset case 距上次人工复验超过 90 天视为过期（age proxy，不比对真实法规变更日期——当前无变更追踪数据源）。90 天是一个季度的量级，与 PM 复盘节奏对齐"

drift:
  window_size: 5
  rationale: "与 route_stability 已用的 k=5 保持一致习惯。滑动窗口对比最近 N 次运行 vs 紧邻其前的 N 次运行，任一指标越级变色记为漂移事件"
```

- [ ] **Step 2: 确认 YAML 仍能被解析**

Run: `uv run python -c "from eval.metrics import load_thresholds; t = load_thresholds(); print(t['staleness']); print(t['drift'])"`
Expected: 打印 `{'goldset_case_max_age_days': 90, 'rationale': '...'}` 和 `{'window_size': 5, 'rationale': '...'}`，无异常。

- [ ] **Step 3: Commit**

```bash
git add eval/thresholds.yaml
git commit -m "chore: add staleness + drift config sections to thresholds.yaml"
```

---

### Task 2: 抽取 `eval/threshold_resolution.py`，重构 `eval/snapshot.py`

**Files:**
- Create: `eval/threshold_resolution.py`
- Create: `tests/test_recompile.py`（本计划的新测试文件，本任务先写 `TestThresholdResolution`，后续任务陆续追加）
- Modify: `eval/snapshot.py:1-24` (imports + `_threshold_status` 重命名), `eval/snapshot.py:56-89` (`build_snapshot` 内部)
- Modify: `tests/test_workbench.py:15`（import 改名）, `tests/test_workbench.py:316-322`（两处调用改名）——重命名后必须同步更新，否则整个文件收集失败
- 回归覆盖: `tests/test_workbench.py` 其余部分（`TestProfilePriority` 里 `test_agent_override_wins_over_vertical` 等，靠现有全量测试兜底——行为不变的重构，不新增这部分专属测试）

- [ ] **Step 1: 重构前，先确认现有全量测试通过（建立基线）**

Run: `uv run pytest tests/ -v`
Expected: 全部通过，`61 passed`（当前基线）。

- [ ] **Step 2: 写 `resolve_threshold_cfg()` 三级优先级的失败测试**

创建 `tests/test_recompile.py`，写入：

```python
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
```

- [ ] **Step 3: 运行确认失败**

Run: `uv run pytest tests/test_recompile.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'eval.threshold_resolution'`

- [ ] **Step 4: 创建 `eval/threshold_resolution.py`**

```python
"""Threshold resolution — agent override > vertical preset > global priority chain.

Shared by snapshot.py (current dashboard state) and drift.py (historical
window comparison) so the two never disagree about what counts as
green/yellow/red for a given agent + metric.
"""

from __future__ import annotations

DEFAULT_THRESHOLDS = {"green": [0.0, 1.0], "yellow": [0.0, 1.0], "red": [0.0, 1.0]}


def resolve_threshold_cfg(
    thresholds_key: str,
    global_thresholds: dict,
    agent_overrides: dict,
    vertical_presets: dict,
) -> dict:
    """Priority: agent override > vertical preset > global."""
    if thresholds_key in agent_overrides:
        return agent_overrides[thresholds_key]
    if thresholds_key in vertical_presets:
        return vertical_presets[thresholds_key]
    return global_thresholds.get(thresholds_key, DEFAULT_THRESHOLDS)
```

- [ ] **Step 5: 运行确认 `TestThresholdResolution` 通过**

Run: `uv run pytest tests/test_recompile.py -v`
Expected: 4 个测试全部 PASS

- [ ] **Step 6: 修改 `eval/snapshot.py` 顶部 import 和 `_threshold_status` 重命名**

在 `eval/snapshot.py` 里，把：

```python
from eval.metric_registry import METRIC_REGISTRY, MetricEntry
from eval.metrics import AgentMetrics


def _threshold_status(value: float, metric_cfg: dict) -> str:
```

改成：

```python
from eval.metric_registry import METRIC_REGISTRY, MetricEntry
from eval.metrics import AgentMetrics
from eval.threshold_resolution import resolve_threshold_cfg


def threshold_status(value: float, metric_cfg: dict) -> str:
```

- [ ] **Step 7: 修改 `build_snapshot()` 内部，删除内嵌闭包和局部 `_DEFAULT_THRESHOLDS`**

把：

```python
        prev_metrics = previous.get(m.name) if previous else None

        _DEFAULT_THRESHOLDS = {"green": [0.0, 1.0], "yellow": [0.0, 1.0], "red": [0.0, 1.0]}

        def _get_cfg(thresholds_key: str) -> dict:
            # Priority: agent override > vertical preset > global
            if thresholds_key in overrides:
                return overrides[thresholds_key]
            if thresholds_key in vertical_presets:
                return vertical_presets[thresholds_key]
            return t.get(thresholds_key, _DEFAULT_THRESHOLDS)
```

改成：

```python
        prev_metrics = previous.get(m.name) if previous else None

        def _get_cfg(thresholds_key: str) -> dict:
            return resolve_threshold_cfg(thresholds_key, t, overrides, vertical_presets)
```

- [ ] **Step 8: 修改 `build_snapshot()` 内唯一的调用点**

把：

```python
                    status = _threshold_status(value, cfg)
```

改成：

```python
                    status = threshold_status(value, cfg)
```

- [ ] **Step 9: 修复 `tests/test_workbench.py` 里对旧私有名的引用**

`tests/test_workbench.py` 有一处模块级 import 和两处调用直接用的是 `_threshold_status` 这个旧私有名，重命名后如果不改，整个文件会在收集阶段就报 `ImportError`，导致该文件里全部既有测试（不只是用到这个函数的 2 个）一起报错。

把第 15 行：

```python
from eval.snapshot import build_snapshot, _threshold_status
```

改成：

```python
from eval.snapshot import build_snapshot, threshold_status
```

把 `TestProfilePriority` 类里的两处调用：

```python
    def test_threshold_status_green(self):
        cfg = {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]}
        assert _threshold_status(0.6, cfg) == "green"

    def test_threshold_status_yellow(self):
        cfg = {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]}
        assert _threshold_status(0.75, cfg) == "yellow"
```

改成：

```python
    def test_threshold_status_green(self):
        cfg = {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]}
        assert threshold_status(0.6, cfg) == "green"

    def test_threshold_status_yellow(self):
        cfg = {"green": [0.0, 0.7], "yellow": [0.7, 0.85], "red": [0.85, 1.0]}
        assert threshold_status(0.75, cfg) == "yellow"
```

注意：`tests/test_workbench.py` 里另一个函数 `_threshold_label`（`eval/metrics.py` 里的，emoji 版本）不在本次重命名范围内，不要动它——那是已知的范围外重复项（见本计划末尾"已知的范围外事项"），本任务只改 `_threshold_status`。

- [ ] **Step 10: 全文搜索确认没有遗留的 `_threshold_status` / `_DEFAULT_THRESHOLDS` 引用**

Run: `grep -rn "_threshold_status\|_DEFAULT_THRESHOLDS" eval/ scripts/ tests/`
Expected: 无匹配输出（`_threshold_label` 会被跳过，因为搜的是 `_threshold_status` 不是 `_threshold_label`，两者字符串不同）。

- [ ] **Step 11: 重跑全量测试，确认行为不变且新测试都在**

Run: `uv run pytest tests/ -v`
Expected: `65 passed`（基线 61 + 本任务新增的 4 个 `TestThresholdResolution`），无失败，尤其确认 `test_workbench.py::TestProfilePriority` 全部通过（证明重命名后旧测试仍然认得这个函数）。

- [ ] **Step 12: Commit**

```bash
git add eval/threshold_resolution.py eval/snapshot.py tests/test_recompile.py tests/test_workbench.py
git commit -m "refactor: extract threshold_resolution.py, rename _threshold_status to public

Behavior-preserving for snapshot.py — covered by the existing full suite
staying green. Updates test_workbench.py's import of the old private
name (would otherwise break collection of the entire file, not just
the two tests calling it directly). Adds dedicated TestThresholdResolution
coverage for the three-tier priority chain itself (agent override >
vertical preset > global), which previously had no direct unit test of
its own — test_workbench.py's TestProfilePriority covers the same chain
but only indirectly through build_snapshot(). snapshot.py and the
upcoming drift.py must resolve overrides identically, or the dashboard
and the recompile-trigger report could disagree about what counts as
green for the same agent+metric."
```

---

### Task 3: `eval/staleness.py` — goldset 过期检测

**Files:**
- Create: `eval/staleness.py`
- Test: `tests/test_recompile.py` (Task 2 已创建此文件，本任务追加 `TestGoldsetStaleness`)

- [ ] **Step 1: 写失败测试**

在 `tests/test_recompile.py` 顶部的 `from eval.threshold_resolution import resolve_threshold_cfg` 那一行之后追加一行 import，并在文件末尾追加新的测试类：

```python
from eval.staleness import compute_goldset_staleness


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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_recompile.py -v`
Expected: 整个文件收集失败（`ModuleNotFoundError: No module named 'eval.staleness'`）——包括 Task 2 已经写好的 `TestThresholdResolution` 也会一起报错，这是正常的：同一文件顶部 import 失败会阻塞整个文件的收集，不代表 `TestThresholdResolution` 本身坏了。

- [ ] **Step 3: 实现 `eval/staleness.py`**

```python
"""Goldset case staleness — pure age-based proxy.

No regulation-change-date comparison: no such tracking data source exists
yet in this repo. If last_verified is missing entirely, the case is
treated as stale — absence of the field must not be read as freshness.
"""

from __future__ import annotations

from datetime import date


def compute_goldset_staleness(
    goldset_cases: list[dict], max_age_days: int, today: date
) -> dict:
    stale_cases = []
    for case in goldset_cases:
        last_verified = case.get("last_verified")
        if not last_verified:
            stale_cases.append({"id": case["id"], "reason": "missing_last_verified"})
            continue
        # last_verified is normally a quoted YAML string ("2026-07-04"), but an
        # unquoted date literal in goldset YAML auto-parses via PyYAML into a
        # real date object — accept both rather than crash on that easy mistake.
        verified_date = last_verified if isinstance(last_verified, date) else date.fromisoformat(last_verified)
        age_days = (today - verified_date).days
        if age_days > max_age_days:
            stale_cases.append({"id": case["id"], "reason": "age", "age_days": age_days})

    total_cases = len(goldset_cases)
    stale_count = len(stale_cases)
    return {
        "max_age_days": max_age_days,
        "method": "age_proxy",
        "total_cases": total_cases,
        "stale_count": stale_count,
        "stale_ratio": stale_count / total_cases if total_cases > 0 else 0.0,
        "stale_cases": stale_cases,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_recompile.py -v`
Expected: `10 passed`（Task 2 的 `TestThresholdResolution` 4 个 + 本任务 `TestGoldsetStaleness` 6 个）

- [ ] **Step 5: Commit**

```bash
git add eval/staleness.py tests/test_recompile.py
git commit -m "feat: add eval/staleness.py — goldset age-based staleness proxy"
```

---

### Task 4: `eval/drift.py` — history.jsonl 滑动窗口漂移检测

**Files:**
- Create: `eval/drift.py`
- Test: `tests/test_recompile.py` (追加 `TestMetricDrift`)

**关键正确性点（写代码前务必确认）**：`history.jsonl` 用的是指标原始 key（如 `avg_latency_ms`），但 `eval/thresholds.yaml` 里 `avg_latency_ms` 的阈值实际存在 `cost_latency_proxy` 这个 key 下（见 `eval/metric_registry.py` 里 `MetricEntry(key="avg_latency_ms", ..., thresholds_key="cost_latency_proxy", ...)`）。必须复用 `METRIC_REGISTRY` 里已有的 `key → thresholds_key` 映射，不能假设两者同名，否则 `avg_latency_ms` 的阈值解析会静默查到不存在的 key、退化成毫无意义的 `DEFAULT_THRESHOLDS`（[0,1] 范围对毫秒值来说恒为 red）。

**第二个关键正确性点（写代码前务必确认）**：`drift.py` 要读 `agent_cfg.get("_profile", {}).get("threshold_presets", {})` 来支持 vertical preset 这一级——这条取值路径目前没有任何测试覆盖过。如果 key 名猜错，`.get()` 会静默返回空 dict，drift.py 会悄悄退化成"只有 global 阈值"，不会报错也不会被现有测试发现。写代码前先验证这两个 key 真实存在。

- [ ] **Step 1: 验证 `_profile` / `threshold_presets` 的真实 key 名**

Run:
```bash
grep -n '_profile\[' eval/*.py; grep -n '"_profile"\|threshold_presets' eval/metrics.py eval/snapshot.py; cat verticals/legal-compliance/profile.yaml
```
Expected：`eval/metrics.py::load_agents()` 里能看到 `a["_profile"] = yaml.safe_load(f)`；`eval/snapshot.py::build_snapshot()` 里能看到 `profile.get("threshold_presets", {})`；`verticals/legal-compliance/profile.yaml` 里能看到顶层 `threshold_presets:` 节，其中 `hitl_trigger_rate: {green: [0.0, 0.7], yellow: [0.7, 0.85], red: [0.85, 1.0]}`。确认这三处一致后再继续——这组值会被下面的测试直接复用。

- [ ] **Step 2: 写失败测试**

在 `tests/test_recompile.py` 追加：

```python
from eval.drift import detect_metric_drift

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
```

- [ ] **Step 3: 运行确认失败**

Run: `uv run pytest tests/test_recompile.py::TestMetricDrift -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'eval.drift'`

- [ ] **Step 4: 实现 `eval/drift.py`**

```python
"""Metric drift detection — sliding-window comparison over reports/history.jsonl.

Only catches level-crossing drift (e.g. green -> yellow). A metric sliding
slowly within one color band (98% -> 96% -> 95.5%, always green) is a known
blind spot in v1 and is not detected until it actually crosses a boundary.
Acceptable for now — add slope detection only if real data shows in-band
drift is common; speculative to build before that's known.
"""

from __future__ import annotations

from eval.metric_registry import METRIC_REGISTRY
from eval.snapshot import threshold_status
from eval.threshold_resolution import resolve_threshold_cfg

_HISTORY_METRIC_KEYS = frozenset({
    "task_completion_rate",
    "accuracy_proxy",
    "hitl_trigger_rate",
    "guardrail_block_rate",
    "silent_failure_rate",
    "avg_latency_ms",
})

# metric key (as stored in history.jsonl) -> thresholds.yaml lookup key.
# These differ for avg_latency_ms (thresholds_key="cost_latency_proxy") —
# reuse METRIC_REGISTRY's mapping so this never drifts out of sync with it.
_THRESHOLDS_KEY_MAP = {
    entry.key: entry.thresholds_key
    for entry in METRIC_REGISTRY
    if entry.key in _HISTORY_METRIC_KEYS
}

_SEVERITY = {"green": 0, "yellow": 1, "red": 2}


def detect_metric_drift(
    history_entries: list[dict],
    agent_name: str,
    window_size: int,
    thresholds: dict,
    agent_cfg: dict,
) -> dict:
    agent_entries = [
        (e["date"], e["agents"][agent_name])
        for e in history_entries
        if agent_name in e.get("agents", {})
    ]
    k = len(agent_entries)
    required = 2 * window_size

    if k < required:
        return {
            "window_size": window_size,
            "insufficient_history": True,
            "history_count": k,
            "required_count": required,
            "events": [],
            "improvements": [],
        }

    old_slice = agent_entries[k - required : k - window_size]
    new_slice = agent_entries[k - window_size :]

    overrides = agent_cfg.get("thresholds_override", {})
    vertical_presets = agent_cfg.get("_profile", {}).get("threshold_presets", {})
    global_thresholds = thresholds["metrics"]

    events = []
    improvements = []
    for metric_key, thresholds_key in _THRESHOLDS_KEY_MAP.items():
        old_avg = sum(v[metric_key] for _, v in old_slice) / window_size
        new_avg = sum(v[metric_key] for _, v in new_slice) / window_size

        cfg = resolve_threshold_cfg(thresholds_key, global_thresholds, overrides, vertical_presets)
        old_status = threshold_status(old_avg, cfg)
        new_status = threshold_status(new_avg, cfg)

        if _SEVERITY[new_status] == _SEVERITY[old_status]:
            continue

        record = {
            "metric": metric_key,
            "old_status": old_status,
            "new_status": new_status,
            "old_avg": old_avg,
            "new_avg": new_avg,
            "old_window_span": {"from": old_slice[0][0], "to": old_slice[-1][0]},
            "new_window_span": {"from": new_slice[0][0], "to": new_slice[-1][0]},
        }
        if _SEVERITY[new_status] > _SEVERITY[old_status]:
            events.append(record)
        else:
            improvements.append(record)

    return {
        "window_size": window_size,
        "insufficient_history": False,
        "history_count": k,
        "required_count": required,
        "events": events,
        "improvements": improvements,
    }
```

- [ ] **Step 5: 运行确认通过**

Run: `uv run pytest tests/test_recompile.py::TestMetricDrift -v`
Expected: `7 passed`（含新增的 `test_vertical_preset_respected`）

- [ ] **Step 6: 运行全文件确认累计通过数**

Run: `uv run pytest tests/test_recompile.py -v`
Expected: `17 passed`（Task 2 的 4 + Task 3 的 6 + 本任务的 7）

- [ ] **Step 7: Commit**

```bash
git add eval/drift.py tests/test_recompile.py
git commit -m "feat: add eval/drift.py — sliding-window metric drift detection

Covers all three threshold-resolution tiers (global / vertical preset /
agent override) via dedicated tests, not just agent override — the
vertical-preset code path (agent_cfg['_profile']['threshold_presets'])
had no test coverage anywhere in the codebase before this."
```

---

### Task 5: `eval/regulation_staleness.py` — 模式三契约（契约先行，不做真实扫描）

**Files:**
- Create: `eval/regulation_staleness.py`
- Test: `tests/test_recompile.py` (追加 `TestRegulationStaleness`)

- [ ] **Step 1: 写失败测试**

在 `tests/test_recompile.py` 追加：

```python
from eval.regulation_staleness import scan_regulation_staleness


class TestRegulationStaleness:
    def test_not_instrumented_when_field_absent(self):
        entries = [{"case_id": "M001", "decision": "auto_approved"}]
        result = scan_regulation_staleness(entries, id_field="case_id", today=date(2026, 7, 4))
        assert result == {"instrumented": False}

    def test_not_instrumented_when_no_entries(self):
        result = scan_regulation_staleness([], id_field="case_id", today=date(2026, 7, 4))
        assert result == {"instrumented": False}

    def test_instrumented_and_clean(self):
        entries = [{
            "case_id": "M001",
            "regulation_refs": [{"id": "反垄断合规", "effective_until": "2027-01-01"}],
        }]
        result = scan_regulation_staleness(entries, id_field="case_id", today=date(2026, 7, 4))
        assert result == {"instrumented": True, "stale_count": 0, "stale_refs": []}

    def test_instrumented_and_stale_found(self):
        entries = [{
            "case_id": "M002",
            "regulation_refs": [{"id": "反垄断合规", "effective_until": "2025-01-01"}],
        }]
        result = scan_regulation_staleness(entries, id_field="case_id", today=date(2026, 7, 4))
        assert result["instrumented"] is True
        assert result["stale_count"] == 1
        assert result["stale_refs"] == [
            {"case_id": "M002", "regulation_id": "反垄断合规", "effective_until": "2025-01-01"}
        ]

    def test_respects_id_field_param(self):
        """contract-approval-agent uses contract_id, not case_id, as its raw id field."""
        entries = [{
            "contract_id": "C005",
            "regulation_refs": [{"id": "关联交易审查", "effective_until": "2025-06-01"}],
        }]
        result = scan_regulation_staleness(entries, id_field="contract_id", today=date(2026, 7, 4))
        assert result["stale_refs"][0]["case_id"] == "C005"
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_recompile.py::TestRegulationStaleness -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'eval.regulation_staleness'`

- [ ] **Step 3: 实现 `eval/regulation_staleness.py`**

```python
"""Regulation staleness — contract definition only, no real scan yet.

This module defines the data contract for detecting expired-regulation
citations (roadmap.md gap 1, item 4 / "Mode 3"). It is NOT implemented
against real data this round: compliance-review-agent's regulation
library (data/regulations/*.md) has no IDs or effective dates, and its
audit_log only records a hit count ("found N regulations"), not which
regulation was matched. Building a real scan against that would only
produce fake data.

To enable this contract:
  1. compliance-review-agent's regulation library gets structured IDs
     and effective_until dates (currently plain prose .md files)
  2. Its audit_log entries carry a `regulation_refs` field:
     {"regulation_refs": [{"id": "反垄断合规", "effective_until": "2027-01-01"}]}

Both are out of scope for this repo (workbench only reads agent repos)
and are tracked as a separate follow-on in docs/roadmap.md.

Until then, scan_regulation_staleness() returns {"instrumented": False}
for any audit_log where no entry carries regulation_refs — this is a
deliberately distinct state from "instrumented and clean" (0 stale
citations found). Collapsing "not instrumented" into "0 findings" would
be a false green light, the same class of error as the M013 confidence
calibration case documented in README.md.
"""

from __future__ import annotations

from datetime import date


def scan_regulation_staleness(
    audit_log_entries: list[dict],
    id_field: str,
    today: date,
) -> dict:
    if not any("regulation_refs" in entry for entry in audit_log_entries):
        return {"instrumented": False}

    stale_refs = []
    for entry in audit_log_entries:
        refs = entry.get("regulation_refs")
        if not refs:
            continue
        case_id = str(entry.get(id_field, ""))
        for ref in refs:
            effective_until = date.fromisoformat(ref["effective_until"])
            if effective_until < today:
                stale_refs.append({
                    "case_id": case_id,
                    "regulation_id": ref["id"],
                    "effective_until": ref["effective_until"],
                })

    return {"instrumented": True, "stale_count": len(stale_refs), "stale_refs": stale_refs}
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_recompile.py -v`
Expected: `22 passed`（Task 2 的 4 + Task 3 的 6 + Task 4 的 7 + 本任务 `TestRegulationStaleness` 5 个）

- [ ] **Step 5: Commit**

```bash
git add eval/regulation_staleness.py tests/test_recompile.py
git commit -m "feat: add eval/regulation_staleness.py — Mode 3 contract, not_instrumented-aware

Contract-only this round. Sibling repo (compliance-review-agent) lacks
regulation IDs/dates and doesn't record which regulation an audit_log
entry cited — building a real scan now would only produce fake data.
See docs/roadmap.md 2026-07-04 note."
```

---

### Task 6: `eval/recompile_report.py` — 汇总 + 渲染

**Files:**
- Create: `eval/recompile_report.py`
- Test: `tests/test_recompile.py` (追加 `TestRecompileReport`)

- [ ] **Step 1: 写失败测试**

在 `tests/test_recompile.py` 追加：

```python
from eval.recompile_report import build_recompile_snapshot, render_recompile_markdown


def _agent_report(name="test-agent", stale_count=0, drift_events=None, instrumented=False):
    return {
        "name": name,
        "goldset_staleness": {
            "max_age_days": 90, "method": "age_proxy", "total_cases": 10,
            "stale_count": stale_count, "stale_ratio": stale_count / 10,
            "stale_cases": [{"id": "X001", "reason": "age", "age_days": 100}] if stale_count else [],
        },
        "metric_drift": {
            "window_size": 5, "insufficient_history": False,
            "history_count": 10, "required_count": 10,
            "events": drift_events or [], "improvements": [],
        },
        "regulation_staleness": (
            {"instrumented": True, "stale_count": 0, "stale_refs": []}
            if instrumented else {"instrumented": False}
        ),
    }


class TestRecompileReport:
    def test_schema_version_present(self):
        snapshot = build_recompile_snapshot([_agent_report()], today=date(2026, 7, 4))
        assert snapshot["schema_version"] == "1.0"
        assert snapshot["date"] == "2026-07-04"

    def test_summary_aggregates_across_agents(self):
        drift_event = {
            "metric": "hitl_trigger_rate", "old_status": "green", "new_status": "yellow",
            "old_avg": 0.2, "new_avg": 0.5,
            "old_window_span": {"from": "2026-07-01", "to": "2026-07-02"},
            "new_window_span": {"from": "2026-07-03", "to": "2026-07-04"},
        }
        agents = [
            _agent_report("agent-a", stale_count=2, drift_events=[drift_event], instrumented=True),
            _agent_report("agent-b", stale_count=0, drift_events=[], instrumented=False),
        ]
        snapshot = build_recompile_snapshot(agents, today=date(2026, 7, 4))
        assert snapshot["summary"]["total_stale_cases"] == 2
        assert snapshot["summary"]["total_drift_events"] == 1
        assert snapshot["summary"]["agents_instrumented_for_regulation_staleness"] == 1

    def test_render_no_drift_no_stale_says_so_explicitly(self):
        snapshot = build_recompile_snapshot([_agent_report()], today=date(2026, 7, 4))
        md = render_recompile_markdown(snapshot)
        assert "当前无过期 case" in md
        assert "当前无漂移" in md
        assert "未接入检测" in md

    def test_render_includes_agent_name_and_stale_case(self):
        snapshot = build_recompile_snapshot(
            [_agent_report("contract-approval-agent", stale_count=1)], today=date(2026, 7, 4)
        )
        md = render_recompile_markdown(snapshot)
        assert "contract-approval-agent" in md
        assert "X001" in md
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_recompile.py::TestRecompileReport -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'eval.recompile_report'`

- [ ] **Step 3: 实现 `eval/recompile_report.py`**

```python
"""Aggregate staleness + drift + regulation_staleness into one report.

Compute/render split, mirroring eval/snapshot.py (compute) and
eval/metrics.py::render_markdown (render) for the dashboard.

recompile_triggers_<date>.json is a new compiled-artifact contract in its
own right (schema_version included) — a future conformance checker would
validate against it the same way it would validate trace_log.jsonl.
"""

from __future__ import annotations

from datetime import date

SCHEMA_VERSION = "1.0"


def build_recompile_snapshot(agent_reports: list[dict], today: date) -> dict:
    total_stale_cases = sum(a["goldset_staleness"]["stale_count"] for a in agent_reports)
    total_drift_events = sum(len(a["metric_drift"]["events"]) for a in agent_reports)
    agents_instrumented = sum(
        1 for a in agent_reports if a["regulation_staleness"]["instrumented"]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "date": today.isoformat(),
        "summary": {
            "total_stale_cases": total_stale_cases,
            "total_drift_events": total_drift_events,
            "agents_instrumented_for_regulation_staleness": agents_instrumented,
        },
        "agents": agent_reports,
    }


def render_recompile_markdown(snapshot: dict) -> str:
    lines = [f"# 重编译触发报告 — {snapshot['date']}", ""]
    s = snapshot["summary"]
    lines.append(
        f"**总览**：过期 case {s['total_stale_cases']} 个，漂移事件 {s['total_drift_events']} 个，"
        f"{s['agents_instrumented_for_regulation_staleness']} 个 agent 已接入模式三检测。"
    )
    lines.append("")

    for agent in snapshot["agents"]:
        lines.append(f"## {agent['name']}")
        lines.append("")

        gs = agent["goldset_staleness"]
        lines.append(f"### Goldset 时效性（过期阈值 {gs['max_age_days']} 天，age proxy）")
        if gs["stale_count"] == 0:
            lines.append("当前无过期 case。")
        else:
            lines.append(f"过期 {gs['stale_count']}/{gs['total_cases']}（{gs['stale_ratio']:.1%}）：")
            for c in gs["stale_cases"]:
                if c["reason"] == "missing_last_verified":
                    lines.append(f"- {c['id']}：缺少 last_verified 字段")
                else:
                    lines.append(f"- {c['id']}：{c['age_days']} 天未复验")
        lines.append("")

        md = agent["metric_drift"]
        lines.append(f"### 指标漂移（窗口 N={md['window_size']}）")
        if md["insufficient_history"]:
            lines.append(f"历史数据不足（{md['history_count']}/{md['required_count']} 条），暂不比较。")
        elif not md["events"] and not md["improvements"]:
            lines.append("当前无漂移。")
        else:
            for e in md["events"]:
                lines.append(
                    f"- 🔴 **{e['metric']}**：{e['old_status']}→{e['new_status']}"
                    f"（{e['old_avg']:.3f}→{e['new_avg']:.3f}，"
                    f"{e['old_window_span']['from']}~{e['old_window_span']['to']} → "
                    f"{e['new_window_span']['from']}~{e['new_window_span']['to']}）"
                )
            for i in md["improvements"]:
                lines.append(f"- 🟢 improvement：{i['metric']} {i['old_status']}→{i['new_status']}")
        lines.append("")

        rs = agent["regulation_staleness"]
        lines.append("### 模式三：法规时效失效")
        if not rs["instrumented"]:
            lines.append("未接入检测（audit_log 未输出 regulation_refs 字段，契约已定义见 eval/regulation_staleness.py）。")
        elif rs["stale_count"] == 0:
            lines.append("已接入检测，当前无过期法规引用。")
        else:
            lines.append(f"发现 {rs['stale_count']} 条过期法规引用：")
            for ref in rs["stale_refs"]:
                lines.append(f"- case {ref['case_id']} 引用 {ref['regulation_id']}（已于 {ref['effective_until']} 失效）")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "已知盲区：指标漂移检测只能抓「越级」变化（如 green→yellow），"
        "同一色带内的缓慢滑坡（如 98%→96%→95.5%，一直是 green）测不到。"
    )

    return "\n".join(lines)
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_recompile.py -v`
Expected: `26 passed`（Task 2 的 4 + Task 3 的 6 + Task 4 的 7 + Task 5 的 5 + 本任务 `TestRecompileReport` 4 个）

- [ ] **Step 5: Commit**

```bash
git add eval/recompile_report.py tests/test_recompile.py
git commit -m "feat: add eval/recompile_report.py — snapshot build + markdown render"
```

---

### Task 7: goldset 回填 `last_verified` + 文档

**Files:**
- Modify: `goldset/contract-approval-agent.yaml` (11 处)
- Modify: `goldset/compliance-review-agent.yaml` (10 处)
- Modify: `goldset/README.md`

- [ ] **Step 1: 给 `goldset/contract-approval-agent.yaml` 每条 case 加 `last_verified`**

对文件里每一条 case，在 `source: synthetic` 行后面加一行 `last_verified: "2026-07-04"`。例如把：

```yaml
  - id: C001
    expected_route: auto
    failure_mode: routing_error
    source: synthetic
    note: 无风险信号的标准合同，应自动审批
```

改成：

```yaml
  - id: C001
    expected_route: auto
    failure_mode: routing_error
    source: synthetic
    last_verified: "2026-07-04"
    note: 无风险信号的标准合同，应自动审批
```

对 C001 到 C011 共 11 条 case 都做同样的插入（`source: synthetic` 之后、`note:` 之前）。

- [ ] **Step 2: 给 `goldset/compliance-review-agent.yaml` 每条 case 加 `last_verified`**

同样的插入方式，对 M001 到 M010 共 10 条 case 都处理。

- [ ] **Step 3: 验证两个文件仍能被正确加载，且 `last_verified` 字段可读**

Run:
```bash
uv run python -c "
from eval.metrics import load_agents
agents = load_agents()
for a in agents:
    goldset = a['_goldset']
    missing = [cid for cid, c in goldset.items() if 'last_verified' not in c]
    print(a['name'], len(goldset), 'cases, missing last_verified:', missing)
"
```
Expected: 两个 agent 都打印 `missing last_verified: []`，`contract-approval-agent` 显示 11 cases，`compliance-review-agent` 显示 10 cases。

- [ ] **Step 4: 更新 `goldset/README.md`，记录字段说明和回填语义**

在 `goldset/README.md` 的 "## Case 格式" 代码块里，把：

```yaml
cases:
  - id: <case_id>           # 与 audit_log 中的 case_id 对应
    expected_route: <route>  # 期望路由：auto | hitl | block
    failure_mode: <mode>     # 该 case 防守的失败模式
    source: <source>         # synthetic | historical
    note: <string>           # 该 case 在集合中的理由
```

改成：

```yaml
cases:
  - id: <case_id>           # 与 audit_log 中的 case_id 对应
    expected_route: <route>  # 期望路由：auto | hitl | block
    failure_mode: <mode>     # 该 case 防守的失败模式
    source: <source>         # synthetic | historical
    last_verified: <date>    # 上次人工复验日期，YYYY-MM-DD，见下方说明
    note: <string>           # 该 case 在集合中的理由
```

并在该代码块后面（"## failure_mode 枚举" 之前）新增一节：

```markdown
## last_verified 字段

`last_verified` 距今超过 `eval/thresholds.yaml` 里 `staleness.goldset_case_max_age_days`
（当前 90 天）即被 `eval/staleness.py` 标记为过期，出现在
`reports/recompile_triggers_<date>.md` 的过期列表里。缺失该字段的 case 直接视为过期——
不能假设没填等于新鲜。

**当前是纯 age-based proxy**：只比较"距上次复验过了多少天"，不比较"规则/法规是否真的变了"
——本仓库目前没有可靠的规则变更追踪数据源，构造一个是投机性建设。这个方向保留在这里作为
以后可能的扩展，现在不建。

**2026-07-04 回填语义**：21 条 case 的 `last_verified` 统一回填为 `2026-07-04`——这是
"字段引入日"，不代表这天真的对每条 case 做了复验。90 天后（约 2026-10-02）第一批过期告警
出现时，不要把这个日期误读成"有人验证过"；从字段引入到那天之前，这些 case 从未被真正复验过，
只是新鲜度指标还没跨过阈值而已。
```

- [ ] **Step 5: Commit**

```bash
git add goldset/contract-approval-agent.yaml goldset/compliance-review-agent.yaml goldset/README.md
git commit -m "chore: backfill goldset last_verified field (2026-07-04, field-introduction date)"
```

---

### Task 8: `scripts/run_recompile_check.py` — 编排脚本

**Files:**
- Create: `scripts/run_recompile_check.py`

- [ ] **Step 1: 实现脚本**

```python
#!/usr/bin/env python3
"""Run recompile trigger check — goldset staleness + metric drift + regulation staleness."""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.drift import detect_metric_drift
from eval.metrics import load_agents, load_thresholds
from eval.recompile_report import build_recompile_snapshot, render_recompile_markdown
from eval.regulation_staleness import scan_regulation_staleness
from eval.staleness import compute_goldset_staleness

REPORTS_DIR = Path(__file__).parent.parent / "reports"
HISTORY_PATH = REPORTS_DIR / "history.jsonl"


def _load_history_entries() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    entries = []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def _load_audit_log(agent_cfg: dict) -> list[dict]:
    audit_path = agent_cfg["repo"] / "data" / "audit_log.jsonl"
    if not audit_path.exists():
        return []
    entries = []
    with open(audit_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def main():
    today = date.today()
    thresholds = load_thresholds()
    agents = load_agents()
    history_entries = _load_history_entries()

    max_age_days = thresholds["staleness"]["goldset_case_max_age_days"]
    window_size = thresholds["drift"]["window_size"]

    agent_reports = []
    for agent_cfg in agents:
        goldset_cases = list(agent_cfg.get("_goldset", {}).values())
        staleness_result = compute_goldset_staleness(goldset_cases, max_age_days, today)

        drift_result = detect_metric_drift(
            history_entries, agent_cfg["name"], window_size, thresholds, agent_cfg
        )

        audit_entries = _load_audit_log(agent_cfg)
        id_field = agent_cfg.get("field_map", {}).get("case_id", "case_id")
        regulation_result = scan_regulation_staleness(audit_entries, id_field, today)

        agent_reports.append({
            "name": agent_cfg["name"],
            "goldset_staleness": staleness_result,
            "metric_drift": drift_result,
            "regulation_staleness": regulation_result,
        })

    snapshot = build_recompile_snapshot(agent_reports, today)
    md_output = render_recompile_markdown(snapshot)

    REPORTS_DIR.mkdir(exist_ok=True)
    date_str_file = datetime.now().strftime("%Y%m%d")
    json_path = REPORTS_DIR / f"recompile_triggers_{date_str_file}.json"
    md_path = REPORTS_DIR / f"recompile_triggers_{date_str_file}.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_output)

    print(md_output)
    print(f"\n重编译触发报告已写入: {md_path}")
    print(f"快照 JSON 已写入: {json_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 手动跑一次，确认端到端可运行**

Run: `uv run python scripts/run_recompile_check.py`
Expected: 打印 markdown 报告到 stdout，无异常；两个 agent 的 `metric_drift.insufficient_history` 应为 `true`（当前 `history.jsonl` 只有 3 条同日记录，< 2×5=10，这是预期状态）；`goldset_staleness.stale_count` 应为 0（Task 7 刚把所有 case 的 `last_verified` 回填为今天）；`regulation_staleness.instrumented` 应为 `false`（两个 demo agent 都未实现该字段）。

- [ ] **Step 3: 检查生成的文件**

Run: `cat reports/recompile_triggers_$(date +%Y%m%d).json | python3 -m json.tool | head -20`
Expected: 合法 JSON，含 `schema_version: "1.0"`。

- [ ] **Step 4: Commit**

```bash
git add scripts/run_recompile_check.py
git commit -m "feat: add scripts/run_recompile_check.py — orchestrate recompile trigger report"
```

不提交本次手动运行生成的 `reports/recompile_triggers_<date>.{json,md}`——是否把每次运行的报告纳入版本控制，和现有 `reports/dashboard_*.md` 的处理方式保持一致，交给使用者决定（当前 repo 里 dashboard 报告是提交的，如果沿用同一习惯可以一并 `git add reports/`）。

---

### Task 9: README.md 更新

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 在"模块二"之后、"## 项目结构"之前插入"模块三"小节**

找到 README.md 里这一段（模块二"如何跑"结尾）：

```markdown
依赖 `eval/agents.yaml` 中注册的各 agent repo 的 `data/audit_log.jsonl` 与 pytest（只读，不修改那些 repo）。每次运行会向 `reports/history.jsonl`（指标级）和 `reports/case_history.jsonl`（case 级，供稳定性指标使用）各追加一次记录。

## 项目结构
```

改成：

```markdown
依赖 `eval/agents.yaml` 中注册的各 agent repo 的 `data/audit_log.jsonl` 与 pytest（只读，不修改那些 repo）。每次运行会向 `reports/history.jsonl`（指标级）和 `reports/case_history.jsonl`（case 级，供稳定性指标使用）各追加一次记录。

## 模块三：重编译触发层

### 设计理由

仪表盘回答"现在健康吗"，这一层回答"哪些编译产物需要重新验证"——两个不同的问题，对应 `docs/roadmap.md` 缺口一。三个信号：

1. **Goldset 时效性**——`last_verified` 距今超过阈值（当前 90 天）的 case 视为过期，缺失该字段直接计入过期（不能假设没填等于新鲜）。纯 age-based proxy，不比对真实规则变更日期（无可靠数据源，构造一个是投机性建设）。
2. **指标漂移**——`reports/history.jsonl` 滑动窗口对比：最近 N 次运行 vs 紧邻其前的 N 次（N=5，与路由稳定性的 k=5 一致），任一核心指标越级变色（如 green→yellow）记为漂移事件。只抓跨级变化，同一色带内的缓慢滑坡是已知盲区，暂不做斜率检测。
3. **模式三（法规时效失效）——契约先行，未实装**：探查发现 sibling repo（`compliance-review-agent`）的法规库无 ID/生效日期、audit_log 不记录命中的具体法规，两个前提都不满足时做真实扫描只会产出假数据。`eval/regulation_staleness.py` 只定义 `regulation_refs` 字段契约和一个区分"未接入检测"与"已检测无异常"的扫描器（前者绝不能显示成后者——那是假绿灯，和 M013 案例同一类错误）。

### 如何跑

```bash
uv run python scripts/run_recompile_check.py
```

输出 `reports/recompile_triggers_<date>.json`（新的编译产物契约，带 `schema_version`）和 `.md`。依赖 `reports/history.jsonl` 和各 goldset 文件，只读不改动 agent 仓库。

## 项目结构
```

- [ ] **Step 2: 更新"项目结构"树，补充新文件**

找到：

```
├── eval/
│   ├── agents.yaml              ← agent 注册表：字段/路由映射、风险规则、goldset 引用、阈值覆盖
│   ├── metrics.py               ← 事件规范化 + 指标计算
│   ├── snapshot.py              ← 计算层：产出 JSON 快照（渲染与计算分离）
│   ├── metric_registry.py       ← 指标注册表（含 CLASSIC 五维归类）
│   ├── trace.py                 ← 轨迹评估的 schema 契约（预留）
│   └── thresholds.yaml          ← 全局红/黄/绿阈值 + 理由
├── goldset/                     ← 黄金测试集：每 case 带失败模式与来源标注
├── verticals/
│   └── legal-compliance/        ← 垂类 profile：权重覆盖、阈值 preset、风险规则模板
├── scripts/
│   ├── run_scorer.py            ← 场景评分（--scenario / --profile）
│   ├── run_dashboard.py         ← 生成快照 + 仪表盘 + 历史
│   └── build_site.py            ← 把最新快照注入 docs/index.html
```

改成：

```
├── eval/
│   ├── agents.yaml              ← agent 注册表：字段/路由映射、风险规则、goldset 引用、阈值覆盖
│   ├── metrics.py               ← 事件规范化 + 指标计算
│   ├── snapshot.py              ← 计算层：产出 JSON 快照（渲染与计算分离）
│   ├── metric_registry.py       ← 指标注册表（含 CLASSIC 五维归类）
│   ├── trace.py                 ← 轨迹评估的 schema 契约（预留）
│   ├── thresholds.yaml          ← 全局红/黄/绿阈值 + 理由，含 staleness/drift 配置
│   ├── threshold_resolution.py  ← 阈值优先级解析（agent override > vertical preset > global），snapshot.py 与 drift.py 共用
│   ├── staleness.py             ← goldset case age-based 过期检测
│   ├── drift.py                 ← history.jsonl 滑动窗口漂移检测
│   ├── regulation_staleness.py  ← 模式三契约定义（未实装真实扫描）
│   └── recompile_report.py      ← 重编译触发报告：计算层 + 渲染层
├── goldset/                     ← 黄金测试集：每 case 带失败模式、来源、last_verified 标注
├── verticals/
│   └── legal-compliance/        ← 垂类 profile：权重覆盖、阈值 preset、风险规则模板
├── scripts/
│   ├── run_scorer.py            ← 场景评分（--scenario / --profile）
│   ├── run_dashboard.py         ← 生成快照 + 仪表盘 + 历史
│   ├── run_recompile_check.py   ← 生成重编译触发报告
│   └── build_site.py            ← 把最新快照注入 docs/index.html
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document recompile trigger layer in README"
```

---

### Task 10: 全量回归 + 收尾检查

**Files:** 无新增/修改，纯验证

- [ ] **Step 1: 跑全量测试套件**

Run: `uv run pytest tests/ -v`
Expected: 全部通过，`87 passed`（基线 61 + `tests/test_recompile.py` 新增 26：Task 2 的 `TestThresholdResolution` 四个 + Task 3 的 `TestGoldsetStaleness` 六个 + Task 4 的 `TestMetricDrift` 七个 + Task 5 的 `TestRegulationStaleness` 五个 + Task 6 的 `TestRecompileReport` 四个）。

- [ ] **Step 2: 确认 `run_dashboard.py` 未被本次改动影响**

Run: `uv run python scripts/run_dashboard.py`
Expected: 正常输出 dashboard，无异常——证明 Task 2 的 `snapshot.py` 重构没有破坏既有仪表盘链路。

- [ ] **Step 3: 确认 `run_recompile_check.py` 仍可正常运行**

Run: `uv run python scripts/run_recompile_check.py`
Expected: 正常输出，无异常。

- [ ] **Step 4: 检查 git log，确认提交顺序清晰**

Run: `git log --oneline -10`
Expected: 依次看到 10 个提交：最早是执行前提交的本计划文件本身（"计划先于执行"），然后是 Task 1-9 的 9 个提交（config → refactor → staleness → drift → regulation_staleness → recompile_report → goldset backfill → script → README），每个提交信息独立可读。

---

## 已知的范围外事项（不在本计划内，供后续参考）

- **`eval/metrics.py::_threshold_label()` 与 `eval/snapshot.py::threshold_status()` 是两套独立实现的相同判定逻辑**（前者输出 emoji 供 `render_markdown` 用，后者输出语义字符串供 `build_snapshot` 用）。写这份计划时发现的既有重复，不影响本次任务（drift.py 只需要 `threshold_status`），但和本层想抓的"两套系统对同一件事判断不一致"是同一类风险。是否合并留给单独的后续任务。
- sibling repo（`compliance-review-agent`）法规库结构化 + audit_log 输出 `regulation_refs`——`docs/roadmap.md` 缺口一第 4 项的后续依赖项，需要在那个仓库里单独排期。
