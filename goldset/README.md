# Gold Set — 黄金测试集

每个 agent 的黄金测试集定义在 `goldset/<agent-name>.yaml` 中，是路由准确率评估的 ground truth。

## Case 格式

```yaml
cases:
  - id: <case_id>           # 与 audit_log 中的 case_id 对应
    expected_route: <route>  # 期望路由：auto | hitl | block
    failure_mode: <mode>     # 该 case 防守的失败模式
    source: <source>         # synthetic | historical
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

## 与 agents.yaml 的关系

`agents.yaml` 中每个 agent 通过 `goldset: goldset/<name>.yaml` 引用黄金测试集。
`expected_routes` 已迁移到 goldset 文件中，不再出现在 agents.yaml。
