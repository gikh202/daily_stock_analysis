from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.lab_replay import replay_stock_db_accuracy_lab


def _fmt(value: Any, *, suffix: str = "", digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def render_weekly_markdown(payload: Mapping[str, Any]) -> str:
    definition = payload.get("strategy_return_definition") or {}
    return_method = payload.get("strategy_return_method") or "-"
    yearly_method = payload.get("yearly_walk_forward_method") or "-"
    lines = [
        "# V6.2 准确率研究周报",
        "",
        "> 该周报来自严格 no-lookahead 的历史价格/成交量/基准回放，仅用于研究；不会自动调权或升级 Challenger。",
        "",
        f"- 方法：**{payload.get('method', '-')}**",
        f"- 范围：{payload.get('scope', '-')} ",
        f"- 总观察记录：**{payload.get('observations', 0)}**",
        f"- 最小研究样本：**{payload.get('minimum_samples', 50)}**",
        f"- Challenger 晋级研究门槛：**{payload.get('promotion_min_samples', 100)}**",
        f"- 方向收益口径：**{return_method}**（bullish={_fmt(definition.get('bullish_position'), suffix='x')} / bearish={_fmt(definition.get('bearish_position'), suffix='x')} / neutral={_fmt(definition.get('neutral_position'), suffix='x')}；基准={definition.get('benchmark', '-')}；交易成本={definition.get('trading_costs', '-')}）",
        f"- 年度稳定性口径：**{yearly_method}**（先在完整时间轴选出非重叠样本，再按自然年切片，避免跨年边界重复计入）",
        "",
        "## Champion / Challenger",
        "",
        "| 模型 | 周期 | 原始N | 原始命中 | 非重叠N | 非重叠命中 | 95% CI | 方向策略收益 | 方向策略SPY超额 | 相对Champion | 晋级候选 |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|",
    ]
    results = list(payload.get("results") or [])
    for item in results:
        raw = item.get("raw") or {}
        independent = item.get("non_overlapping") or {}
        low = independent.get("hit_rate_ci95_low_pct")
        high = independent.get("hit_rate_ci95_high_pct")
        ci = "N/A" if low is None or high is None else f"{_fmt(low, suffix='%')}–{_fmt(high, suffix='%')}"
        delta = item.get("hit_rate_delta_vs_champion_pp")
        lines.append(
            "| {variant} | {h}D | {raw_n} | {raw_hit} | {n} | {hit} | {ci} | {strategy_return} | {excess} | {delta} | {candidate} |".format(
                variant=item.get("variant") or "-",
                h=item.get("horizon_days") or "-",
                raw_n=raw.get("samples", 0),
                raw_hit=_fmt(raw.get("directional_hit_rate_pct"), suffix="%"),
                n=independent.get("samples", 0),
                hit=_fmt(independent.get("directional_hit_rate_pct"), suffix="%"),
                ci=ci,
                strategy_return=_fmt(independent.get("avg_return_pct"), suffix="%", digits=2),
                excess=_fmt(independent.get("avg_excess_vs_spy_pct"), suffix="%", digits=2),
                delta="N/A" if delta is None else _fmt(delta, suffix="pp", digits=1),
                candidate="是（研究）" if item.get("promotion_candidate") else "否",
            )
        )
    if not results:
        lines.append("| - | - | 0 | N/A | 0 | N/A | N/A | N/A | N/A | N/A | 否 |")

    candidates = list(payload.get("promotion_candidates") or [])
    lines.extend(["", "## 研究结论", ""])
    if candidates:
        lines.append("- 出现满足当前统计门槛的 Challenger 研究候选：")
        for item in candidates:
            lines.append(
                f"  - `{item.get('variant')}` · {item.get('horizon_days')}D"
            )
        lines.append("- 这只是进入人工/PR 审核的资格，不代表自动修改生产权重。")
    else:
        lines.append("- 当前没有 Challenger 达到晋级研究门槛；继续积累独立样本。")

    lines.extend(["", "## 年度 Walk-forward", ""])
    for item in results:
        if item.get("variant") != "champion":
            continue
        yearly = list(item.get("yearly_walk_forward") or [])
        if not yearly:
            continue
        lines.append(f"### Champion · {item.get('horizon_days')}D")
        lines.append("")
        lines.append(
            "| 年份 | 原始N | 原始命中 | 原始策略收益 | 原始SPY超额 | 非重叠N | 非重叠命中 | 非重叠95% CI | 非重叠策略收益 | 非重叠SPY超额 |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|---:|---:|")
        for year in yearly:
            raw = year.get("raw") or year
            independent = year.get("non_overlapping") or {}
            low = independent.get("hit_rate_ci95_low_pct")
            high = independent.get("hit_rate_ci95_high_pct")
            ci = "N/A" if low is None or high is None else f"{_fmt(low, suffix='%')}–{_fmt(high, suffix='%')}"
            lines.append(
                "| {year} | {raw_n} | {raw_hit} | {raw_return} | {raw_excess} | {n} | {hit} | {ci} | {strategy_return} | {excess} |".format(
                    year=year.get("year") or "-",
                    raw_n=raw.get("samples", 0),
                    raw_hit=_fmt(raw.get("directional_hit_rate_pct"), suffix="%"),
                    raw_return=_fmt(raw.get("avg_return_pct"), suffix="%", digits=2),
                    raw_excess=_fmt(raw.get("avg_excess_vs_spy_pct"), suffix="%", digits=2),
                    n=independent.get("samples", 0),
                    hit=_fmt(independent.get("directional_hit_rate_pct"), suffix="%"),
                    ci=ci,
                    strategy_return=_fmt(independent.get("avg_return_pct"), suffix="%", digits=2),
                    excess=_fmt(independent.get("avg_excess_vs_spy_pct"), suffix="%", digits=2),
                )
            )
        lines.append("")

    lines.extend(
        [
            "## 安全约束",
            "",
            f"- 自动调权：**{payload.get('auto_weight_tuning', False)}**",
            f"- 自动晋级：**{payload.get('auto_promotion', False)}**",
            "- 当前 SEC/FRED 快照不会回填历史日期，避免未来数据泄漏。",
            "- 相邻日预测会共享未来窗口，因此晋级判断优先使用非重叠样本。",
            "- 年度非重叠样本来自完整时间轴上的同一非重叠集合，再按年份分组；不会在每年年初重新启动抽样导致跨年窗口重复计数。",
            "- 方向策略收益是无杠杆、未计交易成本的研究口径；真实 BUY_SETUP 交易计划仍由单独的保守执行回放评估。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the weekly V6.2 accuracy research report")
    parser.add_argument("--stock-db", default="data/stock_analysis.db")
    parser.add_argument("--codes", default="")
    parser.add_argument("--output-dir", default="v6_reports/accuracy_weekly")
    parser.add_argument("--min-samples", type=int, default=50)
    parser.add_argument("--promotion-min-samples", type=int, default=100)
    args = parser.parse_args()

    codes = [value.strip().upper() for value in args.codes.split(",") if value.strip()]
    payload = replay_stock_db_accuracy_lab(
        args.stock_db,
        codes=codes or None,
        min_samples=max(3, int(args.min_samples)),
        promotion_min_samples=max(int(args.promotion_min_samples), int(args.min_samples)),
    )

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "v6_accuracy_weekly.json"
    md_path = output / "v6_accuracy_weekly.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    md_path.write_text(render_weekly_markdown(payload), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "observations": payload.get("observations", 0)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
