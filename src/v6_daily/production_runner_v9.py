from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.alpha_engine.shadow_store import read_analysis_records

from . import production_runner as base_runner
from .engine import V6DailyEngine
from .free_sources_v9 import fetch_free_context_v9
from .portfolio_context import (
    load_portfolio_risk_context,
    sector_map_from_analysis_records,
)


V9_PRODUCTION_WRAPPER_VERSION = "v9-production-risk-wrapper.1"
_BASE_RUN = base_runner.run


def _persist_v9_metadata(
    *,
    report_dir: str,
    result: Dict[str, Any],
    portfolio_context: Dict[str, Any],
) -> Dict[str, Any]:
    metadata = {
        "v9_production_wrapper": V9_PRODUCTION_WRAPPER_VERSION,
        "portfolio_risk_context": portfolio_context,
        "macro_context_version": "macro-risk-v9-real-yield-liquidity.1",
    }
    result.update(metadata)
    output = Path(report_dir)
    for name in ("v6_run.json", "v6_daily_latest.json"):
        path = output / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(metadata)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return result


def run(**kwargs: Any) -> Dict[str, Any]:
    """Run the existing Stage 11 pipeline with monotonic portfolio-risk injection.

    Stage 11 remains the canonical normalized-only implementation. This wrapper
    only supplies two additional deterministic inputs: current portfolio capacity
    and the V9 free-source macro extension. Both are fail-safe and can only reduce
    risk; unavailable evidence is recorded rather than fabricated.
    """
    stock_db_path = str(kwargs.get("stock_db_path") or "data/stock_analysis.db")
    report_dir = str(kwargs.get("report_dir") or "v6_reports")
    limit = max(1, int(kwargs.get("limit") or 5000))

    records = read_analysis_records(stock_db_path, limit=limit)
    sector_map = sector_map_from_analysis_records(records)
    portfolio_context = load_portfolio_risk_context(
        stock_db_path,
        sector_by_symbol=sector_map,
    ).to_dict()

    original_from_analysis_record = V6DailyEngine.from_analysis_record
    original_fetch_free_context = base_runner.fetch_free_context

    def portfolio_aware_from_analysis_record(
        self: V6DailyEngine,
        record: Any,
        **call_kwargs: Any,
    ) -> Any:
        call_kwargs.setdefault("portfolio_context", portfolio_context)
        return original_from_analysis_record(self, record, **call_kwargs)

    V6DailyEngine.from_analysis_record = portfolio_aware_from_analysis_record  # type: ignore[method-assign]
    base_runner.fetch_free_context = fetch_free_context_v9
    try:
        result = _BASE_RUN(**kwargs)
    finally:
        V6DailyEngine.from_analysis_record = original_from_analysis_record  # type: ignore[method-assign]
        base_runner.fetch_free_context = original_fetch_free_context

    return _persist_v9_metadata(
        report_dir=report_dir,
        result=result,
        portfolio_context=portfolio_context,
    )
