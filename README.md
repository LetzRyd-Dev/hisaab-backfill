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
# Dry-run across all 33 historical weeks (CY26WK01-CY26WK25, CY26WK28-CY26WK35):
python 05_hisaab_generator/run_onetime_hisaab_only_backfill.py --all-missing

# Execute and commit to PostgreSQL:
python 05_hisaab_generator/run_onetime_hisaab_only_backfill.py --all-missing --commit
```

## Live Database Status (`CY26WK01 – CY26WK41`)
- **`public.hisaab_vehicle_weekly`**: `47,548` rows across all **41 weeks** (`12,665` protected rows + `34,883` backfilled rows).
- **`public.hisaab_partner_weekly`**: `15,940` rows across all **41 weeks** (`4,029` protected rows + `11,911` backfilled rows, all backfilled weeks isolated with `previous_outstanding = 0.00`).
- **Protected Cycles Untouched**: `CY26WK26`, `CY26WK27`, `CY26WK36–CY26WK41` and all upstream `core_*`, `sheet_*`, and `daily_rent_log` tables remain **100% untouched**.

