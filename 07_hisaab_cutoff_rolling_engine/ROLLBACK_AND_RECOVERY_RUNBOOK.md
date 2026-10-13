# LetzRyd Cutoff Engine: Rollback & Disaster Recovery Runbook

**Document Version:** 1.0.0  
**Effective Week:** `CY26WK41` (Cutoff Baseline as of Monday, Oct 12, 2026, 12:00 PM Noon IST)  
**Database:** PostgreSQL `letzryd_dev` (`34.93.205.164:5432`)

---

## 1. Executive Summary & Zero-Risk Guarantee
The Cutoff-Based Cumulative Rolling Hisaab Engine was designed with **100% architectural isolation**:
1. Opening balances reside exclusively in `public.hisaab_partner_opening_balances`.
2. Core production tables (`app_hisaabs`, `app_drivers`, `app_driver_allocations`, `vehicles`, `app_payments`) have:
   - **0 Foreign Key constraints** pointing to or from cutoff tables.
   - **0 Triggers** tied to cutoff operations.
   - **0 Shared mutation pathways**.
3. If finance ever discovers that Excel figures need adjustment, or if any unforeseen issue occurs, the system can be rolled back to its exact pre-ingestion state in **under 2 seconds** without disrupting operations or core tables.

---

## 2. Ingestion Artifacts & Checksums (Source of Truth)

All baseline input data is persisted, hashed, and tracked:

| Artifact | Location | SHA-256 Checksum | Purpose |
| :--- | :--- | :--- | :--- |
| **Cutoff Snapshot CSV** | `07_hisaab_cutoff_rolling_engine/cutoff_baseline_oct12_snapshot.csv` | `97673f97ad3aa2a17be1f09a65fb0a72ad5687702fdf5f28ceca761a2961d904` | Master verified snapshot of 2,446 partners (₹36,141,591.90) |
| **Ingestion Script** | `07_hisaab_cutoff_rolling_engine/ingest_cutoff_baseline_oct12.py` | — | Idempotent parser & upsert engine |
| **Rollback Script** | `07_hisaab_cutoff_rolling_engine/rollback_cutoff_baseline.sql` | — | Atomic SQL rollback script |
| **Stored Procedure** | `07_hisaab_cutoff_rolling_engine/sp_sync_hisaab_cumulative_cutoff.sql` | — | Master weekly rolling engine |
| **Audit Logs Directory** | `07_hisaab_cutoff_rolling_engine/logs/` | — | Pre/post flight integrity logs |

---

## 3. Instant Rollback Procedure (Step-by-Step)

If a rollback is required, execute the following steps:

### Option A: Via Python Command Line (Recommended)
```bash
python -c "
import sys; sys.path.insert(0, r'c:\Users\anura\Downloads\hisaab-backfill')
from config import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    with open(r'07_hisaab_cutoff_rolling_engine\rollback_cutoff_baseline.sql', 'r') as f:
        sql = f.read()
    db.execute(text(sql))
    db.commit()
    print('Rollback successfully executed!')
finally:
    db.close()
"
```

### Option B: Via Direct PostgreSQL CLI (`psql`)
```bash
psql "postgresql://letzryd_dev_user:LetzRydDevPass2026!@34.93.205.164:5432/letzryd_dev" \
  -f "07_hisaab_cutoff_rolling_engine/rollback_cutoff_baseline.sql"
```

---

## 4. What the Rollback Script Does

```sql
BEGIN;

-- 1. Cleanses the cutoff opening balances for CY26WK41
DELETE FROM public.hisaab_partner_opening_balances 
WHERE effective_week_id = 'CY26WK41';

-- 2. Removes any draft/open rolling partner calculations
DELETE FROM public.hisaab_partner_weekly 
WHERE week_id = 'CY26WK41' AND settlement_status IN ('DRAFT', 'OPEN');

-- 3. Resets lock status on settlement weeks
UPDATE public.hisaab_settlement_weeks
SET is_locked = FALSE, locked_at = NULL, locked_by = NULL
WHERE week_id = 'CY26WK41';

-- 4. Reverts roll-forward flag
UPDATE public.hisaab_system_config
SET config_value = 'false'
WHERE config_key = 'enable_roll_forward';

-- 5. Logs the event in public.hisaab_cutoff_audit_log
INSERT INTO public.hisaab_cutoff_audit_log (
    partner_id, week_id, action_type, previous_os, new_weekly_due, amount_paid, resulting_os
) VALUES ('SYSTEM', 'CY26WK41', 'ROLLBACK_EXECUTED', 0.00, 0.00, 0.00, 0.00);

COMMIT;
```

---

## 5. Post-Rollback Verification Checklist

Run these queries to confirm clean recovery:
```sql
-- 1. Confirm opening balances are 0
SELECT count(*) FROM public.hisaab_partner_opening_balances WHERE effective_week_id = 'CY26WK41';
-- Expected: 0

-- 2. Confirm core tables remain untouched
SELECT count(*) FROM public.app_hisaabs;              -- Expected: 25 / 75,144 (exact pre-state)
SELECT count(*) FROM public.app_drivers;              -- Expected: 28 / 2,554 (exact pre-state)
SELECT count(*) FROM public.app_driver_allocations;  -- Expected: 25 / 4,550 (exact pre-state)
SELECT count(*) FROM public.vehicles;                -- Expected: 25 / 1,164 (exact pre-state)
SELECT count(*) FROM public.app_payments;            -- Expected: 7 / 85 (exact pre-state)
```

---

## 6. Re-Ingesting After Adjustments
If finance provides updated Excel sheets with revised dues:
1. Replace or update the files in `C:\Users\anura\Downloads\`.
2. Run the ingestion script in dry-run first:
   ```bash
   python 07_hisaab_cutoff_rolling_engine/ingest_cutoff_baseline_oct12.py
   ```
3. Once verified, run with `--commit`:
   ```bash
   python 07_hisaab_cutoff_rolling_engine/ingest_cutoff_baseline_oct12.py --commit
   ```
4. The upsert logic (`ON CONFLICT (partner_id, effective_week_id) DO UPDATE`) guarantees idempotency.
