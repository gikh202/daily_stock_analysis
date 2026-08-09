from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.alpha_engine.shadow_store import read_analysis_records
from src.v6_daily.accuracy_report import build_accuracy_unified_report
from src.v6_daily.engine import V6DailyEngine
from src.v6_daily.free_sources import fetch_free_context
from src.v6_daily.report import write_daily_report
from src.v6_daily.store import V6DailyStore, mature_outcomes
from src.v6_daily.unified_report import count_v4_structured_records


logger = logging.getLogger("v6_daily")
MAX_CURRENT_EXTERNAL_CONTEXT_AGE_DAYS = 4


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def _is_current_external_context_safe(effective_trade_date: str | None) -> bool:
    """Current SEC/FRED snapshots may only score a recent effective trade date.

    Historical records are deliberately excluded because the public-source
    fetch performed by this daily runner is a *current* snapshot, not a
    point-in-time historical snapshot. This prevents current filings/rates from
    leaking into backfilled signals whose future outcomes are already known.
    """
    text = str(effective_trade_date or "").strip()[:10]
    try:
        trade_date = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return False
    age_days = (date.today() - trade_date).days
    return 0 <= age_days <= MAX_CURRENT_EXTERNAL_CONTEXT_AGE_DAYS


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
            dedup_key=f"v6-unified-daily-{datetime.now().strftime('%Y%m%d')}",
        )
        return {"attempted": True, "success": bool(success), "status": "sent" if success else "failed"}
    except Exception as exc:
        logger.exception("V6 unified notification failed")
        return {
            "attempted": True,
            "success": False,
            "status": "exception",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _finalize_report(
    *,
    report_dir: str,
    report_date: str,
    v4_report_path: str | None,
    v6_payload: Dict[str, Any],
    analysis_records: list[Dict[str, Any]],
) -> Dict[str, Any]:
    output = Path(report_dir)
    latest_path = output / "v6_daily_latest.md"
    dated_path = output / f"v6_daily_{report_date}.md"
    v6_markdown = latest_path.read_text(encoding="utf-8")

    v4_path = Path(v4_report_path) if v4_report_path else None
    v4_markdown = None
    if v4_path is not None and v4_path.is_file():
        v4_markdown = v4_path.read_text(encoding="utf-8")
        logger.info("[V6] 已加载同次运行的 V4 报告 Artifact: %s", v4_path)
    elif v4_report_path:
        logger.warning("[V6] 指定的 V4 报告不存在，将仅使用数据库中的 V4 结构化记录: %s", v4_report_path)

    structured_count = count_v4_structured_records(analysis_records)
    if structured_count:
        logger.info("[V6] 融合报告使用 %s 个标的的 V4 结构化投研记录", structured_count)
    else:
        logger.warning("[V6] 未发现 V4 结构化投研记录；最终报告不会回退为 V4/V6 原文拼接")

    unified = build_accuracy_unified_report(
        v6_markdown,
        v4_markdown,
        v6_payload=v6_payload,
        v4_records=analysis_records,
        report_date=report_date,
    )
    latest_path.write_text(unified, encoding="utf-8")
    dated_path.write_text(unified, encoding="utf-8")
    return {
        "language": "zh",
        "fusion_mode": "structured_v4_v6",
        "accuracy_layer": "v6.1",
        "v4_merged": structured_count > 0,
        "v4_structured_records": structured_count,
        "v4_report": str(v4_path) if v4_markdown and v4_path is not None else None,
        "output": str(latest_path),
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
    v4_report_path: str | None = None,
) -> Dict[str, Any]:
    store = V6DailyStore(v6_db_path)
    engine = V6DailyEngine()
    records = read_analysis_records(stock_db_path, limit=max(1, int(limit)))

    source_codes = list(
        dict.fromkeys(
            str(record.get("code") or "").strip().upper()
            for record in records
            if str(record.get("code") or "").strip()
        )
    )
    public_context = fetch_free_context(source_codes)

    # First build every usable signal WITHOUT the newly fetched SEC/FRED
    # snapshot. This establishes the effective date independently of current
    # external evidence and lets us identify the true newest trade-date bucket.
    provisional: list[tuple[Dict[str, Any], Any]] = []
    skipped_unusable = 0
    for record in records:
        history_id = int(record.get("id") or 0)
        if history_id <= 0:
            skipped_unusable += 1
            continue
        signal = engine.from_analysis_record(
            record,
            primary_model=primary_model,
            external_context=None,
        )
        if signal is None:
            skipped_unusable += 1
            continue
        provisional.append((record, signal))

    effective_dates = [
        str(signal.effective_trade_date or "")
        for _, signal in provisional
        if str(signal.effective_trade_date or "")
    ]
    latest_effective_date = max(effective_dates) if effective_dates else None
    external_numeric_date = (
        latest_effective_date
        if latest_effective_date
        and _is_current_external_context_safe(latest_effective_date)
        else None
    )
    if latest_effective_date and external_numeric_date is None:
        logger.warning(
            "[V6.1] 最新有效交易日 %s 距当前过久；SEC/FRED 当前快照仅展示，不参与历史数值回填",
            latest_effective_date,
        )

    new_signals = 0
    skipped_existing = 0
    skipped_same_trade_date = 0
    for record, provisional_signal in provisional:
        history_id = int(record.get("id") or 0)
        if store.has_analysis_history_id(history_id):
            skipped_existing += 1
            continue

        signal = provisional_signal
        if (
            external_numeric_date is not None
            and signal.effective_trade_date == external_numeric_date
        ):
            enriched = engine.from_analysis_record(
                record,
                primary_model=primary_model,
                external_context=public_context,
            )
            if enriched is not None:
                signal = enriched

        if store.has_signal_key(signal.code, signal.effective_trade_date, engine.version):
            skipped_same_trade_date += 1
            continue
        if store.save_signal(signal, engine_version=engine.version):
            new_signals += 1
            horizon_text = ", ".join(
                f"{name}={item.get('direction')}/{item.get('score')}"
                for name, item in signal.horizon_forecasts.items()
            )
            logger.info(
                "[V6.1] history_id=%s code=%s type=%s decision=%s horizons=[%s] opportunity=%s risk=%s evidence=%.2f llm=%s external_numeric=%s",
                signal.analysis_history_id,
                signal.code,
                signal.instrument_type,
                signal.decision,
                horizon_text,
                signal.opportunity_score,
                signal.risk_score,
                signal.evidence_coverage,
                signal.llm_health,
                signal.effective_trade_date == external_numeric_date,
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
    run_stats: Dict[str, Any] = {
        "analysis_records_seen": len(records),
        "new_signals": new_signals,
        "skipped_existing": skipped_existing,
        "skipped_same_trade_date": skipped_same_trade_date,
        "skipped_unusable": skipped_unusable,
        "new_outcomes": maturation["evaluated"],
        "not_yet_mature": maturation["not_yet_mature"],
        "quick_check": quick,
        "free_source_enrichment": (public_context.get("status") or {}).get("enabled", False),
        "external_numeric_trade_date": external_numeric_date,
        "external_backfill_policy": "current snapshot scores newest recent trade date only",
    }
    report_date = datetime.now().strftime("%Y-%m-%d")
    payload = write_daily_report(
        store,
        report_dir,
        run_stats=run_stats,
        min_samples=max(3, int(min_samples)),
        report_date=report_date,
        public_context=public_context,
    )

    unified_report = _finalize_report(
        report_dir=report_dir,
        report_date=report_date,
        v4_report_path=v4_report_path,
        v6_payload=payload,
        analysis_records=records,
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
        "unified_report": unified_report,
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
    parser = argparse.ArgumentParser(description="Run the V6.1 deterministic multi-horizon US-stock daily intelligence layer")
    parser.add_argument("--stock-db", default=os.getenv("STOCK_DB_PATH", "data/stock_analysis.db"))
    parser.add_argument("--v6-db", default=os.getenv("V6_DAILY_DB_PATH", "v6_data/v6_daily.db"))
    parser.add_argument("--report-dir", default=os.getenv("V6_DAILY_REPORT_DIR", "v6_reports"))
    parser.add_argument("--v4-report", default=os.getenv("V6_UPSTREAM_V4_REPORT"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("V6_DAILY_SCAN_LIMIT", "5000")))
    parser.add_argument("--min-samples", type=int, default=int(os.getenv("V6_DAILY_MIN_SAMPLES", "50")))
    parser.add_argument("--primary-model", default=os.getenv("LITELLM_MODEL") or os.getenv("V6_PRIMARY_LLM_MODEL"))
    parser.add_argument("--notify", action="store_true", default=_truthy(os.getenv("V6_DAILY_NOTIFY", "false")))
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
        v4_report_path=args.v4_report,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
