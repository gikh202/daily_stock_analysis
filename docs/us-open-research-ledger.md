# US Open Research Ledger

The U.S.-open confirmation system keeps the production email path separate from research persistence.

## Why

The historical backtest can reconstruct recent +15m sessions from Yahoo 1-minute data, but external intraday retention is not a durable research database. A rolling data source can cause old observations to disappear and can prevent the sample count from growing monotonically.

## Production evidence flow

1. `01-us-open-confirmation.yml` sends the actionable +15m confirmation.
2. After that workflow completes successfully on `main`, `02b-us-open-research-ledger.yml` runs independently.
3. It downloads the exact open-confirmation Artifact and the exact V6 source run referenced by `source_run_id`.
4. It restores the cumulative `open_confirmation_research/us_open_research.db` cache.
5. It records the current signal using the original decision timestamp and reconstructs only bars available through that timestamp.
6. It settles older pending signals with close return, +60m return, MFE, MAE, stop/target touches and modeled first-touch exit.
7. The cumulative standalone SQLite database and summary JSON are cached and uploaded as a 90-day Artifact.

The research workflow cannot trigger a second email. A research-data failure therefore cannot cause a notification retry loop.

## First-touch convention

A stop and target hit on different 1-minute bars are ordered by the first bar that touches either level. If both are touched inside the same 1-minute bar, the path is unknowable from OHLC data; the observation is marked `ambiguous_stop_target_same_bar` and no modeled exit return is fabricated.

## Backtest single source of truth

`scripts/probe_us_open_v2.py` calls the same `classify_confirmation_v2` implementation used by production at a synthetic 09:45 ET evaluation clock. This prevents research logic from silently drifting away from production rules.
