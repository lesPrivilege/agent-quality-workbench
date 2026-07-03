# Verticals — 垂类插槽机制

每个垂类是一个目录，包含 `profile.yaml` 配置文件。垂类用于对 rubric 权重、阈值、风险规则模板进行行业级定制。

## 新增垂类三步

1. **建 profile.yaml**：`verticals/<name>/profile.yaml`，字段 schema 见下方
2. **agents.yaml 挂 vertical**：在 agent 条目中加 `vertical: <name>`
3. **（可选）scenarios 加该垂类场景**：`scenarios/` 目录下的场景文件可带 `vertical` 字段

## profile.yaml 字段 schema

```yaml
name: <string>              # 垂类标识符，与目录名一致
label: <string>             # 中文显示名
description: <string>       # 垂类描述

rubric_overrides:           # 可选 — 复杂度阶梯的权重覆盖
  weights:
    <dimension_name>: <float>   # 覆盖全局 rubric 中该维度的权重
    # 未列出的维度沿用全局值
    # 覆盖后 weights 之和必须 = 1.0（scorer 启动时校验）

threshold_presets:           # 可选 — 垂类级阈值默认值
  <metric_key>:
    green: [lo, hi]
    yellow: [lo, hi]
    red: [lo, hi]
  # 优先级：agent thresholds_override > vertical threshold_presets > global thresholds.yaml

risk_rule_templates:         # 可选 — 风险信号模板，agent 的 risk_rules 可通过 {template: name} 引用
  <template_name>:
    path: <field_path>
    equals: <value>
    # 支持所有 evaluate_rule 语法：equals / contains / gt / lt / all / any
```

## 阈值优先级

```
agent thresholds_override > vertical threshold_presets > global thresholds.yaml
```

仪表盘中被 override 的指标在说明列标注来源：`（agent 阈值）` 或 `（垂类阈值）`。

## 现有垂类

- `legal-compliance`：法务合规 — 合同/合规类审批场景

## 接入 trace 的三步

轨迹级评估（step efficiency、tool argument correctness）需要 agent 输出步骤事件。

1. **Agent 输出 `data/trace_log.jsonl`**：每行一个步骤事件，schema 见 `eval/trace.py`
2. **（可选）配置 `trace_field_map`**：在 `agents.yaml` 的 agent 条目中映射字段名
3. **重跑 dashboard**：步骤效率和工具参数正确率自动从 ⚪ 降级为实际值

trace 数据不存在时，两个指标显示 `⚪ 无 trace 数据`，不影响其他指标。
