# Agent 立项与质量评估工作台

PM 侧的决策与评估工具——不是第三个 agent 项目，是回答「这个场景该不该上 agent」和「agent 上线后表现如何」的两个问题。

> **配套项目**：本工具的数据源来自两个 demo——`contract-approval-agent`（合同审批路由，规则+HITL）和 `compliance-review-agent`（合规审查，RAG+推理）。三个项目是一套组合拳：前两个展示不同场景的 agent 架构，本工具提供立项决策和质量评估的闭环。

**🔗 在线预览：[lesprivilege.github.io/agent-quality-workbench](https://lesprivilege.github.io/agent-quality-workbench/)**

![预览](docs/preview.png)

> 预览图占位——把 `docs/preview.png` 换成真实截图即可：打开上面的在线预览链接，全页截图（Chrome DevTools `Cmd+Shift+P` → "Capture full size screenshot" 最省事），存成 `docs/preview.png`，替换本行图片即生效。

## 这是什么

两个模块，架在一层通用底层上：

1. **复杂度阶梯评分器**（`rubric/` + `scenarios/` + `scripts/run_scorer.py`）——在立项阶段评估候选场景是否值得投入 agent，输出推荐等级和每维度得分。场景写在 YAML 里，支持按垂类 profile 覆盖权重。
2. **跨 agent 质量仪表盘**（`eval/` + `scripts/run_dashboard.py`）——从已有 agent 的运行数据中提取质量指标，按 CLASSIC 五维（成本/延迟/准确/稳定/安全）组织成红黄绿状态表，并记录历史趋势。

底层是注册制的：接入一个新 agent 只需在 `eval/agents.yaml` 加一个条目（字段映射、路由映射、风险规则、黄金测试集引用），不改任何 Python 代码。指标计算和评分全部用纯规则/脚本实现，不调用 LLM——评估工具自己必须是确定性、可审计、可复现的。

> 本仓库的迭代方式是「人做架构判断、出 prompt，CLI agent 写码」，历轮 prompt 存于 `docs/prompts/`，可作为决策记录查阅。

## Gap 分析：为什么需要这个工具

| 问题 | 现状 | 这个工具补什么 |
|------|------|---------------|
| 「该不该上 agent」靠直觉 | PM 拍脑袋决定，没有结构化评估框架 | 6 维度加权评分，输出明确的"不建议/简单/中等/复杂"等级 |
| 「agent 表现如何」靠跑测试看 pass/fail | 测试全绿但 M013 这种 silent failure 漏过去了 | 仪表盘从 audit_log 提取 HITL 触发率、guardrail 拦截率、silent failure 代理等运营指标 |
| 两个 agent 项目各自独立评估 | 没有跨项目的横向对比 | 统一仪表盘，同口径对比两个 agent 的完成率/准确率/HITL 率 |
| 立项和验收之间没有闭环 | 立项时说「需要 HITL」，验收时不知道 HITL 率是否合理 | 复杂度阶梯的推荐等级和仪表盘的实际 HITL 率可以交叉验证 |

## 模块一：复杂度阶梯评分器

### 设计理由

**为什么是 6 个维度？** 从两个 demo 项目中提炼出 agent 复杂度的 6 个驱动因素：
- **任务不确定性**（权重 0.25）——核心门槛：规则能覆盖的不需要 agent
- **不可逆性**（0.20）——错误代价高→需要 HITL/guardrails
- **风险等级**（0.20）——与不可逆性互补，驱动 guardrails
- **异常率**（0.15）——异常多→需要 HITL 能力
- **数据非结构化程度**（0.10）——非结构化→需要 RAG/推理
- **频次**（0.10）——ROI 因子，不决定复杂度

**为什么权重这样分配？** task_determinism 最高（0.25）因为这是 agent 立项的第一道门槛——能用规则解决的不应该上 agent。irreversibility 和 risk_level 并列第二（各 0.20）因为它们共同决定了 HITL/guardrails 的必要性。frequency 最低（0.10）因为高频不等于需要复杂 agent——报价生成就是反例。

**为什么是 0-5 分制？** 每个维度 0-5 分，加权后满分 5.0。初始用 0-2 分制发现实际得分太集中（1.5-1.9 区间），无法区分不同复杂度级别。0-5 分制提供了足够的分辨率。

### 三个场景评分结果

| 场景 | 加权得分 | 推荐等级 | 与实际实现的对比 |
|------|----------|----------|-----------------|
| **contract_approval** | 3.25 | 中等 agent（routing + HITL + RAG） | 实际实现是 routing + HITL + guardrails（无 RAG），评分略高——因为频次和风险拉高了分数。评分器倾向保守，这符合 PM 决策偏好 |
| **compliance_review** | 4.8 | 复杂 agent（全栈） | 实际实现是 RAG + 多步推理 + HITL，完全吻合。5 个维度满分，只有频次中等 |
| **quote_generation** | 0.5 | 不建议上 agent | 正确——报价是高频低风险规则可穷举的场景，用规则引擎即可 |

### 各维度得分明细

```
contract_approval:
  任务不确定性      2/5 × 0.25 = 0.50  ██░░░  审批矩阵可查表，但关联方/条款风险需要语义判断
  不可逆性        4/5 × 0.20 = 0.80  ████░  签署/大额支付不可逆
  风险等级        4/5 × 0.20 = 0.80  ████░  涉及合规、财务、法律风险
  异常率          3/5 × 0.15 = 0.45  ███░░  约5-10%需要HITL
  数据非结构化程度   2/5 × 0.10 = 0.20  ██░░░  结构化字段+自由文本条款描述
  频次            5/5 × 0.10 = 0.50  █████  每天数十到数百份合同

compliance_review:
  任务不确定性      5/5 × 0.25 = 1.25  █████  合规判断需要综合多部法规，无法用规则穷举
  不可逆性        5/5 × 0.20 = 1.00  █████  漏审条款可能导致法律风险
  风险等级        5/5 × 0.20 = 1.00  █████  直接涉及合规和法律风险
  异常率          5/5 × 0.15 = 0.75  █████  法规覆盖不完整、新业务类型常见
  数据非结构化程度   5/5 × 0.10 = 0.50  █████  法规是非结构化文本
  频次            3/5 × 0.10 = 0.30  ███░░  中频

quote_generation:
  所有维度 0 分（规则可穷举、可逆、低风险、无异常、结构化），仅频次 5/5
```

### 如何跑

```bash
cd ~/Projects/agent-quality-workbench
uv sync
uv run python scripts/run_scorer.py                          # 跑 scenarios/ 下全部场景
uv run python scripts/run_scorer.py --scenario quote_generation
uv run python scripts/run_scorer.py --profile legal-compliance  # 应用垂类权重
```

输出 `reports/complexity_scores_<date>.json` 和 `.md`。场景一事一文件（`scenarios/*.yaml`），新增候选场景不需要改代码。

## 模块二：跨 Agent 质量仪表盘

### 设计理由

**为什么从 audit_log 解析而不是从测试结果？** 测试只告诉你 pass/fail，audit_log 告诉你每一步发生了什么——哪个 guardrail 触发了、HITL 的 reason 是什么、决策链路是否符合预期。两者互补，但 audit_log 是运营视角，更接近「上线后会怎样」。

**为什么需要 silent failure 代理？** 因为 silent failure 是最危险的——agent 静默地给出了错误结果但没有任何标记。测试全绿不代表没有 silent failure。

### Silent Failure 的两种正交模式

Silent failure 不止一种。仪表盘的规则扫描覆盖第一种，第二种目前没有自动化检测手段。

**模式一：风险信号覆盖缺口。** 源数据有关联方/担保/不可逆等结构化风险标记，但 agent 没有拦截、静默放行。仪表盘的 `Silent Failure` 指标扫描的就是这一种——拿 `contracts.jsonl`/`materials.jsonl` 的结构化字段和 `audit_log` 的 decision 交叉比对，看"有风险信号 + auto_approved"的用例数。当前两个 repo 都是 0 命中。

**模式二：置信度校准失真。** 数据本身没有结构化风险标记，但模型对依据不足的结论给出了虚高的置信度。compliance-review-agent 的 `docs/llm-verification-log.md` 记录了一次真实发现：

> **M013（量子计算咨询合同）**：法规库对量子计算领域返回 0 结果，但 LLM 仍给出 confidence=0.82 的高置信度。原因是 prompt 中"confidence 表示你对结论的置信度"被 LLM 理解为"对'低风险'这个判断本身的把握"，而非"法规依据的充分程度"。法规库明明没查到东西，置信度却很高。

后续已修复 prompt 语义（"confidence 表示你的结论有多少法规依据支撑"），重验 M013 置信度从 0.82 降至 0.15。

模式二规则扫描抓不到——问题不在结构化字段里，在模型的自我表达上。M013 是靠 `24-CLI委托-subagent验证LLM逻辑` 那次 subagent 验证流程人工发现的。systematic 检测需要比如「结论置信度 vs 实际法规依据数量」的交叉校验，是合理的下一步方向。

### 指标定义与阈值

指标按 CLASSIC 五维归类（Cost / Latency / Accuracy / Stability / Security），仪表盘先给一行五维水位摘要（取每维度下最差指标的状态色），再展开指标明细。阈值分三级：全局默认（`eval/thresholds.yaml`）< 垂类 preset（`verticals/*/profile.yaml`）< agent 覆盖（`eval/agents.yaml`）——合规类 agent 本来就该高 HITL，用全局 40% 绿线去卡它是错的标尺。

| 指标 | 五维 | 定义 | 🟢 绿 | 🟡 黄 | 🔴 红 | 阈值理由 |
|------|------|------|-------|-------|-------|----------|
| 任务完成率 | Accuracy | 测试通过数/总数 | ≥95% | 80-95% | <80% | 核心指标，>95% 说明行为可预期 |
| 路由准确率 | Accuracy | gold case 实际路由命中预期路由（双向：多余的 HITL/拦截同样算错） | ≥90% | 75-90% | <75% | 只奖励命中不惩罚多余会自相矛盾——HITL 红灯时准确率不该还是 100% |
| HITL 触发率 | Cost | 触发 HITL 的用例/总数 | <40%* | 40-60% | ≥60% | HITL 是成本，过高说明自动化价值低。*合规垂类 preset 放宽至 <70% |
| Guardrail 拦截率 | Security | 被拦截的用例/总数 | <30% | 30-50% | ≥50% | 拦截过高说明输入数据质量差或规则过严 |
| Silent Failure | Security | 风险信号覆盖缺口扫描命中数/总数 | <5% | 5-15% | ≥15% | 模式一（结构化风险标记被静默放行），模式二需人工检测 |
| 路由稳定性 | Stability | 最近 5 次运行路由完全一致且命中预期的 case 占比（pass^k 代理） | ≥95% | 85-95% | <85% | 生产环境看重 k 次全对而非 k 次里对一次；当前 demo 是确定性实现恒 100%，接入真实 LLM agent 后该指标才开始工作 |
| 平均延迟 | Latency | audit_log duration_ms 均值 | ⚪ 未校准 | — | — | demo 数据无参考价值，生产环境需替换为 token 计数 |
| 步骤效率 / 工具参数正确率 | Cost / Accuracy | 轨迹级指标，需 agent 输出 `trace_log.jsonl` | ⚪ 无 trace 数据 | — | — | 契约已定义（`eval/trace.py`），agent 侧补日志后自动生效 |

### 仪表盘输出什么

每次运行产出两份文件：`reports/dashboard_<date>.json`（canonical 快照，Demo 页的数据源）和 `.md`（人读版）。markdown 里每个 agent 一个 section，按垂类分组，结构是：

1. **五维摘要行**——`Cost ● | Latency ⚪ | Accuracy ● | Stability ● | Security ●`，一眼看五个维度的水位；
2. **指标明细表**——值、红黄绿状态、环比趋势箭头（↑↓→，对比上次运行）、说明（含阈值来源：全局/垂类/agent）；
3. **Gold Set 覆盖**——按失败模式统计 case 数与命中率，historical 案例占比单列；
4. **路由偏差列表**——期望路由与实际路由不一致的 case 逐条列出，为空则显示"当前无偏差"。

具体数字以 `reports/` 下最新文件为准，不在 README 里复制粘贴——复制的数字必然过期。

### 仪表盘发现

1. **compliance-review-agent 的 HITL 率 60%（红）**：合规审查本身就是高 HITL 场景——这与复杂度阶梯评分器给出的"复杂 agent（全栈）"等级一致。如果 HITL 率持续偏高，说明需要提升 agent 的自主判断能力（如更好的 RAG、更精确的 prompt）。
2. **contract-approval-agent 的 guardrail 拦截率 36.4%（黄）**：主要来自 C003（未立项）、C004（跨板块）、C009（超预算）、C011（黑名单）——这些是刻意设计的测试用例，不代表真实业务中拦截率会这么高。
3. **两个 agent 的路由准确率都是 100%**：命中的是 goldset 里人工标注的期望路由，且当前 gold case 全部是合成数据（`source: synthetic`）。这个 100% 说明"demo 按设计运行"，不说明"上生产没问题"——用真实历史失败案例充实 goldset 之前，它只是回归基线。

### 如何跑

```bash
cd ~/Projects/agent-quality-workbench
uv sync
uv run python scripts/run_dashboard.py    # 输出 dashboard_<date>.json + .md，并追加历史
uv run python scripts/build_site.py       # 把最新快照注入 docs/index.html
```

依赖 `eval/agents.yaml` 中注册的各 agent repo 的 `data/audit_log.jsonl` 与 pytest（只读，不修改那些 repo）。每次运行会向 `reports/history.jsonl`（指标级）和 `reports/case_history.jsonl`（case 级，供稳定性指标使用）各追加一次记录。

## 项目结构

```
agent-quality-workbench/
├── rubric/
│   └── complexity_ladder.yaml   ← 6 维度评分标准 + 权重 + 复杂度等级定义
├── scenarios/                   ← 候选场景，一事一文件
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
├── reports/                     ← 快照、报告、指标/case 级历史
├── tests/                       ← workbench 自身测试（fixture 驱动，不依赖外部 repo）
└── docs/
    ├── index.html               ← 静态展示页（GitHub Pages 入口），数据由 build_site.py 注入
    └── prompts/                 ← 历轮迭代交给 CLI agent 的 prompt，即决策记录
```

## 已知局限

1. **audit_log 是测试运行产生的，不是真实业务数据**——指标反映的是测试覆盖率，不是真实运营表现。
2. **路由准确率只判断路径**——audit_log 只能回答"走没走对路径"，不能回答"判断本身是否正确"；goldset 当前全部是合成 case（historical 占比 0%），真正的说服力要靠历史失败案例反哺。
3. **延迟指标未校准**——demo 本地运行无真实延迟，仪表盘中显示 ⚪，生产环境需替换为 token 计数。
4. **复杂度阶梯需要校准**——当前评分边界针对这两个 demo 调整；垂类 profile 机制解决"权重不该全局统一"的问题，但每个新垂类的权重仍需人拍。
5. **silent failure 只覆盖「风险信号覆盖缺口」**——规则扫描检测的是"源数据有结构化风险标记但被静默放行"。另一类「置信度校准失真」（M013 案例）问题不在结构化字段里而在模型的自我表达上，规则扫描天然抓不到，需要「置信度 vs 依据数量」交叉校验等下一步方向。
6. **风险规则是 guardrail 的镜像**——`agents.yaml` 里的 risk_rules 复述了各 agent 自己的 guardrail 逻辑，存在 drift 风险；长期方向是 agent 在 audit_log 直接输出 `risk_signals`，workbench 只消费不复述。

## 设计对标（社区实践对齐）

企业级 agent 评估的社区实践（EnterpriseBench、CRMArena-Pro、CLASSIC、trajectory evals）里，与"确定性、可复现"定位兼容的部分已经吸收进来；不兼容的（仿真沙盒、GUI 评估、LLM-as-judge）明确不做，只留接口。对 CLASSIC 五维的映射：

| 维度 | 本工作台指标 | 现状 | 缺口 |
|------|-------------|------|------|
| **Accuracy** | 任务完成率、路由准确率 | ✅ 已实现 | — |
| **Cost** | HITL 触发率 | ✅ 代理指标 | 缺 token 成本 |
| **Latency** | 平均延迟 | ⚪ 未校准 | demo 数据无参考价值 |
| **Stability** | 路由稳定性（pass^k 代理） | ✅ 已实现 | 当前恒 100%（确定性 agent），接入 LLM 后生效 |
| **Security** | Guardrail 拦截率、Silent Failure | ✅ 已实现 | 模式二（置信度校准）需人工检测 |

另外两项实践的落法：

- **Gold Set**：社区共识是"用真实历史失败案例反向构建 50–200 条黄金测试集"。`goldset/` 目前 21 条 case，每条标注失败模式（routing_error / guardrail_gap / over_escalation / silent_failure / calibration）与来源；来源全部是 synthetic，仪表盘如实显示 historical 占比 0%——这个数字本身就是路线图。
- **Trace 插槽**：轨迹级评估（步骤效率、工具参数正确率）的价值在"不只看结果、还看路径"，但前提是 agent 输出步骤日志。这里的处理是把契约文档化（`eval/trace.py` 定义 schema，`verticals/README.md` 写接入三步），指标先以 ⚪ 挂在仪表盘上——预留的正式形态是文档化的契约，不是空实现。

## 静态展示页

`docs/index.html` 是单文件静态展示页：叙事部分手写维护，数据部分由 `scripts/build_site.py` 从最新的 `reports/dashboard_<date>.json` 注入（内嵌 JSON 块 + 页内渲染脚本，按快照里的垂类/agent/指标数组循环生成卡片）。无后端、无 fetch、无实时数据——数据更新的方式是重跑 `run_dashboard.py` + `build_site.py` 后提交，页脚标注快照日期。

判断逻辑和阈值定义以本 README 和 `eval/thresholds.yaml`、`rubric/complexity_ladder.yaml` 为准，页面只是渲染层。

**GitHub Pages**：`docs/` 为发布目录（Settings → Pages → `main` /docs），访问 `https://lesprivilege.github.io/agent-quality-workbench/`。

讲述口径：这是"评估工具生成的展示层"，不是前端工程能力的证明——加分点始终是指标设计和阈值判断本身。

## 90 秒讲解稿骨架

```
[0-15s] 这是什么
- 不是第三个 agent，是 PM 侧的决策工具
- 两个模块：立项前评分（该不该上 agent）+ 运营后仪表盘（agent 表现如何）

[15-40s] 复杂度阶梯评分器
- 6 个维度：任务不确定性、不可逆性、风险等级、异常率、数据非结构化程度、频次
- 加权求和映射到 0-5 级复杂度阶梯
- 三个场景实测：合同审批 3.25（中等 agent），合规审查 4.8（复杂 agent），报价生成 0.5（不建议上 agent）
- 关键洞察：频次高不等于需要复杂 agent——报价生成是高频但规则可穷举的反例

[40-60s] 跨 Agent 质量仪表盘
- 从注册的 agent 的 audit_log 提取指标，按 CLASSIC 五维（成本/延迟/准确/稳定/安全）组织
- 红黄绿阈值每条写明理由，三级覆盖：全局 < 垂类 < agent——合规 agent 本来就该高 HITL，不能用同一把尺
- goldset 按失败模式标注；稳定性用 pass^k 代理——生产要的是 k 次全对，不是 k 次里对一次
- 发现：compliance-review-agent 的 HITL 率放在全局阈值下是红灯，放在合规垂类阈值下是绿灯——这个反差本身就是"阈值要分垂类"的论据

[60-80s] Silent Failure 两种模式
- 模式一「风险信号覆盖缺口」：源数据有风险标记但 agent 没拦——仪表盘规则扫描已实现，当前 0 命中
- 模式二「置信度校准失真」：M013 案例——法规库查不到东西但 LLM 给了 0.82 高置信度
- 模式二规则扫描抓不到，是靠 subagent 验证流程人工发现的，systematic 检测是下一步方向

[80-90s] 闭环
- 复杂度阶梯说"合规审查需要复杂 agent"→仪表盘显示 HITL 率 60%→验证了这个判断
- 两个模块互相校验：立项时的推荐等级和运营时的实际指标应该一致
```
