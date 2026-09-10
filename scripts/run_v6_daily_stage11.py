from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily import production_runner as base_runner
from src.v6_daily.production_runner import STAGE11_ENTRYPOINT_VERSION
from src.v6_daily.production_runner_v9 import run


# base_runner.main resolves its module-global ``run`` at invocation time. Keep the
# original Stage 11 parser/gates and replace only the runtime implementation.
base_runner.run = run
main = base_runner.main

__all__ = ["STAGE11_ENTRYPOINT_VERSION", "main", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
