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
python 01_custody_engine/audit_custody_coverage.py --week CY26WK28
```
