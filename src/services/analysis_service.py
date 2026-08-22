# -*- coding: utf-8 -*-
"""Application service for API-triggered stock analysis.

The service owns request/response adaptation and diagnostics.  Pipeline
construction and configuration lookup are injected through small callables so
unit tests do not need to import or instantiate the full orchestration graph.
"""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from src.enums import ReportType
from src.market_phase_summary import extract_market_phase_summary
from src.repositories.analysis_repo import AnalysisRepository
from src.report_language import (
    get_localized_stock_name,
    get_sentiment_label,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.schemas.decision_action import build_action_fields
from src.services.pipeline_factory import (
    ConfigProvider,
    PipelineFactory,
    create_analysis_pipeline,
    get_analysis_config,
)
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    build_run_diagnostic_summary,
    get_current_diagnostic_context,
    reset_run_diagnostic_context,
)

logger = logging.getLogger(__name__)


class AnalysisService:
    """Application-level stock-analysis use case."""

    def __init__(
        self,
        repository: Optional[AnalysisRepository] = None,
        config_provider: ConfigProvider = get_analysis_config,
        pipeline_factory: PipelineFactory = create_analysis_pipeline,
    ) -> None:
        """Initialize explicit service dependencies.

        Defaults preserve the existing runtime behavior.  Tests and alternate
        entry points can inject light-weight fakes without patching
        ``src.core.pipeline`` or the global configuration singleton.
        """

        self.repo = repository if repository is not None else AnalysisRepository()
        self._config_provider = config_provider
        self._pipeline_factory = pipeline_factory
        self.last_error: Optional[str] = None

    def analyze_stock(
        self,
        stock_code: str,
        report_type: str = "detailed",
        force_refresh: bool = False,
        query_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        send_notification: bool = True,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        skills: Optional[List[str]] = None,
        analysis_phase: str = "auto",
        query_source: str = "api",
        portfolio_context: Optional[Dict[str, Any]] = None,
        report_language: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute one stock-analysis request and adapt it for API consumers."""

        del force_refresh  # Retained for backward-compatible API signatures.

        diag_token = None
        try:
            self.last_error = None
            effective_query_id = query_id or uuid.uuid4().hex
            effective_trace_id = trace_id or effective_query_id

            if get_current_diagnostic_context() is None:
                diag_token = activate_run_diagnostic_context(
                    trace_id=effective_trace_id,
                    query_id=effective_query_id,
                    stock_code=stock_code,
                    trigger_source=query_source or "api",
                )

            # Some legacy callers/tests construct this service via object.__new__
            # and therefore bypass __init__. Keep that path compatible while
            # normal instances continue to use explicitly injected dependencies.
            config_provider = getattr(self, "_config_provider", get_analysis_config)
            pipeline_factory = getattr(self, "_pipeline_factory", create_analysis_pipeline)

            config = config_provider()
            normalized_report_language = normalize_report_language(
                report_language,
                default="",
            )
            if normalized_report_language:
                config = copy.copy(config)
                config.report_language = normalized_report_language

            pipeline = pipeline_factory(
                config=config,
                query_id=effective_query_id,
                trace_id=effective_trace_id,
                query_source=query_source or "api",
                progress_callback=progress_callback,
                analysis_skills=skills,
                analysis_phase=analysis_phase,
                portfolio_context=portfolio_context,
            )

            normalized_report_type = ReportType.from_str(report_type)
            result = pipeline.process_single_stock(
                code=stock_code,
                skip_analysis=False,
                single_stock_notify=send_notification,
                report_type=normalized_report_type,
            )

            if result is None:
                logger.warning("分析股票 %s 返回空结果", stock_code)
                self.last_error = self.last_error or f"分析股票 {stock_code} 返回空结果"
                return None

            if not getattr(result, "success", True):
                self.last_error = (
                    getattr(result, "error_message", None)
                    or f"分析股票 {stock_code} 失败"
                )
                logger.warning("分析股票 %s 未成功完成: %s", stock_code, self.last_error)
                return None

            return self._build_analysis_response(
                result,
                effective_query_id,
                report_type=normalized_report_type.value,
            )

        except Exception as exc:
            self.last_error = str(exc)
            logger.error("分析股票 %s 失败: %s", stock_code, exc, exc_info=True)
            return None
        finally:
            reset_run_diagnostic_context(diag_token)

    def _build_analysis_response(
        self,
        result: Any,
        query_id: str,
        report_type: str = "detailed",
    ) -> Dict[str, Any]:
        """Build the stable API response from an analyzer result."""

        sniper_points = {}
        if hasattr(result, "get_sniper_points"):
            sniper_points = result.get_sniper_points() or {}

        report_language = normalize_report_language(
            getattr(result, "report_language", "zh")
        )
        sentiment_label = get_sentiment_label(result.sentiment_score, report_language)
        stock_name = get_localized_stock_name(
            getattr(result, "name", None),
            result.code,
            report_language,
        )
        action_fields = build_action_fields(
            operation_advice=getattr(result, "operation_advice", None),
            explicit_action=getattr(result, "action", None),
            report_type=report_type,
            report_language=report_language,
            sentiment_score=getattr(result, "sentiment_score", None),
            guardrail_reason=getattr(result, "guardrail_reason", None),
            align_with_score=True,
        )

        diagnostic_context = get_current_diagnostic_context()
        trace_id = diagnostic_context.trace_id if diagnostic_context is not None else query_id
        diagnostic_snapshot = (
            diagnostic_context.snapshot() if diagnostic_context is not None else None
        )
        diagnostic_context_snapshot = getattr(
            result,
            "diagnostic_context_snapshot",
            None,
        )
        market_phase_summary = extract_market_phase_summary(
            diagnostic_context_snapshot
        )

        if isinstance(diagnostic_context_snapshot, dict):
            context_snapshot = dict(diagnostic_context_snapshot)
            if diagnostic_snapshot is not None:
                context_snapshot["diagnostics"] = diagnostic_snapshot
        elif diagnostic_snapshot is not None:
            context_snapshot = {"diagnostics": diagnostic_snapshot}
        else:
            context_snapshot = None

        raw_result_payload = result.to_dict() if hasattr(result, "to_dict") else None
        diagnostic_summary = build_run_diagnostic_summary(
            context_snapshot=context_snapshot,
            raw_result=raw_result_payload,
            report_saved=True,
            query_id=query_id,
            stock_code=result.code,
        )

        report = {
            "meta": {
                "query_id": query_id,
                "trace_id": trace_id,
                "stock_code": result.code,
                "stock_name": stock_name,
                "report_type": report_type,
                "report_language": report_language,
                "current_price": result.current_price,
                "change_pct": result.change_pct,
                "model_used": getattr(result, "model_used", None),
                "market_phase_summary": market_phase_summary,
            },
            "summary": {
                "analysis_summary": result.analysis_summary,
                "operation_advice": localize_operation_advice(
                    result.operation_advice,
                    report_language,
                ),
                "action": action_fields["action"],
                "action_label": action_fields["action_label"],
                "trend_prediction": localize_trend_prediction(
                    result.trend_prediction,
                    report_language,
                ),
                "sentiment_score": result.sentiment_score,
                "sentiment_label": sentiment_label,
            },
            "strategy": {
                "ideal_buy": sniper_points.get("ideal_buy"),
                "secondary_buy": sniper_points.get("secondary_buy"),
                "stop_loss": sniper_points.get("stop_loss"),
                "take_profit": sniper_points.get("take_profit"),
            },
            "details": {
                "news_summary": result.news_summary,
                "technical_analysis": result.technical_analysis,
                "fundamental_analysis": result.fundamental_analysis,
                "risk_warning": result.risk_warning,
            },
        }
        if isinstance(raw_result_payload, dict):
            report["details"]["raw_result"] = raw_result_payload

        return {
            "query_id": query_id,
            "trace_id": trace_id,
            "stock_code": result.code,
            "stock_name": stock_name,
            "report": report,
            "diagnostic_summary": diagnostic_summary,
        }
