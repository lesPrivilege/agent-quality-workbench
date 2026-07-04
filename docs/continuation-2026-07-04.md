# 续行文档 · 2026-07-04

> 用途：真实数据可能要等很久，本文档冻结当前状态，供任意时长停摆后快速恢复上下文、继续规划。
> 读法：先看「状态快照」确认没有漂移，再看「触发器 → 动作」决定从哪里续行。

## 状态快照（截至 2026-07-04 收束）

- **代码**：模块三（重编译触发层）落地，main 与 origin 同步，88/88 tests。四个新计算模块（`threshold_resolution` / `staleness` / `drift` / `regulation_staleness`）+ 报告模块（`recompile_report`）+ 编排脚本（`run_recompile_check.py`）。
- **首份报告基线**：0/21 过期（字段引入日 2026-07-04 回填，非真实复验）、drift insufficient_history（3/10 条）、模式三两个 agent 均 not_instrumented。三个都是诚实状态，不是问题。
- **站点**：三模块叙事 + 整体 polish 已上线（lesprivilege.github.io/agent-quality-workbench）。页内 dashboard 快照仍是 2026-07-03 数据，下次跑 `run_dashboard.py` + `build_site.py` 后提交即刷新。
- **文档链**：`roadmap.md`（四缺口 + kill criteria）→ `superpowers/specs/2026-07-04-recompile-trigger-layer-design.md`（缺口一设计）→ `superpowers/plans/2026-07-04-recompile-trigger-layer.md`（10 task 执行计划，含最终 review 遗留的 id_field finding）→ `onboarding_log.md`（缺口二基线行已写）。
- **理论侧（vault，不在本 repo）**：`compile-economy-2026-05-20`、`cognitive-domain-boundary-2026-04-30` 保持原稿；新叙事《验证经济学：编译成本归零之后的边界地图》（verification-economy-2026-07-04）待归档，E6 的实测数字待本 repo 回填。

## 触发器 → 动作

续行不按日期，按事件。哪个触发器先到就做哪条。

| 触发器 | 动作 | 出处 |
|--------|------|------|
| **≈2026-10-02（90 天后）**，首批 goldset 过期告警出现 | 跑 `run_recompile_check.py`；对过期 case 做真实人工复验并更新 `last_verified`——这是第一次真正的复验事件，此前的 0 过期只是回填新鲜度 | 缺口一 · staleness |
| **history.jsonl 单 agent 条目 ≥ 10** | drift 检测开始工作；观察三个月，若全部指标平直 → 按 kill criterion 把重编译报告降级为被动记录 | 缺口一 · kill criterion |
| **接入第三个 agent（外部/非自建）** | onboarding_log 记第二行（agent 接入类型）；这是注册制契约的泛化性检验——若被迫改 `eval/*.py` 核心逻辑超过一次，暂停泛化叙事、先重构契约 | 缺口二 · kill criterion |
| **sibling repo 排期改造** | compliance-review-agent：法规库结构化（ID + effective_until）+ audit_log 输出 `regulation_refs`——模式三扫描自动生效，无需改 workbench | 缺口一 · 4 后续项 |
| **真实生产失败案例出现** | 按 goldset schema 录入（`source: historical` 必填）；historical 占比开始爬升——这是解冻缺口四的唯一条件 | 缺口四 |
| **决定动缺口三** | 步骤 1（confidence vs 依据数量交叉校验）不依赖真实数据即可建，是停摆期内唯一还能动的开发项；步骤 2 受步骤 1 捕获率 ≥ 80% 的 kill criterion 门控 | 缺口三 |

## 停摆期可做 / 不可做

**可做**：缺口三步骤 1（确定性交叉校验，合成 case 可验证）；验证经济学 paper 的 E1 补强（coding agent 成本下降曲线的公开数据）；站点 dashboard 快照刷新。

**不可做**（roadmap「不做什么」在停摆期同样有效，且更容易被违反）：不扩建合成 goldset、不预建外部 bench adapter、不做投机性的漂移斜率检测。停摆期的建设冲动大多是投机性建设。

## 遗留线索（不阻塞，续行时顺手看）

1. `id_field` fallback 分歧——最终 holistic review 的唯一 finding，已记入 plan 文档「已知的范围外事项」。
2. 带内慢漂移盲区——已写入 drift.py docstring 和报告 footer，等真实数据证明常见后再考虑斜率检测。
3. Harness 教训（已入 memory）：worktree 内绝对路径逃逸发生过两次，规则是动文件前 `git rev-parse --show-toplevel` 自检；reviewer 的「重引入 bug 验证测试抓不住」手法值得复用。
4. 沙箱 git 锁残留——Cowork 侧 commit 后可能留下无法自删的 `.git/*.lock`，下次遇到先核对时间戳与进程再 `rm`。

## 回填清单（给 paper 侧）

验证经济学 paper E6 需要的实测数字，数据齐了就回填：本轮验证层捕获 4 处真实缺陷（滑动窗口锚定、回归测试缺口、README 顺序、goldset 回填遗漏）+ 2 处验证层自身盲区（路径逃逸 ×2）；重编译层上线后的漂移触发频率、goldset 复验成本待积累。
