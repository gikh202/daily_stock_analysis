from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.production_runner import STAGE11_ENTRYPOINT_VERSION, main, run


__all__ = ["STAGE11_ENTRYPOINT_VERSION", "main", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
