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
# Backfill year 2025 (all 52 weeks CY25WK01-52):
python 05_hisaab_generator/run_onetime_hisaab_only_backfill.py --year 2025 --commit

# Backfill year 2026 (all 35 historical weeks CY26WK01-35):
python 05_hisaab_generator/run_onetime_hisaab_only_backfill.py --year 2026 --commit
```

## Live Database Status (`CY25WK01 – CY26WK42`: 94 Consecutive Weeks)
- **`public.hisaab_vehicle_weekly`**: **`74,958` rows** across all **94 weeks** (`25,643` for 2025 + `49,315` for 2026).
- **`public.hisaab_partner_weekly`**: **`24,993` rows** across all **94 weeks** (`8,593` for 2025 + `16,400` for 2026, with exact 0.00 zero-variance parity).
- **Production Safety Guarantee**: `CY26WK36` through `CY26WK42` and all upstream `core_*`, `sheet_*`, and `daily_rent_log` tables remain **100% untouched**.

