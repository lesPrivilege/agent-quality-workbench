"""Complexity Ladder Scorer — evaluates candidate use cases for agent suitability."""

import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

RUBRIC_PATH = Path(__file__).parent.parent / "rubric" / "complexity_ladder.yaml"
REPORTS_DIR = Path(__file__).parent.parent / "reports"


def load_rubric() -> dict:
    with open(RUBRIC_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def score_scenario(rubric: dict, scenario: dict) -> dict:
    """Score a single scenario against the rubric.

    Args:
        rubric: loaded rubric YAML
        scenario: dict with keys matching dimension names, values are scores

    Returns:
        dict with level, weighted_score, per-dimension details, recommendation
    """
    dimensions = rubric["dimensions"]
    levels = rubric["complexity_levels"]

    details = []
    weighted_sum = 0.0

    for dim in dimensions:
        name = dim["name"]
        raw_score = scenario.get(name, 0)
        weighted = raw_score * dim["weight"]
        weighted_sum += weighted
        details.append({
            "dimension": dim["label"],
            "raw_score": raw_score,
            "max_score": dim["max_score"],
            "weight": dim["weight"],
            "weighted": round(weighted, 3),
            "reason": scenario.get(f"{name}_reason", ""),
        })

    # Find level
    level_label = "未定义"
    recommendation = ""
    for lvl in levels:
        lo, hi = lvl["range"]
        if lo <= weighted_sum < hi:
            level_label = lvl["label"]
            recommendation = lvl["recommendation"]
            break

    return {
        "scenario": scenario["name"],
        "weighted_score": round(weighted_sum, 3),
        "level": level_label,
        "recommendation": recommendation,
        "dimensions": details,
    }


# Pre-defined scenarios
SCENARIOS = [
    {
        "name": "contract_approval",
        "label": "合同审批路由（正例）",
        "task_determinism": 2,
        "task_determinism_reason": "审批矩阵可查表，但关联方/条款风险需要语义判断",
        "irreversibility": 4,
        "irreversibility_reason": "签署/大额支付不可逆",
        "frequency": 5,
        "frequency_reason": "每天数十到数百份合同",
        "exception_rate": 3,
        "exception_rate_reason": "约5-10%需要HITL（大额/不可逆/关联方）",
        "unstructuredness": 2,
        "unstructuredness_reason": "结构化字段+自由文本条款描述",
        "risk_level": 4,
        "risk_level_reason": "涉及合规、财务、法律风险",
    },
    {
        "name": "compliance_review",
        "label": "合规审查（正例）",
        "task_determinism": 5,
        "task_determinism_reason": "合规判断需要综合多部法规，无法用规则穷举",
        "irreversibility": 5,
        "irreversibility_reason": "漏审条款可能导致法律风险",
        "frequency": 3,
        "frequency_reason": "中频，每次合同或交易触发",
        "exception_rate": 5,
        "exception_rate_reason": "异常率高，法规覆盖不完整、新业务类型常见",
        "unstructuredness": 5,
        "unstructuredness_reason": "法规是非结构化文本，材料半结构化",
        "risk_level": 5,
        "risk_level_reason": "直接涉及合规和法律风险",
    },
    {
        "name": "quote_generation",
        "label": "报价生成（反例）",
        "task_determinism": 0,
        "task_determinism_reason": "报价规则可穷举（产品目录+折扣规则+客户等级）",
        "irreversibility": 0,
        "irreversibility_reason": "报价是建议性质，可修改撤回",
        "frequency": 5,
        "frequency_reason": "高频，销售每天发数十份报价",
        "exception_rate": 0,
        "exception_rate_reason": "异常极少，规则覆盖99%+",
        "unstructuredness": 0,
        "unstructuredness_reason": "产品目录、客户等级、折扣表全是结构化数据",
        "risk_level": 0,
        "risk_level_reason": "低风险，报价是内部建议不具法律约束",
    },
]


def main():
    rubric = load_rubric()
    results = []

    for scenario in SCENARIOS:
        result = score_scenario(rubric, scenario)
        results.append(result)

    # Output
    REPORTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")

    # Print summary
    print("=" * 70)
    print("复杂度阶梯评分结果")
    print("=" * 70)
    for r in results:
        print(f"\n## {r['scenario']}")
        print(f"   加权得分: {r['weighted_score']}")
        print(f"   推荐等级: {r['level']}")
        print(f"   建议: {r['recommendation']}")
        print(f"   各维度:")
        for d in r["dimensions"]:
            bar = "█" * int(d["weighted"] * 10) + "░" * (10 - int(d["weighted"] * 10))
            print(f"     {d['dimension']:10s} {d['raw_score']}/{d['max_score']} × {d['weight']:.2f} = {d['weighted']:.3f} {bar}  {d['reason']}")

    # Write JSON
    out_path = REPORTS_DIR / f"complexity_scores_{date_str}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nJSON 报告已写入: {out_path}")

    # Write markdown
    md_path = REPORTS_DIR / f"complexity_scores_{date_str}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 复杂度阶梯评分报告 {date_str}\n\n")
        for r in results:
            f.write(f"## {r['scenario']}\n\n")
            f.write(f"| 指标 | 值 |\n|------|----|\n")
            f.write(f"| 加权得分 | {r['weighted_score']} |\n")
            f.write(f"| 推荐等级 | {r['level']} |\n")
            f.write(f"| 建议 | {r['recommendation']} |\n\n")
            f.write(f"| 维度 | 得分 | 权重 | 加权 | 理由 |\n")
            f.write(f"|------|------|------|------|------|\n")
            for d in r["dimensions"]:
                f.write(f"| {d['dimension']} | {d['raw_score']}/{d['max_score']} | {d['weight']:.2f} | {d['weighted']:.3f} | {d['reason']} |\n")
            f.write("\n")
    print(f"Markdown 报告已写入: {md_path}")


if __name__ == "__main__":
    main()
