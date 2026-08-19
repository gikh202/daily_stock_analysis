from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, MutableSequence, Sequence


_DAILY_WORKFLOW_NAME = "每日股票分析"
_LEGACY_FALLBACK_STOCK = "600519"


def _is_daily_main(
    argv: Sequence[str],
    environ: Mapping[str, str],
) -> bool:
    return bool(
        environ.get("GITHUB_ACTIONS") == "true"
        and environ.get("GITHUB_WORKFLOW") == _DAILY_WORKFLOW_NAME
        and argv
        and Path(str(argv[0])).name == "main.py"
    )


def apply_daily_workflow_notification_guard(
    argv: MutableSequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Force the GitHub base-analysis entrypoint into no-notify mode.

    The daily workflow keeps its full provider and notification environment
    mapping because those mappings are part of the repository's production
    compatibility contract. V4/base analysis only produces data and artifacts;
    V6 remains the single post-close user notification.
    """
    target_argv = argv if argv is not None else sys.argv
    target_env = environ if environ is not None else os.environ

    if not _is_daily_main(target_argv, target_env):
        return False
    if "--no-notify" in target_argv:
        return False

    target_argv.append("--no-notify")
    return True


def uses_unconfigured_daily_fallback_stock(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Detect the workflow's legacy fallback ticker before it can spend LLM quota.

    A deliberately configured ``600519`` still has ``STOCK_LIST_CONFIG=600519``
    and is therefore allowed. Only the exact legacy fallback shape is blocked.
    """
    target_argv = argv if argv is not None else sys.argv
    target_env = environ if environ is not None else os.environ

    if not _is_daily_main(target_argv, target_env):
        return False
    configured = str(target_env.get("STOCK_LIST_CONFIG") or "").strip()
    effective = str(target_env.get("STOCK_LIST") or "").strip()
    return not configured and effective == _LEGACY_FALLBACK_STOCK


apply_daily_workflow_notification_guard()
if uses_unconfigured_daily_fallback_stock():
    raise SystemExit(
        "STOCK_LIST is not configured; refusing the legacy 600519 fallback "
        "to avoid unrelated paid LLM calls."
    )
