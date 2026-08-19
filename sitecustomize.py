from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import MutableSequence, Mapping


_DAILY_WORKFLOW_NAME = "每日股票分析"


def apply_daily_workflow_notification_guard(
    argv: MutableSequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Force the GitHub base-analysis entrypoint into no-notify mode.

    The large daily workflow deliberately keeps its existing environment mapping
    because those mappings are part of the repository's production compatibility
    contract. Notification suppression belongs at the Python entrypoint instead:
    V4/base analysis produces data and artifacts, while V6 remains the only
    post-close user notification.

    The guard is intentionally narrow and has no effect on local CLI runs, tests,
    other GitHub workflows, or the V6/open-confirmation notification paths.
    """
    target_argv = argv if argv is not None else sys.argv
    target_env = environ if environ is not None else os.environ

    if target_env.get("GITHUB_ACTIONS") != "true":
        return False
    if target_env.get("GITHUB_WORKFLOW") != _DAILY_WORKFLOW_NAME:
        return False
    if not target_argv or Path(str(target_argv[0])).name != "main.py":
        return False
    if "--no-notify" in target_argv:
        return False

    target_argv.append("--no-notify")
    return True


apply_daily_workflow_notification_guard()
