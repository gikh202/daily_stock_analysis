from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .fusion_contracts import FinalDecisionPacket


FINAL_DECISION_AUDIT_SCHEMA_VERSION = "final-decision-audit-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _date(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else ""


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_db(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return 1 if value else 0


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class FinalDecisionAuditStore:
    """Append-only persistence for the final V4+V6 decision contract.

    The audit table deliberately stores the full immutable packet JSON together
    with queryable columns. It does not overwrite prior rows. Re-running the
    exact same source identity + packet is idempotent through ``audit_key``;
    changing engine version, source signal, fusion result or policy packet
    creates a new auditable row.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS v6_final_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_schema_version TEXT NOT NULL,
                    persisted_at TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    instrument_type TEXT NOT NULL,
                    effective_trade_date TEXT,
                    packet_schema_version TEXT NOT NULL,
                    assessment_scope TEXT NOT NULL,
                    assessment_is_final INTEGER NOT NULL,
                    verdict TEXT NOT NULL,
                    worth_buying INTEGER,
                    execution_authorized INTEGER NOT NULL,
                    agreement TEXT NOT NULL,
                    v4_direction TEXT NOT NULL,
                    v6_direction TEXT NOT NULL,
                    v4_horizon TEXT,
                    v4_expected_return_pct REAL,
                    v6_forecast_score REAL,
                    opportunity_score REAL,
                    risk_score REAL,
                    evidence_coverage REAL NOT NULL,
                    v4_operation TEXT,
                    v6_decision TEXT NOT NULL,
                    non_trading INTEGER NOT NULL,
                    source_payload_version TEXT,
                    source_engine_version TEXT NOT NULL,
                    source_v6_signal_id INTEGER,
                    source_analysis_history_id INTEGER,
                    source_query_id TEXT,
                    packet_hash TEXT NOT NULL,
                    audit_key TEXT NOT NULL UNIQUE,
                    packet_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_v6_final_decisions_symbol_date
                    ON v6_final_decisions(symbol, effective_trade_date, id);
                CREATE INDEX IF NOT EXISTS ix_v6_final_decisions_engine_date
                    ON v6_final_decisions(source_engine_version, effective_trade_date, id);
                CREATE INDEX IF NOT EXISTS ix_v6_final_decisions_verdict_date
                    ON v6_final_decisions(verdict, effective_trade_date, id);
                CREATE INDEX IF NOT EXISTS ix_v6_final_decisions_history_engine
                    ON v6_final_decisions(source_analysis_history_id, source_engine_version);
                """
            )

    def quick_check(self) -> str:
        with self.connect() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    @staticmethod
    def _board_by_symbol(payload: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
        result: Dict[str, Mapping[str, Any]] = {}
        for item in payload.get("board") or []:
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("code") or "").strip().upper()
            if code:
                result[code] = item
        return result

    def persist_packets(
        self,
        payload: Mapping[str, Any],
        packets: Sequence[FinalDecisionPacket],
        *,
        report_date: Optional[str] = None,
        source_engine_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        board = self._board_by_symbol(payload)
        report_day = (
            _date(report_date)
            or _date(payload.get("generated_at"))
            or datetime.now(timezone.utc).date().isoformat()
        )
        inserted = 0
        existing = 0

        with self.connect() as conn:
            for packet in packets:
                source = board.get(packet.symbol, {})
                engine_version = str(
                    source.get("engine_version")
                    or source_engine_version
                    or payload.get("version")
                    or "unknown"
                ).strip()
                packet_dict = packet.to_dict()
                packet_hash = _stable_hash(packet_dict)
                source_identity = {
                    "source_payload_version": payload.get("version"),
                    "source_engine_version": engine_version,
                    "source_v6_signal_id": _int_or_none(source.get("id")),
                    "source_analysis_history_id": _int_or_none(source.get("analysis_history_id")),
                    "source_query_id": source.get("query_id"),
                    "packet": packet_dict,
                }
                audit_key = _stable_hash(source_identity)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO v6_final_decisions(
                        audit_schema_version, persisted_at, report_date,
                        symbol, instrument_type, effective_trade_date,
                        packet_schema_version, assessment_scope, assessment_is_final,
                        verdict, worth_buying, execution_authorized, agreement,
                        v4_direction, v6_direction, v4_horizon,
                        v4_expected_return_pct, v6_forecast_score,
                        opportunity_score, risk_score, evidence_coverage,
                        v4_operation, v6_decision, non_trading,
                        source_payload_version, source_engine_version,
                        source_v6_signal_id, source_analysis_history_id, source_query_id,
                        packet_hash, audit_key, packet_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        FINAL_DECISION_AUDIT_SCHEMA_VERSION,
                        _utc_now(),
                        report_day,
                        packet.symbol,
                        packet.instrument_type,
                        packet.effective_trade_date,
                        packet.schema_version,
                        packet.assessment.scope,
                        int(bool(packet.assessment.is_final)),
                        packet.assessment.verdict.value,
                        _bool_db(packet.assessment.worth_buying),
                        int(bool(packet.assessment.execution_authorized)),
                        packet.agreement.value,
                        packet.v4_direction,
                        packet.v6_direction,
                        packet.v4_horizon,
                        packet.v4_expected_return_pct,
                        packet.v6_forecast_score,
                        packet.opportunity_score,
                        packet.risk_score,
                        float(packet.evidence_coverage),
                        packet.v4_operation,
                        packet.v6_decision,
                        int(bool(packet.non_trading)),
                        str(payload.get("version") or ""),
                        engine_version,
                        _int_or_none(source.get("id")),
                        _int_or_none(source.get("analysis_history_id")),
                        str(source.get("query_id") or ""),
                        packet_hash,
                        audit_key,
                        _json(packet_dict),
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
                else:
                    existing += 1

            total_rows = int(
                conn.execute("SELECT COUNT(*) FROM v6_final_decisions").fetchone()[0]
            )

        quick = self.quick_check()
        if quick.strip().lower() != "ok":
            raise RuntimeError(f"final decision audit quick_check failed: {quick}")
        return {
            "schema_version": FINAL_DECISION_AUDIT_SCHEMA_VERSION,
            "report_date": report_day,
            "packets": len(packets),
            "inserted": inserted,
            "existing": existing,
            "table_rows": total_rows,
            "quick_check": quick,
        }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["assessment_is_final"] = bool(item.get("assessment_is_final"))
        worth = item.get("worth_buying")
        item["worth_buying"] = None if worth is None else bool(worth)
        item["execution_authorized"] = bool(item.get("execution_authorized"))
        item["non_trading"] = bool(item.get("non_trading"))
        try:
            item["packet"] = json.loads(str(item.get("packet_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            item["packet"] = None
        return item

    def history(
        self,
        symbol: str,
        *,
        source_engine_version: Optional[str] = None,
        limit: int = 200,
    ) -> list[Dict[str, Any]]:
        clauses = ["symbol=?"]
        params: list[Any] = [str(symbol or "").strip().upper()]
        if source_engine_version:
            clauses.append("source_engine_version=?")
            params.append(str(source_engine_version))
        params.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v6_final_decisions WHERE "
                + " AND ".join(clauses)
                + " ORDER BY COALESCE(effective_trade_date, report_date) DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def transitions(
        self,
        symbol: str,
        *,
        source_engine_version: Optional[str] = None,
        limit: int = 200,
    ) -> list[Dict[str, Any]]:
        rows = list(reversed(self.history(
            symbol,
            source_engine_version=source_engine_version,
            limit=limit,
        )))
        transitions: list[Dict[str, Any]] = []
        tracked = (
            "verdict",
            "worth_buying",
            "execution_authorized",
            "agreement",
            "v4_direction",
            "v6_direction",
            "v6_decision",
        )
        for previous, current in zip(rows, rows[1:]):
            changes = {
                field: {"before": previous.get(field), "after": current.get(field)}
                for field in tracked
                if previous.get(field) != current.get(field)
            }
            if not changes:
                continue
            transitions.append(
                {
                    "symbol": current.get("symbol"),
                    "from_id": previous.get("id"),
                    "to_id": current.get("id"),
                    "from_date": previous.get("effective_trade_date") or previous.get("report_date"),
                    "to_date": current.get("effective_trade_date") or current.get("report_date"),
                    "source_engine_version": current.get("source_engine_version"),
                    "changes": changes,
                }
            )
        return transitions
