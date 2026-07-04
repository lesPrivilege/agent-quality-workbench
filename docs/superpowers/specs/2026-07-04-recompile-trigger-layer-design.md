# 重编译触发层设计（roadmap 缺口一，1-3 项）

> 状态：已批准，待写实施计划
> 关联：`docs/roadmap.md` 缺口一（优先级 1，对应 E5）

## 背景与目标

`docs/roadmap.md` 提出的假设：分布漂移是编译产物的主要衰减方式，「重编译频率」可以从已有的 `reports/history.jsonl` 数据中提取出可操作的信号。仪表盘目前是静态快照，缺的是回答「哪些编译产物需要重新验证」的层。

这层的接口不依赖真实数据就能先定出来——先把机制建好，用合成数据验证正确性；真实信号的有无留给 roadmap 里已经写好的 kill criterion 去筛（接入真实数据三个月内测不出有意义漂移，则报告降级为被动记录）。

## 范围

**本次做**：roadmap 缺口一的 1-3 项。

1. goldset 时效性指标——纯 age-based proxy
2. 指标漂移检测——`history.jsonl` 滑动窗口
3. 重编译触发报告——聚合 1+2

**本次降级为契约先行**：缺口一第 4 项（模式三·法规时效失效）。原因：探查发现 `compliance-review-agent` 的法规库（`data/regulations/*.md`）是无 ID、无日期的纯文本文件，其 `audit_log` 的 `retrieve` 节点只记录命中条数（如"检索到 1 条法规"），不记录命中的是哪一条。在这两个前提都不存在的情况下实现"扫描已失效法规引用"，只能产出假数据。因此本次只定义契约（schema + 扫描器），不做真实扫描；sibling repo 侧的改造（法规库加 ID/生效日期、audit_log 输出引用的法规 ID）另开一轮，记入 `docs/roadmap.md` 作为缺口一的显式后续依赖项。

**不做**：roadmap 缺口二、三、四；`~/Projects/compliance-review-agent` 仓库的任何改动。

## 架构

```
eval/
├── threshold_resolution.py   [新增] resolve_threshold_cfg() —— 从 snapshot.py 抽出
├── staleness.py               [新增] goldset case age-based 过期检测
├── drift.py                   [新增] history.jsonl 滑动窗口漂移检测
├── regulation_staleness.py    [新增] 模式三契约定义 + not_instrumented-aware 扫描器
└── snapshot.py                [修改] 改用 threshold_resolution.py，_threshold_status 改名为公开的 threshold_status

scripts/
└── run_recompile_check.py     [新增] 编排上述模块，产出 reports/recompile_triggers_<date>.{json,md}

tests/
└── test_recompile.py          [新增]
```

设计原则：颗粒度与现有 `metrics.py` / `snapshot.py` / `metric_registry.py` / `trace.py` 一致——每个文件一个职责。`regulation_staleness.py` 仿照 `trace.py` 的契约模式（docstring 定义 schema，loader/scanner 缺数据时返回明确的"未装契约"状态而非假装为 0）。

## 详细设计

### `eval/threshold_resolution.py`（重构，行为不变）

把 `snapshot.py::build_snapshot()` 内嵌的闭包 `_get_cfg()` 抽成独立函数：

```python
def resolve_threshold_cfg(thresholds_key: str, global_thresholds: dict,
                           agent_overrides: dict, vertical_presets: dict) -> dict:
    """优先级：agent override > vertical preset > global"""
```

`snapshot.py` 改为调用这个函数，删除原闭包。`_threshold_status()` 去掉下划线前缀改为公开的 `threshold_status()`，供 `drift.py` 复用。

这不是纯洁癖式重构：`drift.py` 判断"漂移"必须用和 dashboard 渲染**同一套**阈值解析逻辑（含 agent override 和 vertical preset），否则会出现 dashboard 显示绿、drift 报告却说漂移的自相矛盾——两套逻辑各自维护迟早分叉，这正是"重编译层"想抓的那类问题在自己代码里的实例。

