# Prompt: workbench 泛化迭代（Phase 3–5）

> 交给 CLI coding agent 执行。仓库：`~/Projects/agent-quality-workbench`，基于 Phase 0–2 完成后的状态（commit db06dd9）。
> 按 Phase 顺序执行，每 Phase 验收后单独 commit（`phase3: ...` 等）。
> 定位：BG 向多垂类评估 bench 的 prototype。原则：通用底层先行，垂类只"预留插槽"不铺开；保持低耦合、可增量迭代；不引入新运行时依赖（仅 pyyaml + pytest）；两个 demo repo 仍只读。
> **明确不做**：实时数据接入、LLM-as-judge、Web 服务端。Demo 页永远是静态快照。

---

## Phase 3 — 通用底层：schema 契约 + 计算/渲染分离

**P3-1 清理死代码。** `eval/metrics.py`（681 行）中 `parse_contract_audit`、`parse_compliance_audit`、`_detect_silent_failures_contract`、`_detect_silent_failures_compliance` 已被 `parse_agent_audit` 取代但未删除。删除，并确认 tests 与两个 scripts 无引用。

**P3-2 规范化事件 schema + 映射层。** 当前泛化的最大障碍：audit_log 的字段语义（`decision` 的取值 `auto_approved/approved/rejected/blocked`）是硬编码约定，新 BG 的 agent 日志格式必然不同。定义 canonical 事件模型并把映射下放到配置：

```python
@dataclass
class AuditEvent:
    case_id: str
    route: str        # canonical: auto | hitl | block | error
    duration_ms: float
    raw: dict         # 原始 entry，供 risk_rules 等使用
```

`agents.yaml` 每个 agent 增加映射节（现有两个 agent 显式写出当前默认值，作为文档）：

```yaml
    field_map:
      case_id: contract_id      # 取代顶层 id_field，迁移后删除 id_field
      duration_ms: duration_ms
    route_map:                  # 原始 decision 值 → canonical route
      auto_approved: auto
      approved: hitl
      rejected: hitl
      blocked: block
```

新增 `normalize_events(entries, agent_cfg) -> list[AuditEvent]`；`parse_agent_audit` 内所有对原始 decision 字符串的判断改为消费 canonical route。route_map 未覆盖的 decision 值计入 `unmapped_decisions` 并在仪表盘说明列以 ⚠️ 提示（不静默丢弃——这是接入新 agent 时最常见的坑）。

**P3-3 计算/渲染分离。** 现在 `generate_dashboard` 直接从 `AgentMetrics` 拼 markdown，Demo 页只能手写数字。切成两层：

- 计算层：`build_snapshot(metrics_list, thresholds, agent_cfgs, previous) -> dict`，输出可 JSON 序列化的完整仪表盘模型（每 agent 每指标：value / status(green|yellow|red|uncalibrated) / trend(up|down|flat|none) / note / threshold_source(global|agent)，外加 routing mismatches、生成日期、agent 元信息）。emoji 不进快照，快照只存语义值。
- 渲染层：`render_markdown(snapshot) -> str`，语义值 → emoji/箭头在这里发生。
- `run_dashboard.py` 落盘两份：`reports/dashboard_<date>.json`（canonical，Phase 5 的 Demo 页数据源）+ `reports/dashboard_<date>.md`。

**P3-4 指标注册表。** 仪表盘的指标集合目前散在 `generate_dashboard` 的手写行里。建 `eval/metric_registry.py`：每个指标一条注册项（key / label / 计算函数名或 AgentMetrics 属性 / thresholds key / 是否 uncalibrated / 说明模板），计算层遍历注册表产出快照，渲染层不再点名任何具体指标。验收标准：新增一个指标（如 `block_rate 与 expected block 占比之差`）只需在注册表加一项 + 一个纯函数，snapshot/md/后续 html 自动带上。加完这个示例指标后删掉它，只保留能力。

**验收：** `uv run pytest` 全绿（tests 相应迁移到 snapshot 断言，不再断言 md 字符串里的 emoji 拼接细节，列数一致性测试保留）；`dashboard_<date>.json` 结构稳定可读；在 route_map 里临时删掉一行制造 unmapped decision，仪表盘出现 ⚠️；改回。

---

## Phase 4 — 垂类插槽：profile 机制（只做一个参考实现）

**P4-1 vertical profile 结构。** 建 `verticals/legal-compliance/profile.yaml`，把现有两个 agent 归入该垂类作为参考实现：

