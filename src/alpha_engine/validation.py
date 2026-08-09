from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .shadow_store import ALPHA_SCHEMA_VERSION, AlphaShadowStore


VALIDATION_VERSION = "v6.0-validation.1"
DEFAULT_MIN_SAMPLES = 50
DEFAULT_PRIMARY_HORIZON = 5


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _average_ranks(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[indexed[position][0]] = average_rank
        cursor = end
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denom_x = math.sqrt(sum(value * value for value in dx))
    denom_y = math.sqrt(sum(value * value for value in dy))
    denominator = denom_x * denom_y
    if denominator <= 0:
        return None
    return sum(left * right for left, right in zip(dx, dy)) / denominator


def _spearman(pairs: Iterable[Tuple[Any, Any]]) -> Tuple[Optional[float], int]:
    clean: List[Tuple[float, float]] = []
    for left, right in pairs:
        x = _finite(left)
        y = _finite(right)
        if x is not None and y is not None:
            clean.append((x, y))
    if len(clean) < 3:
        return None, len(clean)
    xs = [pair[0] for pair in clean]
    ys = [pair[1] for pair in clean]
    return _pearson(_average_ranks(xs), _average_ranks(ys)), len(clean)


def _strategy_return(decision: str, return_pct: Any) -> Optional[float]:
    """Executable long-only research return.

    AVOID means no position. It must never be inverted and counted as a short.
    A future explicit SHORT_SETUP can add inverse-return semantics separately.
    """
    value = _finite(return_pct)
    if value is None:
        return None
    return value if str(decision or "").strip().upper() == "BUY_SETUP" else None


def _profit_factor(returns: Sequence[float]) -> Tuple[Optional[float], bool]:
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    if losses <= 0:
        return (None, gains > 0)
    return gains / losses, False


def _max_drawdown_proxy_pct(returns: Sequence[float]) -> Optional[float]:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= max(0.0, 1.0 + value / 100.0)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return 100.0 * worst


def _sharpe_like(returns: Sequence[float], horizon_days: int) -> Optional[float]:
    if len(returns) < 2:
        return None
    deviation = statistics.stdev(returns)
    if deviation <= 0:
        return None
    annualization = math.sqrt(252.0 / max(1, int(horizon_days)))
    return statistics.fmean(returns) / deviation * annualization


def _median(values: Sequence[float]) -> Optional[float]:
    return None if not values else float(statistics.median(values))


def build_validation_summary(
    store: AlphaShadowStore,
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    primary_horizon: int = DEFAULT_PRIMARY_HORIZON,
) -> Dict[str, Any]:
    """Build deterministic, read-only research metrics from matured outcomes."""
    minimum = max(3, int(min_samples))
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                s.id AS signal_id,
                s.code,
                s.analysis_created_at,
                s.decision,
                s.market_regime,
                s.opportunity_score,
                s.risk_score,
                s.confidence,
                o.horizon_days,
                o.return_pct,
                o.mfe_pct,
                o.mae_pct,
                o.directional_hit
            FROM alpha_outcomes o
            JOIN alpha_signals s ON s.id=o.signal_id
            ORDER BY o.horizon_days ASC, s.analysis_created_at ASC, s.id ASC
            """
        ).fetchall()
        total_signals = int(conn.execute("SELECT COUNT(*) FROM alpha_signals").fetchone()[0])
        evaluated_signals = int(
            conn.execute("SELECT COUNT(DISTINCT signal_id) FROM alpha_outcomes").fetchone()[0]
        )

    grouped: Dict[int, List[Any]] = defaultdict(list)
    for row in rows:
        grouped[int(row["horizon_days"])].append(row)

    horizons: List[Dict[str, Any]] = []
    for horizon_days in sorted(grouped):
        bucket = grouped[horizon_days]
        strategy_returns: List[float] = []
        raw_returns: List[float] = []
        overall_directional_hits: List[int] = []
        buy_hits: List[int] = []
        avoid_hits: List[int] = []
        avoided_returns: List[float] = []
        evidence_values: List[float] = []

        for row in bucket:
            decision = str(row["decision"] or "").strip().upper()
            raw = _finite(row["return_pct"])
            if raw is not None:
                raw_returns.append(raw)
            executable = _strategy_return(decision, raw)
            if executable is not None:
                strategy_returns.append(executable)

            hit = None if row["directional_hit"] is None else int(row["directional_hit"])
            if hit is not None:
                overall_directional_hits.append(hit)
                if decision == "BUY_SETUP":
                    buy_hits.append(hit)
                elif decision == "AVOID":
                    avoid_hits.append(hit)

            if decision == "AVOID" and raw is not None:
                avoided_returns.append(raw)

            evidence = _finite(row["confidence"])
            if evidence is not None:
                evidence_values.append(evidence)

        opportunity_ic, opportunity_ic_samples = _spearman(
            (row["opportunity_score"], row["return_pct"]) for row in bucket
        )
        profit_factor, profit_factor_unbounded = _profit_factor(strategy_returns)

        def hit_rate(values: Sequence[int]) -> Optional[float]:
            return None if not values else 100.0 * sum(values) / len(values)

        avoid_negative = [value for value in avoided_returns if value <= 0]
        false_avoid = [value for value in avoided_returns if value > 0]
        avg_avoided_downside = (
            None
            if not avoid_negative
            else statistics.fmean(-value for value in avoid_negative)
        )

        horizons.append(
            {
                "horizon_days": horizon_days,
                "samples": len(bucket),
                "mature": len(bucket) >= minimum,
                "directional_samples": len(overall_directional_hits),
                "directional_hit_rate_pct": _round(hit_rate(overall_directional_hits), 2),
                "buy_samples": len(strategy_returns),
                "buy_directional_samples": len(buy_hits),
                "buy_directional_hit_rate_pct": _round(hit_rate(buy_hits), 2),
                "avoidance_samples": len(avoided_returns),
                "avoidance_hit_rate_pct": _round(hit_rate(avoid_hits), 2),
                "false_avoid_rate_pct": _round(
                    100.0 * len(false_avoid) / len(avoided_returns)
                    if avoided_returns else None,
                    2,
                ),
                "avg_avoided_return_pct": _round(
                    statistics.fmean(avoided_returns) if avoided_returns else None
                ),
                "avg_avoided_downside_pct": _round(avg_avoided_downside),
                "avg_raw_return_pct": _round(
                    statistics.fmean(raw_returns) if raw_returns else None
                ),
                "strategy_samples": len(strategy_returns),
                "avg_strategy_return_pct": _round(
                    statistics.fmean(strategy_returns) if strategy_returns else None
                ),
                "median_strategy_return_pct": _round(_median(strategy_returns)),
                "strategy_volatility_pct": _round(
                    statistics.stdev(strategy_returns) if len(strategy_returns) >= 2 else None
                ),
                "signal_sharpe_proxy": _round(
                    _sharpe_like(strategy_returns, horizon_days), 3
                ),
                "profit_factor": _round(profit_factor, 3),
                "profit_factor_unbounded": bool(profit_factor_unbounded),
                "sequence_max_drawdown_proxy_pct": _round(
                    _max_drawdown_proxy_pct(strategy_returns), 3
                ),
                "opportunity_ic_spearman": _round(opportunity_ic, 4),
                "opportunity_ic_samples": opportunity_ic_samples,
                "avg_evidence_coverage_pct": _round(
                    100.0 * statistics.fmean(evidence_values) if evidence_values else None,
                    2,
                ),
            }
        )

    selected = next(
        (item for item in horizons if int(item["horizon_days"]) == int(primary_horizon)),
        horizons[0] if horizons else None,
    )

    checks: Dict[str, Optional[bool]] = {
        "sample_size": None,
        "buy_directional_hit_rate": None,
        "profit_factor": None,
        "drawdown_proxy": None,
        "opportunity_ic": None,
    }
    status = "insufficient_data"
    if selected is not None:
        checks["sample_size"] = selected["samples"] >= minimum
        checks["buy_directional_hit_rate"] = (
            selected["buy_directional_samples"] >= minimum
            and selected["buy_directional_hit_rate_pct"] is not None
            and selected["buy_directional_hit_rate_pct"] >= 52.0
        )
        checks["profit_factor"] = (
            selected["strategy_samples"] >= minimum
            and (
                selected["profit_factor_unbounded"]
                or (
                    selected["profit_factor"] is not None
                    and selected["profit_factor"] >= 1.20
                )
            )
        )
        checks["drawdown_proxy"] = (
            selected["strategy_samples"] >= minimum
            and selected["sequence_max_drawdown_proxy_pct"] is not None
            and selected["sequence_max_drawdown_proxy_pct"] <= 20.0
        )
        checks["opportunity_ic"] = (
            selected["opportunity_ic_samples"] >= minimum
            and selected["opportunity_ic_spearman"] is not None
            and selected["opportunity_ic_spearman"] >= 0.03
        )
        if checks["sample_size"]:
            status = "research_pass" if all(value is True for value in checks.values()) else "research_hold"

    return {
        "validation_version": VALIDATION_VERSION,
        "schema_version": ALPHA_SCHEMA_VERSION,
        "minimum_samples": minimum,
        "primary_horizon_days": None if selected is None else int(selected["horizon_days"]),
        "coverage": {
            "signals": total_signals,
            "evaluated_signals": evaluated_signals,
            "evaluated_signal_pct": _round(
                100.0 * evaluated_signals / total_signals if total_signals else 0.0,
                2,
            ),
            "outcomes": len(rows),
        },
        "research_gate": {
            "status": status,
            "checks": checks,
            "thresholds": {
                "minimum_samples": minimum,
                "buy_directional_hit_rate_pct": 52.0,
                "profit_factor": 1.20,
                "sequence_max_drawdown_proxy_pct": 20.0,
                "opportunity_ic_spearman": 0.03,
            },
            "production_effect": "none",
        },
        "horizons": horizons,
        "methodology_notes": [
            "All outcomes use trading bars strictly after the original analysis date.",
            "Opportunity IC is Spearman rank correlation versus future raw return.",
            "BUY_SETUP is the only long strategy-return sample; WATCH/WAIT/AVOID are not positions.",
            "AVOID is evaluated separately as avoidance accuracy and is never treated as an implicit short.",
            "The legacy confidence column is interpreted as evidence coverage, not calibrated win probability.",
            "Sharpe and drawdown are signal-sequence proxies because horizon outcomes can overlap; they are not executable portfolio statistics.",
            "Research gate is descriptive only and never changes V4 advice, Alpha weights, or order execution.",
        ],
    }


def _format_pct(value: Any) -> str:
    number = _finite(value)
    return "N/A" if number is None else f"{number:.2f}%"


def _format_number(value: Any, digits: int = 3) -> str:
    number = _finite(value)
    return "N/A" if number is None else f"{number:.{digits}f}"


def render_validation_markdown(summary: Dict[str, Any], *, title: str = "V6 Alpha Validation") -> str:
    coverage = summary.get("coverage") or {}
    gate = summary.get("research_gate") or {}
    checks = gate.get("checks") or {}
    lines = [
        f"# {title}",
        "",
        f"- Validation version: `{summary.get('validation_version', '-')}`",
        f"- Signals: **{coverage.get('signals', 0)}**",
        f"- Evaluated signals: **{coverage.get('evaluated_signals', 0)}** ({_format_pct(coverage.get('evaluated_signal_pct'))})",
        f"- Mature outcomes: **{coverage.get('outcomes', 0)}**",
        f"- Research gate: **{gate.get('status', 'insufficient_data')}**",
        "- Production effect: **none (shadow/research only)**",
        "",
        "## Research Gate",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for name in (
        "sample_size",
        "buy_directional_hit_rate",
        "profit_factor",
        "drawdown_proxy",
        "opportunity_ic",
    ):
        value = checks.get(name)
        rendered = "N/A" if value is None else ("PASS" if value else "HOLD")
        lines.append(f"| {name} | {rendered} |")

    lines.extend(
        [
            "",
            "## Horizon Metrics",
            "",
            "| Horizon | N | BUY N | BUY Hit | Avoid N | Avoid Hit | Avg BUY | Profit Factor | IC | Evidence |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in summary.get("horizons") or []:
        profit_factor = "∞" if item.get("profit_factor_unbounded") else _format_number(item.get("profit_factor"))
        lines.append(
            "| {horizon}D | {samples} | {buy_n} | {buy_hit} | {avoid_n} | {avoid_hit} | {avg} | {pf} | {ic} | {evidence} |".format(
                horizon=item.get("horizon_days"),
                samples=item.get("samples", 0),
                buy_n=item.get("buy_samples", 0),
                buy_hit=_format_pct(item.get("buy_directional_hit_rate_pct")),
                avoid_n=item.get("avoidance_samples", 0),
                avoid_hit=_format_pct(item.get("avoidance_hit_rate_pct")),
                avg=_format_pct(item.get("avg_strategy_return_pct")),
                pf=profit_factor,
                ic=_format_number(item.get("opportunity_ic_spearman"), 4),
                evidence=_format_pct(item.get("avg_evidence_coverage_pct")),
            )
        )
    if not summary.get("horizons"):
        lines.append("| - | 0 | 0 | N/A | 0 | N/A | N/A | N/A | N/A | N/A |")

    lines.extend(["", "## Methodology", ""])
    for note in summary.get("methodology_notes") or []:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def write_validation_report(
    store: AlphaShadowStore,
    report_dir: str | Path,
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    primary_horizon: int = DEFAULT_PRIMARY_HORIZON,
    stem: str = "validation_latest",
    title: str = "V6 Alpha Validation",
) -> Dict[str, Any]:
    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = build_validation_summary(
        store,
        min_samples=min_samples,
        primary_horizon=primary_horizon,
    )
    (output / f"{stem}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output / f"{stem}.md").write_text(
        render_validation_markdown(summary, title=title),
        encoding="utf-8",
    )
    return summary
