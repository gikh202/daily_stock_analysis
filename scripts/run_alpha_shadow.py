from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.alpha_engine import AlphaDecisionEngine
from src.alpha_engine.features import AlphaFeatureAdapter
from src.alpha_engine.shadow_store import (
    ALPHA_SCHEMA_VERSION,
    AlphaShadowStore,
    mature_pending_outcomes,
    read_analysis_records,
)


logger = logging.getLogger("alpha_shadow")
ENGINE_VERSION = "v5.0-shadow.1"


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_report(
    report_dir: Path,
    *,
    store: AlphaShadowStore,
    run_stats: Dict[str, Any],
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    scorecard = store.scorecard(min_samples=5)
    counts = store.counts()
    payload = {
        "engine_version": ENGINE_VERSION,
        "schema_version": ALPHA_SCHEMA_VERSION,
        "feature_adapter_version": AlphaFeatureAdapter.version,
        "counts": counts,
        "run": run_stats,
        "scorecard": scorecard,
    }
    (report_dir / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    lines = [
        "# V5 Alpha Shadow Report",
        "",
        f"- Engine: `{ENGINE_VERSION}`",
        f"- Feature adapter: `{AlphaFeatureAdapter.version}`",
        f"- Signals persisted: **{counts['signals']}**",
        f"- Mature outcomes: **{counts['outcomes']}**",
        f"- New signals this run: **{run_stats.get('new_signals', 0)}**",
        f"- New outcomes this run: **{run_stats.get('new_outcomes', 0)}**",
        "",
        "## Scorecard",
        "",
        "> Shadow results are descriptive only. No weights or production actions are changed automatically.",
        "",
        "| Decision | Regime | Horizon | N | Mature | Avg Return | Hit Rate | Avg MFE | Avg MAE |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for bucket in scorecard.get("buckets", []):
        def pct(value: Any) -> str:
            return "N/A" if value is None else f"{float(value):.2f}%"

        lines.append(
            "| {decision} | {regime} | {horizon}D | {samples} | {mature} | {ret} | {hit} | {mfe} | {mae} |".format(
                decision=bucket.get("decision") or "unknown",
                regime=bucket.get("market_regime") or "unknown",
                horizon=bucket.get("horizon_days"),
                samples=bucket.get("samples"),
                mature="yes" if bucket.get("mature") else "no",
                ret=pct(bucket.get("avg_return_pct")),
                hit=pct(bucket.get("directional_hit_rate_pct")),
                mfe=pct(bucket.get("avg_mfe_pct")),
                mae=pct(bucket.get("avg_mae_pct")),
            )
        )
    if not scorecard.get("buckets"):
        lines.append("| - | - | - | 0 | no | N/A | N/A | N/A | N/A |")

    (report_dir / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    *,
    stock_db_path: str,
    alpha_db_path: str,
    report_dir: str,
    limit: int,
) -> Dict[str, Any]:
    store = AlphaShadowStore(alpha_db_path)
    engine = AlphaDecisionEngine()
    records = read_analysis_records(stock_db_path, limit=limit)

    new_signals = 0
    skipped_existing = 0
    skipped_unusable = 0

    for record in records:
        history_id = int(record["id"])
        if store.has_analysis_history_id(history_id):
            skipped_existing += 1
            continue

        adapted = AlphaFeatureAdapter.from_snapshot(record.get("context_snapshot"))
        decision = engine.evaluate(
            str(record.get("code") or ""),
            adapted.features,
            current_price=adapted.current_price,
            support=adapted.support,
            resistance=adapted.resistance,
            atr=adapted.atr,
        )

        # A baseline price is required for honest outcome maturation.  Persisting
        # a signal without one would create a permanently unevaluable record.
        if adapted.current_price is None or adapted.current_price <= 0:
            skipped_unusable += 1
            logger.warning(
                "[AlphaShadow] history_id=%s code=%s skipped: no deterministic baseline price",
                history_id,
                record.get("code"),
            )
            continue

        if store.save_signal(
            analysis_history_id=history_id,
            query_id=record.get("query_id"),
            code=str(record.get("code") or ""),
            analysis_created_at=str(record.get("created_at") or ""),
            engine_version=ENGINE_VERSION,
            feature_version=AlphaFeatureAdapter.version,
            decision=decision,
            baseline_price=adapted.current_price,
            market_regime=adapted.market_regime,
            adapter_diagnostics=adapted.diagnostics,
        ):
            new_signals += 1
            logger.info(
                "[AlphaShadow] history_id=%s code=%s decision=%s quality=%s opportunity=%s risk=%s confidence=%.2f",
                history_id,
                record.get("code"),
                decision.decision,
                decision.quality_score,
                decision.opportunity_score,
                decision.risk_score,
                decision.confidence,
            )

    maturation = mature_pending_outcomes(store, stock_db_path)
    run_stats = {
        "analysis_records_seen": len(records),
        "new_signals": new_signals,
        "skipped_existing": skipped_existing,
        "skipped_unusable": skipped_unusable,
        "new_outcomes": maturation["evaluated"],
        "not_yet_mature": maturation["skipped_not_mature"],
        "quick_check": store.quick_check(),
    }
    if run_stats["quick_check"].strip().lower() != "ok":
        raise RuntimeError(f"alpha shadow database quick_check failed: {run_stats['quick_check']}")

    _write_report(Path(report_dir), store=store, run_stats=run_stats)
    return run_stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V5 Alpha Engine in shadow mode")
    parser.add_argument(
        "--stock-db",
        default=os.getenv("STOCK_DB_PATH", "data/stock_analysis.db"),
    )
    parser.add_argument(
        "--alpha-db",
        default=os.getenv("ALPHA_SHADOW_DB_PATH", "alpha_data/alpha_shadow.db"),
    )
    parser.add_argument(
        "--report-dir",
        default=os.getenv("ALPHA_REPORT_DIR", "alpha_reports"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("ALPHA_SHADOW_SCAN_LIMIT", "5000")),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    stats = run(
        stock_db_path=args.stock_db,
        alpha_db_path=args.alpha_db,
        report_dir=args.report_dir,
        limit=max(1, args.limit),
    )
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
