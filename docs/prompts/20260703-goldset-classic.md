# Prompt: 社区对齐迭代（Phase 6–7）— Gold Set + CLASSIC 五维 + 稳定性

> 交给 CLI coding agent 执行。仓库：`~/Projects/agent-quality-workbench`，基于 Phase 3–5 完成后状态（commit 9d240eb）。
> 背景：对照社区企业级 agent bench 实践（EnterpriseBench / CRMArena-Pro / CLASSIC / trajectory evals / pass^k），本轮吸收其中与"规则驱动、低耦合 prototype"定位兼容的部分。
> 约束不变：无新运行时依赖；demo repo 只读；无 LLM-as-judge、无实时数据；**docs/index.html 本轮禁止修改**——snapshot 新增字段必须向后兼容（页面渲染脚本忽略未知字段），仅允许重跑 `build_site.py` 刷新数据。
> 每 Phase 验收后单独 commit（`phase6: ...` / `phase7: ...`）。全部完成后：跑 `run_dashboard.py` + `build_site.py`，commit 并 **push 到 origin main**（当前本地已领先 6 个 commit，Pages 等待这次 push 刷新）。

---

## Phase 6 — Gold Set 形式化 + case 级历史 + 稳定性指标

社区共识：用真实历史失败案例反向构建 50–200 条黄金测试集，且生产环境最看重 pass^k（k 次全对）而非 pass@k。本仓库当前的 ground truth 是 `agents.yaml` 里 21 条内联 `expected_routes`——能用，但没有案例元数据，无法回答"这个 case 为什么在集合里"。

**P6-1 Gold Set 独立成文件。** 建 `goldset/<agent-name>.yaml`，每 case 一条记录：

```yaml
# goldset/contract-approval-agent.yaml
cases:
  - id: C003
    expected_route: block
    failure_mode: guardrail_gap      # 该 case 防守的失败模式（枚举见下）
    source: synthetic                # synthetic | historical（真实历史失败案例）
    note: 大额不可逆合同，guardrail 必须拦截
```

`failure_mode` 枚举（写入 goldset/README.md，允许后续扩展）：`routing_error`（该 HITL 的被 auto）、`guardrail_gap`（该 block 的放行）、`over_escalation`（该 auto 的被推给人）、`silent_failure`（风险信号被静默放过）、`calibration`（置信度失真，如 M013）。
`agents.yaml` 的 `expected_routes` 节删除，改为 `goldset: goldset/<name>.yaml`；现有 21 条迁移时补上 failure_mode 与 note（依据两个 demo repo 的 README/数据文件判断，判断不了的标 `source: synthetic` + 保守归类）。
快照与仪表盘新增 Gold Set 覆盖行：按 failure_mode 统计 case 数与命中率，`source: historical` 占比单列——当前为 0%，如实显示，这本身就是对"用真实失败案例充实测试集"的提醒。

**P6-2 case 级运行历史。** 现有 `history.jsonl` 只存指标级数值。新增 `reports/case_history.jsonl`：每次 dashboard 运行为每个 gold case 追加一行 `{date, agent, case_id, expected_route, actual_route}`。

**P6-3 稳定性指标（pass^k 代理）。** 注册表新增 `route_stability`：取每个 case 最近 k=5 次运行（不足则用现有全部，快照注明窗口大小），`稳定 case = k 次 actual_route 完全一致且等于 expected`，`route_stability = 稳定 case 数 / gold case 总数`。阈值：green [0.95,1.0]，yellow [0.85,0.95)，red [0,0.85)。诚实标注：demo agent 是确定性规则实现，此指标当前恒为 100%，其价值在于接入真实 LLM agent（非确定性）后立即生效——把这句写进指标的 rationale。

