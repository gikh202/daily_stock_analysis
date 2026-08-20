from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.run_us_open_timing import run as run_timing

logger=logging.getLogger("us_open_confirmation_safe"); NY=ZoneInfo("America/New_York")

def _env_float(name: str, default: float) -> float:
    raw=str(os.getenv(name) or "").strip()
    if not raw: return default
    try: return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; use default %s",name,raw,default); return default

def _truthy(value: Any) -> bool: return str(value or "").strip().lower() in {"1","true","yes","on"}

def _policy() -> dict[str,float]:
    return {"normal_chase_tolerance_pct":_env_float("OPEN_CONFIRMATION_CHASE_TOLERANCE_PCT",.5),"momentum_chase_tolerance_pct":_env_float("OPEN_CONFIRMATION_MOMENTUM_CHASE_PCT",.75),"weak_open_pct":_env_float("OPEN_CONFIRMATION_WEAK_OPEN_PCT",-.5),"min_volume_ratio":_env_float("OPEN_CONFIRMATION_MIN_VOLUME_RATIO",.70),"min_opening_range_position":_env_float("OPEN_CONFIRMATION_MIN_OPENING_RANGE_POSITION",.25),"momentum_min_opening_range_position":_env_float("OPEN_CONFIRMATION_MOMENTUM_MIN_RANGE_POSITION",.50),"momentum_min_volume_ratio":_env_float("OPEN_CONFIRMATION_MOMENTUM_MIN_VOLUME_RATIO",.70),"max_quote_age_minutes":_env_float("OPEN_CONFIRMATION_MAX_QUOTE_AGE_MINUTES",8.0),"starter_position_pct":_env_float("OPEN_CONFIRMATION_STARTER_POSITION_PCT",10.0)}

def _near_open_retry_seconds(now: datetime | None=None) -> float:
    current=(now or datetime.now(NY)).astimezone(NY)
    if current.hour!=9 or current.minute<30 or current.minute>=35: return 0.0
    target=current.replace(hour=9,minute=35,second=5,microsecond=0); return max(0.0,min(300.0,(target-current).total_seconds()))

def _run_once(*,v6_payload_path: str|Path,output_dir: str|Path,source_run_id: str|None,notify: bool,previous_state_path: str|Path|None,force_notify: bool,allow_all_unavailable: bool) -> dict[str,Any]:
    return run_timing(v6_payload_path=v6_payload_path,output_dir=output_dir,source_run_id=source_run_id,notify=notify,previous_state_path=previous_state_path,force_notify=force_notify,allow_all_unavailable=allow_all_unavailable,**_policy())

def run_safe(*,v6_payload_path: str|Path,output_dir: str|Path,source_run_id: str|None,notify: bool,previous_state_path: str|Path|None=None,force_notify: bool=False) -> dict[str,Any]:
    try: return _run_once(v6_payload_path=v6_payload_path,output_dir=output_dir,source_run_id=source_run_id,notify=notify,previous_state_path=previous_state_path,force_notify=force_notify,allow_all_unavailable=False)
    except RuntimeError as exc:
        text=str(exc)
        if "all live U.S. session quotes unavailable" not in text: raise
        retry_seconds=_near_open_retry_seconds()
        if retry_seconds>0:
            logger.info("opening bars are warming up; retry V7 intraday timing in %.1f seconds",retry_seconds); time_module.sleep(retry_seconds)
            try: return _run_once(v6_payload_path=v6_payload_path,output_dir=output_dir,source_run_id=source_run_id,notify=notify,previous_state_path=previous_state_path,force_notify=force_notify,allow_all_unavailable=False)
            except RuntimeError as retry_exc:
                if "all live U.S. session quotes unavailable" not in str(retry_exc): raise
        logger.warning("all live quotes unavailable; emit non-actionable V7 timing state")
        return _run_once(v6_payload_path=v6_payload_path,output_dir=output_dir,source_run_id=source_run_id,notify=notify,previous_state_path=previous_state_path,force_notify=force_notify,allow_all_unavailable=True)

def main() -> int:
    parser=argparse.ArgumentParser(description="Fail-safe V7 U.S. open timing decision using the actual execution clock")
    parser.add_argument("--v6-payload",required=True); parser.add_argument("--output-dir",default="open_confirmation_reports"); parser.add_argument("--source-run-id",default=os.getenv("OPEN_CONFIRMATION_SOURCE_RUN_ID")); parser.add_argument("--previous-state",default=os.getenv("OPEN_CONFIRMATION_PREVIOUS_STATE")); parser.add_argument("--notify",action="store_true"); parser.add_argument("--force-notify",action="store_true",default=_truthy(os.getenv("OPEN_CONFIRMATION_FORCE_NOTIFY"))); args=parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO").upper(),format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    result=run_safe(v6_payload_path=args.v6_payload,output_dir=args.output_dir,source_run_id=args.source_run_id,notify=args.notify,previous_state_path=args.previous_state,force_notify=args.force_notify); print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0

if __name__=="__main__": raise SystemExit(main())
