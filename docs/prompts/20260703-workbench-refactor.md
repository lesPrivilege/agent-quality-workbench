# Prompt: agent-quality-workbench 重构（分阶段）

> 交给 CLI coding agent 执行。仓库：`~/Projects/agent-quality-workbench`。
> 按 Phase 顺序执行，每个 Phase 结束跑一次验收再进入下一个。
> 不要触碰 `~/Projects/contract-approval-agent` 和 `~/Projects/compliance-review-agent`（只读消费）。
> 依赖约束：运行时仅 pyyaml，dev 仅 pytest。保留现有 CLI 入口（`scripts/run_scorer.py`、`scripts/run_dashboard.py`）和中文 label。

## 背景

本仓库是 PM 侧的 agent 评估工作台，包含两个工具：

1. **Complexity Ladder Scorer**（`rubric/complexity_ladder.yaml` + `scripts/run_scorer.py`）：给候选场景打分，判断是否值得上 agent、上什么复杂度。
2. **质量仪表盘**（`eval/metrics.py` + `eval/thresholds.yaml` + `scripts/run_dashboard.py`）：解析两个 demo agent 仓库的 audit_log + pytest 结果，产出红黄绿仪表盘。

复盘发现的核心问题：与两个 demo repo 硬耦合、策略写死在代码里、accuracy 指标单向、若干渲染/健壮性缺陷。目标是把它改造成"注册新 agent 只需加配置，不改代码"的通用工作台。

---

## Phase 0 — 缺陷修复（不改架构）

**P0-1 仪表盘表格列错位。** `generate_dashboard()` 表头 4 列（指标/值/状态/说明），但数据行只写 3 个单元格（值和状态被合并进同一格）。修正为 4 列对齐：值、状态 emoji、说明各占一列。参照 `reports/dashboard_20260701.md` 确认修复后渲染正确。

**P0-2 `_run_pytest` 静默失败。** 异常时返回 `(0,0,0)`，导致完成率静默显示 0%——评估工具自己制造 silent failure。改为：捕获异常后在返回值中区分"跑不起来"和"全挂"（如返回 `None` 并在 `AgentMetrics` 加 `pytest_error: str | None`），仪表盘对应行显示 `⚠️ pytest 未能执行: <原因>` 而不是 0% 红灯。同时检查 `uv` 不存在、repo 路径不存在两种情况。

**P0-3 low_confidence 检测语义不明。** `_detect_silent_failures_contract` 里用 `"low_confidence" in e.get("decision", "")` 做子串匹配，且嵌套循环 O(n²)。先按 contract_id 建一次索引；子串匹配改为对 audit entry 的显式字段判断（若 audit_log 无此字段，删掉这条规则并在 docstring 注明原因）。

**P0-4 rubric 注释与实现不符。** `complexity_ladder.yaml` 头部写 "Score range: 0-10"，实际加权满分 5.0。修正注释；同时把 `[4.2, 5.01]`、`green: [0.95, 1.01]` 这类 hack 边界改为明确的区间语义：最后一档上界为 inclusive（代码里判断 `lo <= x <= hi`），并在 YAML 注释写清边界约定（其余为左闭右开）。

**P0-5 命名。** `AgentMetrics.total_contracts` 在 compliance 场景实际是 materials 数，改名 `total_cases`（相关 `contract_map` 等局部命名一并语义化）。

**验收：** `uv run python scripts/run_dashboard.py` 正常输出且表格 4 列对齐；两个 demo repo 路径改成不存在的路径时，仪表盘显示 ⚠️ 而非 0% 红灯。

---

## Phase 1 — 配置驱动，消除硬耦合

**P1-1 建 `eval/agents.yaml`，注册制取代硬编码。** `parse_contract_audit` / `parse_compliance_audit` 约 90% 重复，仅差 id 字段名、数据文件名、风险规则、期望路由。合并为单个 `parse_agent_audit(agent_cfg)`，所有差异进配置：

