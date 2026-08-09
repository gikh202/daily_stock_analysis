from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .shadow_store import ALPHA_SCHEMA_VERSION, AlphaShadowStore


VALIDATION_VERSION = "v5.1-validation.1"
DEFAULT_MIN_SAMPLES = 20
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
    """Return 1-based average ranks with deterministic tie handling."""
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
    coefficient = _pearson(_average_ranks(xs), _average_ranks(ys))
    return coefficient, len(clean)


def _signed_strategy_return(decision: str, return_pct: Any) -> Optional[float]:
    value = _finite(return_pct)
    if value is None:
        return None
    normalized = str(decision or "").strip().upper()
    if normalized == "BUY_SETUP":
        return value
    if normalized == "AVOID":
        return -value
    return None


def _profit_factor(returns: Sequence[float]) -> Tuple[Optional[float], bool]:
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    if losses <= 0:
        return (None, gains > 0)
    return gains / losses, False


def _max_drawdown_proxy_pct(returns: Sequence[float]) -> Optional[float]:
    """Sequence drawdown proxy over signal returns, not a portfolio equity curve.

    Outcomes can overlap in time, so this metric must not be presented as a true
    executable portfolio drawdown. It is still useful for spotting unstable
    signal sequences during shadow validation.
    """
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
    """Annualized signal-return Sharpe proxy; not a portfolio Sharpe ratio."""
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
    """Build deterministic, read-only research metrics from matured outcomes.

    The summary deliberately separates *signal research* from execution. It does
    not change Alpha weights, V4 advice, position sizes, or production actions.
    """
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
        directional_hits: List[int] = []
        raw_returns: List[float] = []
        confidence_pairs: List[Tuple[float, int]] = []

        for row in bucket:
            raw = _finite(row["return_pct"])
            if raw is not None:
                raw_returns.append(raw)
            signed = _signed_strategy_return(row["decision"], row["return_pct"])
            if signed is not None:
                strategy_returns.append(signed)
            if row["directional_hit"] is not None:
                hit = int(row["directional_hit"])
                directional_hits.append(hit)
                confidence = _finite(row["confidence"])
                if confidence is not None:
                    confidence_pairs.append((confidence, hit))

        opportunity_ic, opportunity_ic_samples = _spearman(
            (row["opportunity_score"], row["return_pct"]) for row in bucket
        )
        profit_factor, profit_factor_unbounded = _profit_factor(strategy_returns)
        hit_rate = (
            None
            if not directional_hits
            else 100.0 * sum(directional_hits) / len(directional_hits)
        )
        avg_confidence = (
            None
            if not confidence_pairs
            else 100.0 * statistics.fmean(pair[0] for pair in confidence_pairs)
        )
        calibration_gap = (
            None
            if avg_confidence is None or hit_rate is None
            else avg_confidence - hit_rate
        )

        horizons.append(
            {
                "horizon_days": horizon_days,
                "samples": len(bucket),
                "mature": len(bucket) >= minimum,
                "directional_samples": len(directional_hits),
                "directional_hit_rate_pct": _round(hit_rate, 2),
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
                "avg_directional_confidence_pct": _round(avg_confidence, 2),
                "confidence_calibration_gap_pct": _round(calibration_gap, 2),
            }
        )

    selected = None
    for item in horizons:
        if int(item["horizon_days"]) == int(primary_horizon):
            selected = item
            break
    if selected is None and horizons:
        selected = horizons[0]

    checks: Dict[str, Optional[bool]] = {
        "sample_size": None,
        "directional_hit_rate": None,
        "profit_factor": None,
        "drawdown_proxy": None,
        "opportunity_ic": None,
    }
    status = "insufficient_data"
    if selected is not None:
        checks["sample_size"] = selected["samples"] >= minimum
        checks["directional_hit_rate"] = (
            selected["directional_samples"] >= minimum
            and selected["directional_hit_rate_pct"] is not None
            and selected["directional_hit_rate_pct"] >= 52.0
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
                "directional_hit_rate_pct": 52.0,
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
            "BUY_SETUP uses long return; AVOID uses inverse return; WATCH/WAIT are excluded from strategy-return metrics.",
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


def render_validation_markdown(summary: Dict[str, Any], *, title: str = "V5 Alpha Validation") -> str:
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
        "- Production effect: **none (shadow-only)**",
        "",
        "## Research Gate",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for name in ("sample_size", "directional_hit_rate", "profit_factor", "drawdown_proxy", "opportunity_ic"):
        value = checks.get(name)
        rendered = "N/A" if value is None else ("PASS" if value else "HOLD")
        lines.append(f"| {name} | {rendered} |")

    lines.extend(
        [
            "",
            "## Horizon Metrics",
            "",
            "| Horizon | N | Hit Rate | Avg Strategy | Profit Factor | IC | Sharpe Proxy | DD Proxy |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in summary.get("horizons") or []:
        profit_factor = "∞" if item.get("profit_factor_unbounded") else _format_number(item.get("profit_factor"))
        lines.append(
            "| {horizon}D | {samples} | {hit} | {avg} | {pf} | {ic} | {sharpe} | {dd} |".format(
                horizon=item.get("horizon_days"),
                samples=item.get("samples", 0),
                hit=_format_pct(item.get("directional_hit_rate_pct")),
                avg=_format_pct(item.get("avg_strategy_return_pct")),
                pf=profit_factor,
                ic=_format_number(item.get("opportunity_ic_spearman"), 4),
                sharpe=_format_number(item.get("signal_sharpe_proxy"), 3),
                dd=_format_pct(item.get("sequence_max_drawdown_proxy_pct")),
            )
        )
    if not summary.get("horizons"):
        lines.append("| - | 0 | N/A | N/A | N/A | N/A | N/A | N/A |")

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
    title: str = "V5 Alpha Validation",
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
