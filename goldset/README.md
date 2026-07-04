# Gold Set — 黄金测试集

每个 agent 的黄金测试集定义在 `goldset/<agent-name>.yaml` 中，是路由准确率评估的 ground truth。

## Case 格式

```yaml
cases:
  - id: <case_id>           # 与 audit_log 中的 case_id 对应
    expected_route: <route>  # 期望路由：auto | hitl | block
    failure_mode: <mode>     # 该 case 防守的失败模式
    source: <source>         # synthetic | historical
    last_verified: <date>    # 上次人工复验日期，YYYY-MM-DD，见下方说明
    note: <string>           # 该 case 在集合中的理由
```

## failure_mode 枚举

| 模式 | 含义 | 示例 |
|------|------|------|
| `routing_error` | 该 HITL 的被 auto，或该 auto 的被推给人 | 低风险合同被不必要地推给人工 |
| `guardrail_gap` | 该 block 的放行 | 大额不可逆合同未被 guardrail 拦截 |
| `over_escalation` | 该 auto 的被过度升级 | 无风险材料被错误触发 HITL |
| `silent_failure` | 风险信号被静默放过 | 有关联方标记但 auto_approved |
| `calibration` | 置信度失真 | M013 式：法规库 0 结果但 confidence 0.82 |

允许后续扩展——新增枚举值只需在本文件补充。

## source 字段

- `synthetic`：人工构造的测试 case，用于覆盖已知路由路径
- `historical`：从真实历史失败案例中提取，有具体的失败记录

当前两个 demo agent 的 gold set 全部为 `source: synthetic`。
historical 占比会在仪表盘中单列显示——这本身就是对"用真实失败案例充实测试集"的提醒。

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

## 与 agents.yaml 的关系

`agents.yaml` 中每个 agent 通过 `goldset: goldset/<name>.yaml` 引用黄金测试集。
`expected_routes` 已迁移到 goldset 文件中，不再出现在 agents.yaml。
