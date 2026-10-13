# LetzRyd Hisaab Backfill Engine

This repository houses the standalone historical backfill engine, verification scripts, and audit reports for generating historical Hisaab settlement cycles across PostgreSQL for the LetzRyd EV commercial fleet.

See the complete [ROADMAP.md](ROADMAP.md) for the variable-by-variable specification.

## Execution Flow (One Variable at a Time)

```
01_custody_engine/         -> [Variable 1] Reconstruct vehicle custody & on-road attendance
02_rent_engine/            -> [Variable 2] Calculate daily lease rent & indemnity fees
03_adjustments_challans/   -> [Variable 3 & 4] Attach approved adjustments and unpaid challans
04_platform_feeds/         -> [Variable 5 & 6] Ingest/zero-fill Uber, Ola, Rapido and GPS telematics
05_hisaab_generator/       -> [Variable 7 & 8] Run weekly vehicle settlement & partner consolidation
06_reports_and_parity/     -> Audit zero-variance parity and export Excel statements for sign-off
```

## Quick Start

```bash
pip install -r requirements.txt

# One-Time Hisaab-Only Backfill (writes ONLY to hisaab_vehicle_weekly & hisaab_partner_weekly)
# Dry-run across all 35 historical weeks (CY26WK01-CY26WK35):
python 05_hisaab_generator/run_onetime_hisaab_only_backfill.py

# Execute and commit to PostgreSQL:
python 05_hisaab_generator/run_onetime_hisaab_only_backfill.py --commit
```

## Live Database Status (`CY26WK01 – CY26WK42`)
- **`public.hisaab_vehicle_weekly`**: `49,315` rows across all **42 weeks** (`37,214` unified backfilled rows + `12,101` live production rows).
- **`public.hisaab_partner_weekly`**: `16,400` rows across all **42 weeks** (`12,864` unified backfilled rows + `3,536` live production rows, all historical weeks isolated with `previous_outstanding = 0.00`).
- **August 2026 Onwards & Production Untouched**: `CY26WK36` through `CY26WK42` and all upstream `core_*`, `sheet_*`, and `daily_rent_log` tables remain **100% untouched**.