行为不变，靠重跑现有 `test_workbench.py` 全量兜底，不新增该重构专属的测试。

### `eval/staleness.py`

```python
def compute_goldset_staleness(goldset_cases: list[dict], max_age_days: int, today: date) -> dict:
    """
    返回: {total, stale_count, stale_ratio, stale_cases}
    stale_cases 每项二选一形状：
      {"id": "C001", "reason": "age", "age_days": 120}
      {"id": "C002", "reason": "missing_last_verified"}
    """
```

逻辑：
- 每条 case 读 `last_verified`（新增必填字段，`YYYY-MM-DD`）。
- `age_days = (today - last_verified).days`；`age_days > max_age_days` → stale。
- **`last_verified` 字段缺失的 case 直接归入 stale**，标注原因为"字段缺失"而非年龄数字——不能假设缺字段等于新鲜。
- `max_age_days` 从 `eval/thresholds.yaml` 新增的 `staleness:` 节读取，值为 **90**。

**回填语义（重要，写入 `goldset/README.md`）**：本次上线时 21 条 case 的 `last_verified` 统一回填为 **2026-07-04**（字段引入日）。这个日期表示"字段是这天加上去的"，**不代表**这天真的对 21 条 case 做了复验。`goldset/README.md` 必须明确写这个区别——否则 90 天后（约 2026-10-02）第一批 stale 告警出现时，日期基准会被误读成"有人在 7 月 4 日验证过、现在真的过期了"，而实际上从未有过真正的复验事件。这和模式三"not_instrumented 不能伪装成 0"是同一类问题：回填动作不该伪装成验证事件。

"与规则/法规变更日期比对"不建实际字段（避免在没有变更追踪数据源时构造死字段），只在 `goldset/README.md` 里写成保留方向。

### `eval/drift.py`

```python
def detect_metric_drift(history_entries: list[dict], agent_name: str, window_size: int,
                         thresholds: dict, agent_cfg: dict) -> dict:
    """
    返回: {
      insufficient_history: bool, history_count, required_count,
      events: [{metric, old_status, new_status, old_avg, new_avg,
                old_window_span, new_window_span}],       # 越级变差
      improvements: [{... 同上字段 ...}],                  # 越级变好，不算触发项
    }
    old_window_span / new_window_span 形状: {"from": "2026-07-01", "to": "2026-07-03"}
    —— 取自窗口内 history 条目的 date 字段首尾，不是新的时间戳来源
    """
```

逻辑：
- 按 `agent_name` 从 `history.jsonl` 里取出该 agent 每次运行的指标值，按时间顺序排列（记总条数为 k）。
- `k < 2 * window_size` → `insufficient_history: true`，不做比较（避免用不够的数据误报）。`window_size` 取 **5**（与 `route_stability` 已用的 k=5 保持一致习惯）。
- 否则切片：新窗口 = 最近 N 条（`entries[k-N:]`），旧窗口 = **紧邻新窗口之前**的 N 条（`entries[k-2N:k-N]`）——不是"有史以来最早的 N 条"。这一点必须明确：真正的滑动窗口要随着新数据不断前移对比基准，如果旧窗口固定钉在最初 N 次运行，比较基准会永远停留在项目早期（可能只是开发期的噪声数据），失去"滑动"的意义，也违背 roadmap 原文"近 N 次 vs 前 N 次"的表述。每个窗口对每个指标取均值。
- 用 `threshold_resolution.resolve_threshold_cfg()` + `threshold_status()`（复用，非重新实现）把两个窗口的均值各自转成 green/yellow/red。
- 严重度顺序 green < yellow < red；新窗口比旧窗口更严重 → `events`；更轻 → `improvements`。
- 窗口单位是"运行次数"而非时间——`history.jsonl` 里已经出现同日 3 条重复记录，说明运行密度不均匀，N=5 可能横跨 5 分钟也可能横跨 5 周。因此 `events`/`improvements` 每项附带 `old_window_span` / `new_window_span`（取窗口内 history 条目 `date` 字段的首尾），让报告读者能判断这次漂移发生在多长的时间跨度里。只加输出字段，不改检测逻辑本身。
- 范围限定：只处理 `save_history()` 实际持久化的 6 个指标（`task_completion_rate`、`accuracy_proxy`、`hitl_trigger_rate`、`guardrail_block_rate`、`silent_failure_rate`、`avg_latency_ms`）——这 6 个在 `history.jsonl` 里恒为数值，不会出现 `uncalibrated`/`error` 状态，因此 drift.py 不需要处理这两种状态。trace 类指标（`step_efficiency` 等）和 `route_stability` 当前不写入 `history.jsonl`，天然不在本模块范围内。