```yaml
agents:
  - name: contract-approval-agent
    repo: ~/Projects/contract-approval-agent   # 支持 ~ 展开
    id_field: contract_id
    source_data: contracts.jsonl
    expected_routes:      # ground truth：case → 期望路由
      C001: auto
      C002: hitl
      C003: block
      # ... 现有 expected_auto/block/hitl 三个 set 全部迁移到这里
    risk_rules:           # silent-failure 信号，声明式
      - {path: 关联方标记, equals: true}
      - {path: 条款标记.不可逆, equals: true}
      - {path: 条款标记.担保, equals: true}
  - name: compliance-review-agent
    repo: ~/Projects/compliance-review-agent
    id_field: material_id
    source_data: materials.jsonl
    expected_routes: { M001: auto, M002: hitl, ... }
    risk_rules:
      - {path: 关联方标记, equals: true}
      - all:
          - {path: 涉及数据共享, equals: true}
          - {path: 涉及受监管业务, equals: true}
      - {path: 条款标记.担保, equals: true}
      - all:
          - {path: 内容摘要, contains: 反垄断}
          - {path: 金额, gt: 3000000}
    thresholds_override:  # 见 P1-3
      hitl_trigger_rate: {green: [0.0, 0.7], yellow: [0.7, 0.85], red: [0.85, 1.01]}
```

规则求值器支持算子：`equals` / `contains` / `gt` / `lt`，组合子：`all` / `any`，`path` 支持 `a.b` 点号取嵌套字段。写成独立纯函数 `evaluate_rule(rule, record) -> bool`，便于测试。
在 `agents.yaml` 顶部注释注明：risk_rules 是对各 agent guardrail 逻辑的镜像，存在 drift 风险；长期方向是让 agent 在 audit_log 输出 `risk_signals` 字段、workbench 直接消费，届时删除本节。

**P1-2 accuracy 改为双向 routing accuracy。** 现在 `accuracy_proxy` 只奖励命中、不惩罚多余（compliance 实际 6 次 HITL、期望 4 次，accuracy 仍 100%，与 HITL 红灯自相矛盾）。改为：对 `expected_routes` 中每个 case，从 audit entries 推导实际路由（优先级 blocked > hitl(approved/rejected) > auto_approved），`routing_accuracy = 完全匹配数 / 期望 case 总数`。不匹配的 case 在仪表盘表格下方列出：`case id: 期望 X → 实际 Y`。

**P1-3 阈值支持 per-agent override。** `thresholds.yaml` 保留为全局默认；agent 配置里的 `thresholds_override` 逐指标覆盖。理由：合规 agent 的设计意图就是高 HITL，全局 40% 绿线对它不成立。仪表盘中被 override 的指标在说明列标注"(agent 阈值)"。

**P1-4 延迟指标降级为 placeholder。** demo audit_log 的 duration_ms 全是 0-1ms，阈值没有校准依据。仪表盘保留该行但状态列显示 `⚪ 未校准` 而非绿灯，说明列写"demo 数据，生产环境需替换为 token 计数"。

**P1-5 dashboard 循环遍历 `agents.yaml`**，`run_dashboard.py` 删除对 CONTRACT_REPO/COMPLIANCE_REPO 常量的引用。

**验收：** 输出与 Phase 0 后基线一致（除 accuracy 与延迟两处预期变化）；在 `agents.yaml` 里临时复制一个 agent 条目，仪表盘出现第三个 section 且无需改任何 .py 文件；改回。

---

## Phase 2 — 工具化与趋势

**P2-1 scorer 场景外置。** `run_scorer.py` 的 `SCENARIOS` 硬编码迁移到 `scenarios/*.yaml`（一场景一文件，字段与现有 dict 相同）。CLI：`uv run python scripts/run_scorer.py` 默认跑全部，`--scenario <name>` 跑单个。现有三个场景迁移为三个 YAML 文件。

**P2-2 历史趋势。** 每次 dashboard 运行往 `reports/history.jsonl` append 一行（date + 每 agent 每指标的值）。仪表盘每个指标增加趋势列：与上一次运行相比 `↑ ↓ →`（首轮显示 `-`）。这是 PM 视角的核心价值：单次快照不如"质量是否在劣化"。

**P2-3 workbench 自身测试。** 建 `tests/`（pyproject 已配置 testpaths）。最低覆盖：`evaluate_rule` 各算子与组合子、`_threshold_label` 边界值（含 override）、routing accuracy 推导（构造 fixture audit entries）、`generate_dashboard` 输出的表格列数一致性、rubric 分档边界（0.0 / 1.0 / 5.0 落在哪一档）。全部用 fixture 数据，不依赖两个 demo repo 存在。

**验收：** `uv run pytest` 全绿；连续跑两次 dashboard，第二次出现趋势箭头；`run_scorer.py --scenario quote_generation` 只输出反例。

---

## 通用要求

- 增量修改，不要重写整个文件；保持现有函数签名的对外行为除非上文明确要求改。
- 每个 Phase 单独 commit，message 格式 `phase0: ...` / `phase1: ...` / `phase2: ...`。
- 所有面向 PM 的输出文案保持中文。
- 完成后输出一份简短变更摘要：每个 Phase 改了哪些文件、验收结果。
