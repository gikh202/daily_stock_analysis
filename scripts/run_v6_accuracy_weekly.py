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
from src.v6_daily.research_governance import (
    enrich_accuracy_payload_from_stock_db,
    render_research_governance_markdown,
)


def _fmt(value: Any, *, suffix: str = "", digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _ci(metric: Mapping[str, Any], *, prefix: str = "hit_rate") -> str:
    low = metric.get(f"{prefix}_ci95_low_pct")
    high = metric.get(f"{prefix}_ci95_high_pct")
    if low is None or high is None:
        return "N/A"
    return f"{_fmt(low, suffix='%')}–{_fmt(high, suffix='%')}"


def render_weekly_markdown(payload: Mapping[str, Any]) -> str:
    definition = payload.get("strategy_return_definition") or {}
    alpha_definition = payload.get("alpha_target_definition") or {}
    return_method = payload.get("strategy_return_method") or "-"
    yearly_method = payload.get("yearly_walk_forward_method") or "-"
    selectivity_method = payload.get("selectivity_analysis_method") or "-"
    alpha_method = payload.get("alpha_target_method") or "-"
    calibration_method = payload.get("alpha_calibration_method") or "-"
    regime_method = payload.get("regime_matrix_method") or "-"
    lines = [
        "# V6.4 Accuracy / Alpha Governance 周报",
        "",
        "> 该周报来自严格 no-lookahead 的历史价格/成交量/基准回放，仅用于研究；不会自动调权、修改生产阈值或升级 Challenger。",
        "",
        f"- 方法：**{payload.get('method', '-')}**",
        f"- 范围：{payload.get('scope', '-')} ",
        f"- 总观察记录：**{payload.get('observations', 0)}**",
        f"- 最小研究样本：**{payload.get('minimum_samples', 50)}**",
        f"- Challenger 晋级研究门槛：**{payload.get('promotion_min_samples', 100)}**",
        f"- 方向收益口径：**{return_method}**（bullish={_fmt(definition.get('bullish_position'), suffix='x')} / bearish={_fmt(definition.get('bearish_position'), suffix='x')} / neutral={_fmt(definition.get('neutral_position'), suffix='x')}；基准={definition.get('benchmark', '-')}；交易成本={definition.get('trading_costs', '-')}）",
        f"- 年度稳定性口径：**{yearly_method}**（先在完整时间轴选出非重叠样本，再按自然年切片，避免跨年边界重复计入）",
        f"- 置信度选择性口径：**{selectivity_method}**（仅方向信号；按超过 bullish/bearish 触发阈值的分数余量筛选，再在完整时间轴做非重叠抽样）",
        f"- Alpha Target 口径：**{alpha_method}**（基准={alpha_definition.get('benchmark', 'SPY')}；neutral 不参与；方向目标直接判断未来相对 SPY 的超额方向）",
        f"- Score Calibration 口径：**{calibration_method}**（固定 0–2 / 2–5 / 5–10 / 10pt+ 桶，不按本次历史结果自动寻优）",
        f"- Regime Matrix 口径：**{regime_method}**（仅使用 as-of 时点及以前的 SPY 20D/60D 趋势与波动信息）",
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
        delta = item.get("hit_rate_delta_vs_champion_pp")
        lines.append(
            "| {variant} | {h}D | {raw_n} | {raw_hit} | {n} | {hit} | {ci} | {strategy_return} | {excess} | {delta} | {candidate} |".format(
                variant=item.get("variant") or "-",
                h=item.get("horizon_days") or "-",
                raw_n=raw.get("samples", 0),
                raw_hit=_fmt(raw.get("directional_hit_rate_pct"), suffix="%"),
                n=independent.get("samples", 0),
                hit=_fmt(independent.get("directional_hit_rate_pct"), suffix="%"),
                ci=_ci(independent),
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

    lines.extend(["", "## Champion 置信度 / 选择性研究", ""])
    lines.append(
        "> `分数余量` 是预测分数超过该周期 bullish/bearish 触发阈值的点数。先筛选再做 non-overlap，模拟“只有高置信方向信号才占用交易窗口”的研究策略；该结果不会自动改变生产阈值。"
    )
    lines.append("")
    lines.append(
        "| 周期 | 最低分数余量 | 参与率 | 原始N | 非重叠N | 非重叠命中 | 非重叠95% CI | 非重叠策略收益 | 非重叠SPY超额 |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---|---:|---:|")
    for item in results:
        if item.get("variant") != "champion":
            continue
        for slice_item in list(item.get("selectivity_analysis") or []):
            independent = slice_item.get("non_overlapping") or {}
            lines.append(
                "| {h}D | ≥{margin} | {participation} | {raw_n} | {n} | {hit} | {ci} | {strategy_return} | {excess} |".format(
                    h=item.get("horizon_days") or "-",
                    margin=_fmt(slice_item.get("min_margin_points"), suffix="pt", digits=0),
                    participation=_fmt(slice_item.get("participation_rate_pct"), suffix="%"),
                    raw_n=(slice_item.get("raw") or {}).get("samples", 0),
                    n=independent.get("samples", 0),
                    hit=_fmt(independent.get("directional_hit_rate_pct"), suffix="%"),
                    ci=_ci(independent),
                    strategy_return=_fmt(independent.get("avg_return_pct"), suffix="%", digits=2),
                    excess=_fmt(independent.get("avg_excess_vs_spy_pct"), suffix="%", digits=2),
                )
            )

    lines.extend(["", "## Champion Alpha Target（相对 SPY）", ""])
    lines.append(
        "> Alpha Target 只评估已经给出 bullish/bearish 的方向信号。bullish 要求 `股票未来收益 - SPY未来收益 > 0`，bearish 要求该差值 `< 0`；Alpha Spread 为方向化后的相对收益研究值。"
    )
    lines.append("")
    lines.append(
        "| 周期 | 原始方向N | 非重叠N | Alpha命中 | 95% CI | 平均Alpha Spread | 中位Alpha Spread |"
    )
    lines.append("|---:|---:|---:|---:|---|---:|---:|")
    for item in results:
        if item.get("variant") != "champion":
            continue
        alpha = item.get("alpha_target") or {}
        raw = alpha.get("raw") or {}
        independent = alpha.get("non_overlapping") or {}
        lines.append(
            "| {h}D | {raw_n} | {n} | {hit} | {ci} | {avg} | {median} |".format(
                h=item.get("horizon_days") or "-",
                raw_n=raw.get("samples", 0),
                n=independent.get("samples", 0),
                hit=_fmt(independent.get("alpha_hit_rate_pct"), suffix="%"),
                ci=_ci(independent, prefix="alpha_hit"),
                avg=_fmt(independent.get("avg_alpha_trade_return_pct"), suffix="%", digits=2),
                median=_fmt(independent.get("median_alpha_trade_return_pct"), suffix="%", digits=2),
            )
        )

    lines.extend(["", "## Champion Score Calibration（固定分数余量桶）", ""])
    lines.append(
        "> 固定桶只检验“分数余量越大是否对应更好的未来 Alpha”这一校准关系；不会从本次历史中挑最佳桶并改写生产阈值。"
    )
    lines.append("")
    lines.append(
        "| 周期 | 分数余量桶 | 方向占比 | 原始N | 非重叠N | Alpha命中 | 95% CI | 平均Alpha Spread |"
    )
    lines.append("|---:|---|---:|---:|---:|---:|---|---:|")
    for item in results:
        if item.get("variant") != "champion":
            continue
        for bucket in list(item.get("alpha_calibration") or []):
            independent = bucket.get("non_overlapping") or {}
            lines.append(
                "| {h}D | {label} | {share} | {raw_n} | {n} | {hit} | {ci} | {avg} |".format(
                    h=item.get("horizon_days") or "-",
                    label=bucket.get("label") or "-",
                    share=_fmt(bucket.get("directional_share_pct"), suffix="%"),
                    raw_n=(bucket.get("raw") or {}).get("samples", 0),
                    n=independent.get("samples", 0),
                    hit=_fmt(independent.get("alpha_hit_rate_pct"), suffix="%"),
                    ci=_ci(independent, prefix="alpha_hit"),
                    avg=_fmt(independent.get("avg_alpha_trade_return_pct"), suffix="%", digits=2),
                )
            )

    lines.extend(["", "## Champion SPY Regime Matrix", ""])
    lines.append(
        "> Regime 只使用预测当时可见的 SPY 历史：趋势按 trailing 20D/60D 收益同号划分 `up/down/mixed`；波动按 20D 年化波动是否高于 60D 划分 `expanding/contracting`。先做全时间轴 Alpha non-overlap，再按 Regime 分组。"
    )
    lines.append("")
    lines.append(
        "| 周期 | SPY趋势 | SPY波动 | 原始N | 非重叠N | Alpha命中 | 95% CI | 平均Alpha Spread |"
    )
    lines.append("|---:|---|---|---:|---:|---:|---|---:|")
    for item in results:
        if item.get("variant") != "champion":
            continue
        for regime in list(item.get("regime_matrix") or []):
            independent = regime.get("non_overlapping") or {}
            lines.append(
                "| {h}D | {trend} | {vol} | {raw_n} | {n} | {hit} | {ci} | {avg} |".format(
                    h=item.get("horizon_days") or "-",
                    trend=regime.get("spy_trend_regime") or "unknown",
                    vol=regime.get("spy_vol_regime") or "unknown",
                    raw_n=(regime.get("raw") or {}).get("samples", 0),
                    n=independent.get("samples", 0),
                    hit=_fmt(independent.get("alpha_hit_rate_pct"), suffix="%"),
                    ci=_ci(independent, prefix="alpha_hit"),
                    avg=_fmt(independent.get("avg_alpha_trade_return_pct"), suffix="%", digits=2),
                )
            )

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
            lines.append(
                "| {year} | {raw_n} | {raw_hit} | {raw_return} | {raw_excess} | {n} | {hit} | {ci} | {strategy_return} | {excess} |".format(
                    year=year.get("year") or "-",
                    raw_n=raw.get("samples", 0),
                    raw_hit=_fmt(raw.get("directional_hit_rate_pct"), suffix="%"),
                    raw_return=_fmt(raw.get("avg_return_pct"), suffix="%", digits=2),
                    raw_excess=_fmt(raw.get("avg_excess_vs_spy_pct"), suffix="%", digits=2),
                    n=independent.get("samples", 0),
                    hit=_fmt(independent.get("directional_hit_rate_pct"), suffix="%"),
                    ci=_ci(independent),
                    strategy_return=_fmt(independent.get("avg_return_pct"), suffix="%", digits=2),
                    excess=_fmt(independent.get("avg_excess_vs_spy_pct"), suffix="%", digits=2),
                )
            )
        lines.append("")

    governance_markdown = render_research_governance_markdown(payload)
    if governance_markdown:
        lines.extend(["", governance_markdown, ""])

    lines.extend(
        [
            "## 安全约束",
            "",
            f"- 自动调权：**{payload.get('auto_weight_tuning', False)}**",
            f"- 自动晋级：**{payload.get('auto_promotion', False)}**",
            "- 当前 SEC/FRED 快照不会回填历史日期，避免未来数据泄漏。",
            "- 相邻日预测会共享未来窗口，因此晋级判断优先使用非重叠样本。",
            "- 年度非重叠样本来自完整时间轴上的同一非重叠集合，再按年份分组；不会在每年年初重新启动抽样导致跨年窗口重复计数。",
            "- 置信度选择性切片只用于发现‘少出手是否更有效’的研究关系；不会参与 Challenger 晋级，也不会自动修改生产方向阈值。",
            "- Alpha Target、Score Calibration 与 Regime Matrix 都是研究旁路，不参与既有 Challenger Promotion Gate，也不会自动选择历史最佳阈值或市场状态。",
            "- Alpha Spread 是无杠杆、未计交易成本/滑点/借券成本的相对价值研究口径，不等同于可执行组合收益。",
            "- 方向策略收益是无杠杆、未计交易成本的研究口径；真实 BUY_SETUP 交易计划仍由单独的保守执行回放评估。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the weekly V6.4 accuracy/alpha governance report")
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
    payload = enrich_accuracy_payload_from_stock_db(
        payload,
        args.stock_db,
        codes=codes or None,
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