**已知盲区（写入报告 footer 和本模块 docstring，不在 v1 解决）**：窗口均值转状态色的方法只能抓"越级"漂移（如 green→yellow）。指标在同一色带内缓慢滑坡（例如 98%→96%→95.5%，一直是 green）测不出来，直到真正跨过边界才会报警。v1 这样做已经够用——真实数据显示带内漂移是常见模式之后，再考虑加斜率检测；现在不做，属于投机性建设。

当前 `reports/history.jsonl` 只有 3 条同日重复记录，上线初期两个 agent 都会落在 `insufficient_history`，这是预期状态，不是 bug。

### `eval/regulation_staleness.py`（契约先行，仿 `trace.py`）

Docstring 定义契约：

```
audit_log 条目可选携带 regulation_refs 字段（数组）：
  {"regulation_refs": [{"id": "反垄断合规", "effective_until": "2027-01-01"}, ...]}
未来 sibling repo（如 compliance-review-agent）实现该字段后，本模块的扫描逻辑自动生效，
无需修改本文件。
```

```python
def scan_regulation_staleness(audit_log_entries: list[dict], today: date) -> dict:
    """
    返回三态之一：
      {"instrumented": False}
        —— 没有任何一条 audit_log 记录携带 regulation_refs 字段
      {"instrumented": True, "stale_count": 0, "stale_refs": []}
        —— 已装契约，扫描后没有过期引用
      {"instrumented": True, "stale_count": N, "stale_refs": [...]}
        —— 已装契约，发现 N 条引用已失效法规的记录
    """
```

`instrumented: False` 和 `stale_count: 0` 在报告里必须显式区分展示（"未接入检测" vs "已检测，无过期引用"）——把前者显示成后者就是又一次假绿灯，和 README 已知局限里 M013 案例是同一类错误。

两个现有 demo agent 当前都会落在 `instrumented: False`。这不是失败状态，是诚实状态。

### `scripts/run_recompile_check.py`

复用 `eval.metrics.load_agents()` / `load_thresholds()`（与 `run_dashboard.py` 相同的配置加载方式）。流程：

1. 加载 `agents.yaml`、`thresholds.yaml`、各 agent 的 `goldset/*.yaml`、`reports/history.jsonl`、各 agent 的 `audit_log.jsonl`。
2. 对每个 agent 调用 `staleness.compute_goldset_staleness()`、`drift.detect_metric_drift()`、`regulation_staleness.scan_regulation_staleness()`。
3. 汇总成一个 dict，写 `reports/recompile_triggers_<date>.json`（含 `schema_version`）和渲染出 `.md`。
4. print 摘要到 stdout（跟 `run_dashboard.py` 一致的习惯）。

## 报告 Schema

`reports/recompile_triggers_<date>.json`（新契约产物，纳入未来 conformance 校验范围，先带版本号）：

