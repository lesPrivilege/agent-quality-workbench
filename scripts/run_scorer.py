"""Complexity Ladder Scorer — evaluates candidate use cases for agent suitability."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

RUBRIC_PATH = Path(__file__).parent.parent / "rubric" / "complexity_ladder.yaml"
SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"
REPORTS_DIR = Path(__file__).parent.parent / "reports"


def load_rubric() -> dict:
    with open(RUBRIC_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_scenarios(name: str | None = None) -> list[dict]:
    """Load scenario YAML files from scenarios/ directory.

    Args:
        name: if provided, load only the scenario with this name.
    """
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            s = yaml.safe_load(f)
        if name is None or s.get("name") == name:
            scenarios.append(s)
    return scenarios


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
        if lo <= weighted_sum <= hi:
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



def main():
    parser = argparse.ArgumentParser(description="复杂度阶梯评分")
    parser.add_argument("--scenario", help="只跑指定场景（场景名）")
    args = parser.parse_args()

    rubric = load_rubric()
    scenarios = load_scenarios(args.scenario)

    if not scenarios:
        print(f"未找到场景: {args.scenario}", file=sys.stderr)
        sys.exit(1)

    results = []
    for scenario in scenarios:
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
