# Onboarding Log

`docs/roadmap.md` 缺口二的最小件：workbench 的接入成本自测。每次**接入新 agent / 新垂类**记一行——配置行数、人时。北极星是「新 agent 接入 = 只改 `eval/agents.yaml` + goldset 引用，零 Python 改动」；这个数字不好看就让它不好看，不在这里粉饰。

第一行不是 agent 接入，是缺口一本身（workbench 能力构建）——单独标类型，不进「agent 接入」这条序列的均值，只作对比基线：下次真实接入第三个 agent 时，用它衡量"只改注册配置"和"要动 workbench 核心逻辑"之间的成本量级差距。

| 日期 | 事件 | 类型 | 规模 | 人时 | 备注 |
|------|------|------|------|------|------|
| 2026-07-04 | 缺口一：重编译触发层 | workbench 能力构建（非 agent 接入） | 14 文件，+827/-15（`eval/` 核心逻辑约 430 行 + 测试 340 行 + 配置/文档约 60 行） | 约 1 天 | 10 task，subagent-driven 执行，每 task 独立 spec+quality 两阶段 review。验证层拦下 4 处真实缺陷：滑动窗口锚定 bug（自查，写计划时）、回归测试覆盖缺口（reviewer 用「重引入 bug 验证测试抓不住」证实）、README 章节顺序违反 spec（spec reviewer）、goldset 回填一处未及时发现（同上）。另有 2 处是验证层自身的盲区，非编译产物缺陷：worktree 内用绝对路径逃逸到 main checkout，发生两次（一次是我自己，一次是 subagent），事后补了「操作前先 `pwd`/`git branch --show-current` 自检」的规则，不是这轮抓到的功能性 bug。 |

## 接入下一个 agent 时怎么填

- **规模**：只统计 `eval/agents.yaml` 新增条目行数 + 新 goldset 文件行数；如果动了 `eval/*.py` 核心逻辑，在备注里明确写"改了哪个文件、为什么"——这是缺口二 kill criterion 的判定依据（第三个 agent 接入若被迫改核心逻辑超过一次，说明注册制抽象漏了维度）。
- **人时**：从拿到 agent 的 audit_log/trace_log 格式说明到该 agent 出现在仪表盘上为止。
- **类型**：`agent 接入` 或 `垂类新增`，与本行的 `workbench 能力构建` 区分，避免均值被污染。
