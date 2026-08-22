# -*- coding: utf-8 -*-
"""Market-data acquisition and persistence stage for the analysis pipeline."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Callable, Optional, Tuple


logger = logging.getLogger(__name__)


ResumeTargetResolver = Callable[..., Any]


class MarketDataPersistenceStage:
    """Fetch one symbol's daily data and persist it without owning pipeline state."""

    def __init__(
        self,
        *,
        fetcher_manager: Any,
        db: Any,
        resume_target_resolver: ResumeTargetResolver,
        stage_logger: Any = logger,
    ) -> None:
        self.fetcher_manager = fetcher_manager
        self.db = db
        self.resume_target_resolver = resume_target_resolver
        self.logger = stage_logger

    def run(
        self,
        code: str,
        *,
        force_refresh: bool = False,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Preserve the historical fetch/cache/persist behavior for one symbol."""

        stock_name = code
        try:
            stock_name = self.fetcher_manager.get_stock_name(
                code,
                allow_realtime=False,
            )

            target_date = self.resume_target_resolver(
                code,
                current_time=current_time,
            )

            if not force_refresh and self.db.has_today_data(code, target_date):
                self.logger.info(
                    f"{stock_name}({code}) {target_date} 数据已存在，跳过获取（断点续传）"
                )
                return True, None

            self.logger.info(f"{stock_name}({code}) 开始从数据源获取数据...")
            df, source_name = self.fetcher_manager.get_daily_data(code, days=60)

            if df is None or df.empty:
                return False, "获取数据为空"

            saved_count = self.db.save_daily_data(df, code, source_name)
            self.logger.info(
                f"{stock_name}({code}) 数据保存成功（来源: {source_name}，新增 {saved_count} 条）"
            )

            return True, None

        except Exception as exc:
            error_msg = f"获取/保存数据失败: {str(exc)}"
            self.logger.error(f"{stock_name}({code}) {error_msg}")
            return False, error_msg
