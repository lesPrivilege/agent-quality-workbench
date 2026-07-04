"""Aggregate staleness + drift + regulation_staleness into one report.

Compute/render split, mirroring eval/snapshot.py (compute) and
eval/metrics.py::render_markdown (render) for the dashboard.

recompile_triggers_<date>.json is a new compiled-artifact contract in its
own right (schema_version included) — a future conformance checker would
validate against it the same way it would validate trace_log.jsonl.
"""

from __future__ import annotations

from datetime import date

SCHEMA_VERSION = "1.0"


def build_recompile_snapshot(agent_reports: list[dict], today: date) -> dict:
    total_stale_cases = sum(a["goldset_staleness"]["stale_count"] for a in agent_reports)
    total_drift_events = sum(len(a["metric_drift"]["events"]) for a in agent_reports)
    agents_instrumented = sum(
        1 for a in agent_reports if a["regulation_staleness"]["instrumented"]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "date": today.isoformat(),
        "summary": {
            "total_stale_cases": total_stale_cases,
            "total_drift_events": total_drift_events,
            "agents_instrumented_for_regulation_staleness": agents_instrumented,
        },
        "agents": agent_reports,
    }


def render_recompile_markdown(snapshot: dict) -> str:
    lines = [f"# 重编译触发报告 — {snapshot['date']}", ""]
    s = snapshot["summary"]
    lines.append(
        f"**总览**：过期 case {s['total_stale_cases']} 个，漂移事件 {s['total_drift_events']} 个，"
        f"{s['agents_instrumented_for_regulation_staleness']} 个 agent 已接入模式三检测。"
    )
    lines.append("")

    for agent in snapshot["agents"]:
        lines.append(f"## {agent['name']}")
        lines.append("")

        gs = agent["goldset_staleness"]
        lines.append(f"### Goldset 时效性（过期阈值 {gs['max_age_days']} 天，age proxy）")
        if gs["stale_count"] == 0:
            lines.append("当前无过期 case。")
        else:
            lines.append(f"过期 {gs['stale_count']}/{gs['total_cases']}（{gs['stale_ratio']:.1%}）：")
            for c in gs["stale_cases"]:
                if c["reason"] == "missing_last_verified":
                    lines.append(f"- {c['id']}：缺少 last_verified 字段")
                else:
                    lines.append(f"- {c['id']}：{c['age_days']} 天未复验")
        lines.append("")

        md = agent["metric_drift"]
        lines.append(f"### 指标漂移（窗口 N={md['window_size']}）")
        if md["insufficient_history"]:
            lines.append(f"历史数据不足（{md['history_count']}/{md['required_count']} 条），暂不比较。")
        elif not md["events"] and not md["improvements"]:
            lines.append("当前无漂移。")
        else:
            for e in md["events"]:
                lines.append(
                    f"- 🔴 **{e['metric']}**：{e['old_status']}→{e['new_status']}"
                    f"（{e['old_avg']:.3f}→{e['new_avg']:.3f}，"
                    f"{e['old_window_span']['from']}~{e['old_window_span']['to']} → "
                    f"{e['new_window_span']['from']}~{e['new_window_span']['to']}）"
                )
            for i in md["improvements"]:
                lines.append(f"- 🟢 improvement：{i['metric']} {i['old_status']}→{i['new_status']}")
        lines.append("")

        rs = agent["regulation_staleness"]
        lines.append("### 模式三：法规时效失效")
        if not rs["instrumented"]:
            lines.append("未接入检测（audit_log 未输出 regulation_refs 字段，契约已定义见 eval/regulation_staleness.py）。")
        elif rs["stale_count"] == 0:
            lines.append("已接入检测，当前无过期法规引用。")
        else:
            lines.append(f"发现 {rs['stale_count']} 条过期法规引用：")
            for ref in rs["stale_refs"]:
                lines.append(f"- case {ref['case_id']} 引用 {ref['regulation_id']}（已于 {ref['effective_until']} 失效）")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "已知盲区：指标漂移检测只能抓「越级」变化（如 green→yellow），"
        "同一色带内的缓慢滑坡（如 98%→96%→95.5%，一直是 green）测不到。"
    )

    return "\n".join(lines)
