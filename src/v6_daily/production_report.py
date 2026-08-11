from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .engine import V6_ENGINE_VERSION


def _number(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def _pct(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return "N/A"


def _coverage(value: Any) -> str:
    try:
        return f"{100.0 * float(value):.0f}%"
    except (TypeError, ValueError):
        return "N/A"


def _dominant(values: Iterable[Any], default: str = "unknown") -> str:
    clean = [str(value).strip() for value in values if str(value or "").strip()]
    if not clean:
        return default
    return Counter(clean).most_common(1)[0][0]


def _avg(items: Iterable[Any]) -> Optional[float]:
    values = []
    for item in items:
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            continue
    return None if not values else statistics.fmean(values)


def _feature_line(features: Dict[str, Any]) -> str:
    labels = (
        ("trend", "Trend"),
        ("momentum", "Momentum"),
        ("relative_strength", "RS"),
        ("volume_confirmation", "Volume"),
        ("fundamental_quality", "Fundamental"),
        ("market_regime", "Regime"),
    )
    return " | ".join(
        f"{label} {_number(features.get(key), 0)}" for key, label in labels
    )


def build_daily_payload(
    store: Any,
    *,
    run_stats: Dict[str, Any],
    min_samples: int = 50,
    public_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    board = store.latest_board()
    deltas = store.daily_deltas()
    scoreboard = store.scoreboard(min_samples=min_samples)
    counts = store.counts()
    return {
        "version": V6_ENGINE_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
        "run": run_stats,
        "market_pulse": {
            "regime": _dominant(item.get("market_regime") for item in board),
            "breadth": _dominant(item.get("market_breadth") for item in board),
            "average_opportunity": _avg(item.get("opportunity_score") for item in board),
            "average_risk": _avg(item.get("risk_score") for item in board),
            "average_evidence_coverage": _avg(item.get("evidence_coverage") for item in board),
        },
        "board": board,
        "deltas": deltas,
        "scoreboard": scoreboard,
        "public_context": public_context or {},
    }


def _append_public_context(lines: List[str], context: Dict[str, Any]) -> None:
    lines.extend(["## 7. Free Public Data Context", ""])
    lines.append(
        "> SEC/FRED are evidence-only in V6.0. They do not change numeric scores until a historical mapping is validated."
    )
    lines.append("")
    status = context.get("status") or {}
    if not status or not status.get("enabled"):
        lines.append("- Enrichment: **disabled** (set `V6_FREE_SOURCE_ENRICHMENT=true` to enable configured free sources).")
        lines.append("")
        return

    sec_status = status.get("sec") or {}
    fred_status = status.get("fred") or {}
    lines.append(
        f"- SEC EDGAR: **{'configured' if sec_status.get('configured') else 'not configured'}**"
    )
    lines.append(
        f"- FRED: **{'configured' if fred_status.get('configured') else 'not configured'}**"
    )

    fred = context.get("fred") or {}
    if fred:
        lines.extend(["", "### Macro snapshot", ""])
        for series_id, item in fred.items():
            if not isinstance(item, dict):
                continue
            latest = item.get("latest") or {}
            if isinstance(latest, dict) and latest.get("value"):
                lines.append(
                    f"- {item.get('label') or series_id}: **{latest.get('value')}** ({latest.get('date') or '-'})"
                )
            elif item.get("error"):
                lines.append(f"- {item.get('label') or series_id}: unavailable")

    sec = context.get("sec") or {}
    if sec:
        lines.extend(["", "### Recent SEC filings", ""])
        for code, item in sorted(sec.items()):
            if not isinstance(item, dict):
                continue
            filings = item.get("recent_filings") or []
            if not filings:
                continue
            compact = []
            for filing in filings[:4]:
                if not isinstance(filing, dict):
                    continue
                compact.append(
                    f"{filing.get('form') or '-'} {filing.get('filing_date') or '-'}"
                )
            if compact:
                lines.append(
                    f"- **{code}** ({item.get('company') or 'company'}): " + "; ".join(compact)
                )
    lines.append("")


def render_daily_markdown(payload: Dict[str, Any], *, report_date: Optional[str] = None) -> str:
    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    pulse = payload.get("market_pulse") or {}
    board: List[Dict[str, Any]] = list(payload.get("board") or [])
    deltas: List[Dict[str, Any]] = list(payload.get("deltas") or [])
    scoreboard = payload.get("scoreboard") or {}
    run = payload.get("run") or {}

    lines = [
        f"# V6 AI 美股日报 · {report_date}",
        "",
        "> 数字由确定性规则产生；LLM 仅用于公开新闻/事件的文字解释，不直接决定 Forecast、Opportunity、Risk 或仓位。",
        "",
        "## 1. Market Pulse",
        "",
        f"- Regime: **{pulse.get('regime') or 'unknown'}**",
        f"- Breadth: **{pulse.get('breadth') or 'unknown'}**",
        f"- Average Opportunity: **{_number(pulse.get('average_opportunity'))}**",
        f"- Average Risk: **{_number(pulse.get('average_risk'))}**",
        f"- Evidence Coverage: **{_coverage(pulse.get('average_evidence_coverage'))}**",
        "",
        "## 2. Significant Changes",
        "",
    ]

    meaningful = []
    for item in deltas:
        decision_changed = item.get("decision_before") != item.get("decision_after")
        direction_changed = item.get("direction_before") != item.get("direction_after")
        magnitude = max(
            abs(float(item.get("opportunity_delta") or 0.0)),
            abs(float(item.get("risk_delta") or 0.0)),
            abs(float(item.get("forecast_delta") or 0.0)),
        )
        if decision_changed or direction_changed or magnitude >= 5.0:
            meaningful.append(item)
    meaningful.sort(
        key=lambda item: max(
            abs(float(item.get("opportunity_delta") or 0.0)),
            abs(float(item.get("risk_delta") or 0.0)),
            abs(float(item.get("forecast_delta") or 0.0)),
        ),
        reverse=True,
    )
    if meaningful:
        for item in meaningful[:10]:
            lines.append(
                "- **{code}**: {before} → {after} | Direction {d_before} → {d_after} | "
                "Opportunity {opp:+.1f} | Risk {risk:+.1f} | Forecast {forecast:+.1f}".format(
                    code=item.get("code"),
                    before=item.get("decision_before") or "-",
                    after=item.get("decision_after") or "-",
                    d_before=item.get("direction_before") or "-",
                    d_after=item.get("direction_after") or "-",
                    opp=float(item.get("opportunity_delta") or 0.0),
                    risk=float(item.get("risk_delta") or 0.0),
                    forecast=float(item.get("forecast_delta") or 0.0),
                )
            )
    else:
        lines.append("- 首次 V6 运行或本轮没有达到显著变化阈值（5 分）。")

    lines.extend(
        [
            "",
            "## 3. Opportunity Ranking",
            "",
            "| Rank | Symbol | Decision | Direction | Forecast | Opportunity | Quality | Risk | Evidence | LLM |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for rank, item in enumerate(board, 1):
        lines.append(
            "| {rank} | {code} | {decision} | {direction} | {forecast} | {opportunity} | {quality} | {risk} | {evidence} | {llm} |".format(
                rank=rank,
                code=item.get("code") or "-",
                decision=item.get("decision") or "-",
                direction=item.get("direction") or "-",
                forecast=_number(item.get("forecast_score")),
                opportunity=_number(item.get("opportunity_score")),
                quality=_number(item.get("quality_score")),
                risk=_number(item.get("risk_score")),
                evidence=_coverage(item.get("evidence_coverage")),
                llm=item.get("llm_health") or "unknown",
            )
        )
    if not board:
        lines.append("| - | - | WAIT | neutral | N/A | N/A | N/A | N/A | 0% | missing |")

    lines.extend(["", "## 4. Setup Cards", ""])
    for item in board[:12]:
        plan = item.get("trade_plan") or {}
        features = item.get("features") or {}
        lines.extend(
            [
                f"### {item.get('code')} · {item.get('decision')} · {item.get('direction')}",
                "",
                f"- Forecast Score: **{_number(item.get('forecast_score'))}**",
                f"- Opportunity / Quality / Risk: **{_number(item.get('opportunity_score'))} / {_number(item.get('quality_score'))} / {_number(item.get('risk_score'))}**",
                f"- Evidence Coverage: **{_coverage(item.get('evidence_coverage'))}**",
                f"- Factors: {_feature_line(features)}",
                f"- Entry: `{plan.get('entry_zone') or 'N/A'}`",
                f"- Stop: `{plan.get('stop_loss') or 'N/A'}`",
                f"- Targets: `{plan.get('targets') or 'N/A'}`",
                f"- R:R: `{plan.get('risk_reward') if plan.get('risk_reward') is not None else 'N/A'}`",
            ]
        )
        catalysts = item.get("catalysts") or []
        risks = item.get("risks") or []
        if catalysts:
            lines.append("- Qualitative catalysts (LLM extracted, not numerically scored):")
            lines.extend(f"  - {text}" for text in catalysts[:3])
        if risks:
            lines.append("- Qualitative risks (LLM extracted, not numerically scored):")
            lines.extend(f"  - {text}" for text in risks[:3])
        limitations = item.get("limitations") or []
        if limitations:
            lines.append("- Data limitations: " + "; ".join(str(value) for value in limitations[:4]))
        lines.append("")

    lines.extend(["## 5. LLM Health", ""])
    health = Counter(str(item.get("llm_health") or "unknown") for item in board)
    if health:
        for status in ("healthy", "fallback", "degraded", "unknown", "missing"):
            if health.get(status):
                lines.append(f"- {status}: **{health[status]}**")
    else:
        lines.append("- No V6 signals available.")
    lines.append("")

    lines.extend(
        [
            "## 6. Prediction Scoreboard",
            "",
            f"- Status: **{scoreboard.get('status', 'insufficient_data')}**",
            f"- Minimum sample floor: **{scoreboard.get('minimum_samples', 50)}** per horizon",
            "",
            "| Horizon | N | Direction Hit | BUY N | BUY Hit | Avoid N | Avoid Hit | Forecast IC | Opportunity IC |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in scoreboard.get("horizons") or []:
        lines.append(
            "| {h}D | {n} | {hit} | {buy_n} | {buy_hit} | {avoid_n} | {avoid_hit} | {fic} | {oic} |".format(
                h=item.get("horizon_days"),
                n=item.get("samples", 0),
                hit=_pct(item.get("directional_hit_rate_pct")),
                buy_n=item.get("buy_setup_samples", 0),
                buy_hit=_pct(item.get("buy_setup_hit_rate_pct")),
                avoid_n=item.get("avoidance_samples", 0),
                avoid_hit=_pct(item.get("avoidance_hit_rate_pct")),
                fic=_number(item.get("forecast_score_ic_spearman"), 4),
                oic=_number(item.get("opportunity_ic_spearman"), 4),
            )
        )
    if not scoreboard.get("horizons"):
        lines.append("| - | 0 | N/A | 0 | N/A | 0 | N/A | N/A | N/A |")
    lines.append("")

    _append_public_context(lines, payload.get("public_context") or {})

    lines.extend(
        [
            "## 8. Run Health",
            "",
            f"- New V6 signals: **{run.get('new_signals', 0)}**",
            f"- Skipped existing: **{run.get('skipped_existing', 0)}**",
            f"- Skipped unusable: **{run.get('skipped_unusable', 0)}**",
            f"- New matured outcomes: **{run.get('new_outcomes', 0)}**",
            f"- Not yet mature: **{run.get('not_yet_mature', 0)}**",
            f"- SQLite quick_check: **{run.get('quick_check', 'unknown')}**",
            "",
            "## Methodology",
            "",
            "- V6 direction uses deterministic Trend, Momentum, Relative Strength and Market Regime features.",
            "- Opportunity/Quality/Risk and trade plan reuse the deterministic Alpha decision layer.",
            "- Missing evidence lowers coverage instead of being replaced with a fake neutral 50.",
            "- AVOID means no position; it is evaluated as avoidance accuracy, never as an implicit short.",
            "- Outcomes mature after 5/10/20 future trading bars, not calendar days.",
            "- LLM prose has zero direct numeric influence on V6 scores.",
            "- SEC/FRED enrichment is evidence-only until its scoring contribution is validated out-of-sample.",
            "",
            f"*Generated by {payload.get('version', V6_ENGINE_VERSION)} at {payload.get('generated_at', '-')}*",
        ]
    )
    return "\n".join(lines) + "\n"


def write_daily_report(
    store: Any,
    report_dir: str | Path,
    *,
    run_stats: Dict[str, Any],
    min_samples: int = 50,
    report_date: Optional[str] = None,
    public_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    date_text = report_date or datetime.now().strftime("%Y-%m-%d")
    payload = build_daily_payload(
        store,
        run_stats=run_stats,
        min_samples=min_samples,
        public_context=public_context,
    )
    markdown = render_daily_markdown(payload, report_date=date_text)
    (output / "v6_daily_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output / "v6_daily_latest.md").write_text(markdown, encoding="utf-8")
    (output / f"v6_daily_{date_text}.md").write_text(markdown, encoding="utf-8")
    return payload
