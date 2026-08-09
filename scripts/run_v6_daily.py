from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.alpha_engine.shadow_store import read_analysis_records
from src.v6_daily.engine import V6DailyEngine
from src.v6_daily.free_sources import fetch_free_context
from src.v6_daily.report import write_daily_report
from src.v6_daily.store import V6DailyStore, mature_outcomes


logger = logging.getLogger("v6_daily")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def _notify(report_path: Path, codes: list[str]) -> Dict[str, Any]:
    try:
        from src.notification import NotificationService

        content = report_path.read_text(encoding="utf-8")
        service = NotificationService()
        if not service.is_available():
            return {"attempted": False, "success": False, "status": "no_channel"}
        success = service.send(
            content,
            email_stock_codes=codes,
            email_send_to_all=True,
            route_type="report",
            severity="info",
            dedup_key=f"v6-daily-{datetime.now().strftime('%Y%m%d')}",
        )
        return {
            "attempted": True,
            "success": bool(success),
            "status": "sent" if success else "failed",
        }
    except Exception as exc:
        logger.exception("V6 notification failed")
        return {
            "attempted": True,
            "success": False,
            "status": "exception",
            "error": f"{type(exc).__name__}: {exc}",
        }


def run(
    *,
    stock_db_path: str,
    v6_db_path: str,
    report_dir: str,
    limit: int = 5000,
    min_samples: int = 50,
    primary_model: str | None = None,
    notify: bool = False,
) -> Dict[str, Any]:
    store = V6DailyStore(v6_db_path)
    engine = V6DailyEngine()
    records = read_analysis_records(stock_db_path, limit=max(1, int(limit)))

    new_signals = 0
    skipped_existing = 0
    skipped_unusable = 0

    for record in records:
        history_id = int(record.get("id") or 0)
        if history_id <= 0:
            skipped_unusable += 1
            continue
        if store.has_analysis_history_id(history_id):
            skipped_existing += 1
            continue
        signal = engine.from_analysis_record(record, primary_model=primary_model)
        if signal is None:
            skipped_unusable += 1
            continue
        if store.save_signal(signal, engine_version=engine.version):
            new_signals += 1
            logger.info(
                "[V6] history_id=%s code=%s direction=%s decision=%s forecast=%s opportunity=%s risk=%s evidence=%.2f llm=%s",
                signal.analysis_history_id,
                signal.code,
                signal.direction,
                signal.decision,
                signal.forecast_score,
                signal.opportunity_score,
                signal.risk_score,
                signal.evidence_coverage,
                signal.llm_health,
            )

    maturation = mature_outcomes(store, stock_db_path)
    quick = store.quick_check()
    if quick.strip().lower() != "ok":
        raise RuntimeError(f"V6 database quick_check failed: {quick}")

    board_before_report = store.latest_board()
    codes = [
        str(item.get("code") or "").strip().upper()
        for item in board_before_report
        if str(item.get("code") or "").strip()
    ]
    public_context = fetch_free_context(codes)

    run_stats: Dict[str, Any] = {
        "analysis_records_seen": len(records),
        "new_signals": new_signals,
        "skipped_existing": skipped_existing,
        "skipped_unusable": skipped_unusable,
        "new_outcomes": maturation["evaluated"],
        "not_yet_mature": maturation["not_yet_mature"],
        "quick_check": quick,
        "free_source_enrichment": (public_context.get("status") or {}).get("enabled", False),
    }
    payload = write_daily_report(
        store,
        report_dir,
        run_stats=run_stats,
        min_samples=max(3, int(min_samples)),
        report_date=datetime.now().strftime("%Y-%m-%d"),
        public_context=public_context,
    )

    report_path = Path(report_dir) / "v6_daily_latest.md"
    notification = _notify(report_path, codes) if notify else {
        "attempted": False,
        "success": False,
        "status": "disabled",
    }

    result = {
        "version": engine.version,
        "database": str(store.path),
        "report": str(report_path),
        "run": run_stats,
        "scoreboard_status": (payload.get("scoreboard") or {}).get("status"),
        "free_sources": public_context.get("status") or {},
        "notification": notification,
    }
    (Path(report_dir) / "v6_run.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the V6 deterministic AI US-stock daily intelligence layer")
    parser.add_argument("--stock-db", default=os.getenv("STOCK_DB_PATH", "data/stock_analysis.db"))
    parser.add_argument("--v6-db", default=os.getenv("V6_DAILY_DB_PATH", "v6_data/v6_daily.db"))
    parser.add_argument("--report-dir", default=os.getenv("V6_DAILY_REPORT_DIR", "v6_reports"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("V6_DAILY_SCAN_LIMIT", "5000")))
    parser.add_argument("--min-samples", type=int, default=int(os.getenv("V6_DAILY_MIN_SAMPLES", "50")))
    parser.add_argument("--primary-model", default=os.getenv("LITELLM_MODEL") or os.getenv("V6_PRIMARY_LLM_MODEL"))
    parser.add_argument(
        "--notify",
        action="store_true",
        default=_truthy(os.getenv("V6_DAILY_NOTIFY", "false")),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    result = run(
        stock_db_path=args.stock_db,
        v6_db_path=args.v6_db,
        report_dir=args.report_dir,
        limit=args.limit,
        min_samples=args.min_samples,
        primary_model=args.primary_model,
        notify=args.notify,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
