# -*- coding: utf-8 -*-
"""Characterization tests for the extracted pipeline market-data stage."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.stages.market_data import MarketDataPersistenceStage


def _stage(*, fetcher_manager=None, db=None, resolver=None):
    return MarketDataPersistenceStage(
        fetcher_manager=fetcher_manager or MagicMock(),
        db=db or MagicMock(),
        resume_target_resolver=resolver or MagicMock(return_value="2026-08-21"),
    )


def test_market_data_stage_skips_cached_target_date_without_fetching() -> None:
    fetcher = MagicMock()
    fetcher.get_stock_name.return_value = "Alphabet"
    db = MagicMock()
    db.has_today_data.return_value = True
    resolver = MagicMock(return_value="2026-08-21")
    current_time = datetime(2026, 8, 22, 1, 0, tzinfo=timezone.utc)

    result = _stage(
        fetcher_manager=fetcher,
        db=db,
        resolver=resolver,
    ).run("GOOGL", current_time=current_time)

    assert result == (True, None)
    fetcher.get_stock_name.assert_called_once_with("GOOGL", allow_realtime=False)
    resolver.assert_called_once_with("GOOGL", current_time=current_time)
    db.has_today_data.assert_called_once_with("GOOGL", "2026-08-21")
    fetcher.get_daily_data.assert_not_called()
    db.save_daily_data.assert_not_called()


def test_market_data_stage_force_refresh_fetches_and_persists() -> None:
    fetcher = MagicMock()
    fetcher.get_stock_name.return_value = "Alphabet"
    frame = SimpleNamespace(empty=False)
    fetcher.get_daily_data.return_value = (frame, "futu")
    db = MagicMock()
    db.has_today_data.return_value = True
    db.save_daily_data.return_value = 17
    resolver = MagicMock(return_value="2026-08-21")

    result = _stage(
        fetcher_manager=fetcher,
        db=db,
        resolver=resolver,
    ).run("GOOGL", force_refresh=True)

    assert result == (True, None)
    db.has_today_data.assert_not_called()
    fetcher.get_daily_data.assert_called_once_with("GOOGL", days=60)
    db.save_daily_data.assert_called_once_with(frame, "GOOGL", "futu")


def test_market_data_stage_returns_empty_data_error_without_persisting() -> None:
    fetcher = MagicMock()
    fetcher.get_stock_name.return_value = "Alphabet"
    fetcher.get_daily_data.return_value = (SimpleNamespace(empty=True), "futu")
    db = MagicMock()
    db.has_today_data.return_value = False

    result = _stage(fetcher_manager=fetcher, db=db).run("GOOGL")

    assert result == (False, "获取数据为空")
    db.save_daily_data.assert_not_called()


def test_market_data_stage_preserves_fail_safe_error_contract() -> None:
    fetcher = MagicMock()
    fetcher.get_stock_name.side_effect = RuntimeError("provider unavailable")

    result = _stage(fetcher_manager=fetcher).run("GOOGL")

    assert result == (False, "获取/保存数据失败: provider unavailable")