```json
{
  "schema_version": "1.0",
  "date": "2026-07-04",
  "summary": {
    "total_stale_cases": 0,
    "total_drift_events": 0,
    "agents_instrumented_for_regulation_staleness": 0
  },
  "agents": [
    {
      "name": "contract-approval-agent",
      "goldset_staleness": {
        "max_age_days": 90,
        "method": "age_proxy",
        "total_cases": 11,
        "stale_count": 0,
        "stale_ratio": 0.0,
        "stale_cases": []
      },
      "metric_drift": {
        "window_size": 5,
        "insufficient_history": true,
        "history_count": 3,
        "required_count": 10,
        "events": [],
        "improvements": []
      },
      "regulation_staleness": {
        "instrumented": false
      }
    }
  ]
}
```

`.md` 镜像同样结构：顶部汇总 + 每 agent 一节（过期明细 / 漂移明细 / 模式三状态）。三者皆无时明确写"当前无漂移""当前无过期 case"，不留空白造成歧义（沿用现有仪表盘"当前无偏差"的写法习惯）。

## 已确认参数

| 参数 | 值 | 依据 |
|------|-----|------|
| `staleness.max_age_days` | 90 | 写入 `eval/thresholds.yaml` 新增 `staleness:` 节 |
| `drift.window_size` | 5 | 与 `route_stability` 的 k=5 保持一致习惯 |
| goldset 21 条 case 的 `last_verified` 初始值 | 2026-07-04 | 字段引入日，非验证日（见上文回填语义） |

## 错误处理与边界情况

- `history.jsonl` 缺失/为空 → 所有 agent 走 `insufficient_history` 分支，不报错。
- 某 agent 在个别历史行里缺失（如中途接入）→ 只用该 agent 实际出现的条目，不崩溃。
- goldset case 缺 `last_verified` → 计入 stale，单独标注"字段缺失"。
- `audit_log.jsonl` 缺失 → `regulation_staleness` 返回 `instrumented: False`（等同于"没有该字段"的处理）。

## 测试计划

`tests/test_recompile.py`，fixture 驱动（不依赖两个 agent 仓库真实数据，与现有测试哲学一致）：

- **staleness**：过期 / 未过期 / 缺字段三种 case 各至少一条。
- **drift**：触发漂移、无漂移（平稳）、improvement、`insufficient_history` 四种历史序列。
- **regulation_staleness**：`not_instrumented` / 已装契约且干净 / 已装契约且发现过期引用，三态各一条。
- **threshold_resolution**：直接测 `resolve_threshold_cfg()` 的三级优先级。
- **回归**：重跑现有 `test_workbench.py`、`test_goldset.py`、`test_stability.py`、`test_classic_trace.py` 全量，确认 `threshold_resolution.py` 抽取和 `_threshold_status` 改名后 `snapshot.py` 行为不变。

## 文档更新清单

- `README.md`：新增"模块三：重编译触发层"小节（设计理由 + 如何跑 + 已知局限编号延续现有风格）；已知局限第 5、6 条视情况更新措辞。
- `goldset/README.md`：新增 `last_verified` 字段说明 + 回填语义说明（见上）+ "与法规变更日期比对"的保留方向说明。
- `eval/thresholds.yaml`：新增 `staleness:` 节（`goldset_case_max_age_days: 90` + rationale）和 `drift:` 节（`window_size: 5` + rationale）。
- `docs/roadmap.md`：缺口一第 4 项标注"本轮降级为契约先行，sibling repo 改造为独立后续项"，记录探查发现（法规库无 ID/日期、audit_log 不记录命中法规）。

## 不做什么

- 不改 `~/Projects/compliance-review-agent`（法规库结构、audit_log 字段）——独立后续项。
- 不做带内（同色带内）漂移的斜率检测——v1 已知盲区，留待真实数据验证是否常见。
- 不做 roadmap 缺口二、三、四。
- 不给 goldset 加"与法规变更日期比对"的实际字段——保留方向记录在文档，不构造死字段。