**验收：** `uv run pytest` 全绿（新增 goldset 加载/failure_mode 枚举校验/route_stability 窗口计算用例）；`agents.yaml` 无 expected_routes 残留；连跑两次 dashboard 后 case_history.jsonl 每 agent 每 case 两行；仪表盘出现 Gold Set 覆盖与稳定性行；index.html 未被修改（`git diff --stat docs/index.html` 为空），重跑 build_site.py 后页面正常渲染。

---

## Phase 7 — CLASSIC 五维打标 + Trace 插槽（只留接口不实现评估）

**P7-1 指标按 CLASSIC 五维归类。** `metric_registry` 每条注册项增加两个字段：

- `dimension`: `cost | latency | accuracy | stability | security`
- `evaluator`: 固定 `rule`（为未来 LLM-judge 预留枚举位，本轮不实现任何 judge 逻辑）

现有指标映射：任务完成率/routing accuracy → accuracy；HITL 触发率 → cost（人工介入是成本）；guardrail 拦截率/silent failure → security；平均延迟 → latency；route_stability → stability。
快照中每指标带 dimension 字段；markdown 渲染层在指标表前加一行五维摘要：`Cost ● | Latency ⚪ | Accuracy ● | Stability ● | Security ●`（该维度下最差指标的状态色，全部 uncalibrated 则 ⚪）。这是 BG 汇报口径：一眼回答"五个企业关心的维度各是什么水位"。
`thresholds.yaml` 顶部注释补一段：五维口径出处（CLASSIC benchmark），以及当前 cost 维度只有 HITL 代理、缺 token 成本的已知缺口。

**P7-2 Trace 插槽（轨迹级评估预留）。** 社区实践要求评估执行路径而非仅最终结果（step efficiency、tool/argument correctness）。当前两个 demo 的 audit_log 只有终态 decision，无步骤事件——因此本轮**只定义契约，不实现计算**：

- `eval/trace.py`（新）：定义 `TraceStep` dataclass（case_id / step_idx / action：tool_call|reason|route / tool_name / args_ok: bool|None / duration_ms）与 `load_traces(agent_cfg) -> list[TraceStep] | None`——从 agent repo 的 `data/trace_log.jsonl` 读取（若存在），字段映射复用 field_map 机制（新增 `trace_field_map` 配置节，可缺省）。
- 注册表新增两个指标：`step_efficiency`（实际步数 / 期望步数上限）与 `tool_arg_correctness`（args_ok 为 true 的比例），dimension 分别为 cost 与 accuracy。trace 数据不存在时状态显示 `⚪ 无 trace 数据`，说明列写"需 agent 输出 data/trace_log.jsonl，schema 见 eval/trace.py"。
- 在 `verticals/README.md` 追加一节"接入 trace 的三步"，同样是预留的正式形态：文档化契约，不是空实现。

**P7-3 README 对齐。** README.md 增加"设计对标"小节（≤15 行）：本工作台与 CLASSIC 五维的映射表、gold set / pass^k 代理 / trace 插槽各自的现状与缺口。语气克制——是 prototype 对社区实践的对齐说明，不是能力宣称。

**验收：** `uv run pytest` 全绿（新增 dimension 完整性校验——注册表每项必须有合法 dimension、trace 缺失时指标降级用例）；仪表盘出现五维摘要行；两个 trace 指标显示 ⚪；index.html 无 diff；最后重跑 dashboard + build_site，commit 并 push，确认 https://lesprivilege.github.io/agent-quality-workbench/ 数据日期刷新（Pages 部署有几分钟延迟，用页脚快照日期为准）。

---

## 本轮明确不吸收的社区方向（写进 commit message 或 README 皆可，避免后续重复讨论）

- **EnterpriseBench 式仿真沙盒**：那是评测通用模型能力的横向 bench；本工作台是评自家 agent 的纵向质检，方向不同。
- **OSWorld / WebArena GUI 评估**：两个 demo 均非 GUI agent，不适用。
- **LLM-as-a-Judge**：与"无 LLM 依赖、确定性可复现"的 prototype 原则冲突；`evaluator` 枚举位已预留，等有真实需要再启用。