```yaml
name: legal-compliance
label: 法务合规
description: 合同/合规类审批场景
rubric_overrides:            # 复杂度阶梯的垂类权重（维度集合不可改，只可改权重和分档）
  weights:
    risk_level: 0.25         # 法务垂类风险权重高于全局默认
    frequency: 0.05
  # 未列出的维度沿用全局 rubric；weights 覆盖后必须重新归一（校验和=1.0，否则报错）
threshold_presets:           # 垂类级阈值默认，优先级：agent override > vertical preset > global
  hitl_trigger_rate: {green: [0.0, 0.7], yellow: [0.7, 0.85], red: [0.85, 1.0]}
risk_rule_templates:         # 该垂类常见风险信号模板，注册 agent 时可引用（见 P4-2）
  related_party: {path: 关联方标记, equals: true}
  guarantee: {path: 条款标记.担保, equals: true}
```

**P4-2 接线。**
- `agents.yaml` 每个 agent 加 `vertical: legal-compliance`；`risk_rules` 支持 `{template: related_party}` 引用垂类模板（与内联规则可混用）。compliance-review-agent 现有的 `thresholds_override` 中与垂类 preset 重复的部分上移到 profile，agent 级只留真正个性化的。
- 仪表盘按 vertical 分组（快照中 agent 带 vertical 字段，渲染层按组出 section 标题）。
- scorer 支持 `--profile legal-compliance`：应用 rubric_overrides 后评分，报告标题注明所用 profile；不带参数则用全局 rubric，行为不变。

**P4-3 预留而不铺开。** 不创建第二个垂类目录。在 `verticals/README.md` 写清"新增垂类三步"：建 profile.yaml → agents.yaml 挂 vertical → （可选）scenarios 加该垂类场景；并列出 profile.yaml 全字段 schema 与优先级规则。这份 README 是"预留模块"的正式形态。

**验收：** 现有输出不回归（除 threshold_source 标注从 "agent 阈值" 变为 "垂类阈值" 处）；`--profile legal-compliance` 与不带参数的评分结果差异符合权重改动方向；tests 增加 profile 合并与优先级用例（agent > vertical > global）。

---

## Phase 5 — 网页预览 Demo 最终修订（静态、一次到位）

**目标：** `docs/index.html` 从"手写数字的介绍页"变为"由快照数据构建的展示页"，此后不再手改数据。保持现有视觉风格（配色、字体、排版语言、hero 几何动效），不重设计。

**P5-1 数据内嵌机制。** 建 `scripts/build_site.py`：读取最新 `reports/dashboard_<date>.json`、`reports/history.jsonl`、最新 `reports/complexity_scores_<date>.json`，合并为一个 snapshot 对象，替换 `docs/index.html` 中 `<script type="application/json" id="snapshot">…</script>` 块的内容。页面自带渲染脚本从该块读数据填充 DOM。无 fetch、无外部请求（现有 Google Fonts 保留）、无实时数据；页脚显著标注"数据快照 · <日期> · 非实时"。

**P5-2 数据驱动的区块。** 以下内容改为从 snapshot 渲染，其余叙事文案保持手写：hero 三个统计数（维度数、silent failure、agent 数）；复杂度阶梯三场景的得分/等级表；质量仪表盘各 agent 指标卡（含红黄绿状态、趋势箭头、垂类分组）；routing mismatch 列表（为空则显示"当前无偏差"）。

**P5-3 结构预留。** 渲染脚本按 snapshot 中的 verticals/agents/metrics 数组循环生成，不硬编码"两个 agent、六个指标"；未来加垂类或指标，重跑 build_site.py 即可，页面不需要再改——这是"最后一次修订"成立的前提。

**验收：** `uv run python scripts/build_site.py` 后用本地浏览器打开 docs/index.html，数字与最新 reports 一致；在 agents.yaml 临时复制一个 agent 条目 → 重跑 dashboard + build_site → 页面自动出现第三张卡且布局不破；改回。grep 确认页面无 fetch/XHR/websocket。

---

## 通用要求

- 增量修改优先；`metrics.py` 若超过 ~500 行可拆为 `eval/` 包内模块（normalize / compute / render），但拆分要在单独 commit 且 tests 全绿后进行。
- 面向 PM 的输出与页面文案保持中文；README.md 的模块说明同步更新（agents.yaml 映射节、verticals 机制、build_site 用法）。
- 完成后输出变更摘要：每 Phase 文件清单 + 验收结果，格式同上一轮。
