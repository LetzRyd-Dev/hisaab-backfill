# VARIABLES 3 & 4: ADJUSTMENTS & TRAFFIC CHALLANS ENGINE

**Module Directory:** `03_adjustments_challans/`  
**Target Persistence Tables:**
- `public.hisaab_adjustments_ledger` (Settlement ledger for debits/credits)
- `public.hisaab_vehicle_weekly` (`challan_amount`, `adjustment_amount`, `accident_deduction`)
**Upstream Input Tables:**
- `public.core_adjustments` (15,843 approved adjustments dating back to Dec 2025)
- `public.core_challans` (10,025 traffic notice records from Karnataka One scraper)
- `public.core_daily_vehicle_status` (Custody window on violation/incident date)

---

## 1. OBJECTIVE & ARCHITECTURAL ROLE
Variables 3 & 4 represent the non-operational financial debits and credits:
- **Variable 3 (Adjustments):** Manual corrections, downtime waivers, incentives, physical asset damage recoveries, and driver cash advances.
- **Variable 4 (Traffic Challans):** Real-time automated traffic violation notices captured by government traffic camera networks.

---

## 2. VARIABLE 3: ADJUSTMENT POLARITY & PARTITIONING

### A. Sign Polarity Conventions
In `core_adjustments` and `hisaab_adjustments_ledger`:
- **`DEBIT` (Driver Debt):** Adds to driver dues (`+` to balance). Includes speed violations, towing charges, manual lease rent corrections, and equipment damage.
- **`CREDIT` (Company Debt):** Reduces driver dues (`-` to balance). Includes referral bonuses, toll refunds, downtime credit for shop repairs, and battery replacement waivers.

### B. Mutually Exclusive Accident Partitioning
To eliminate double-counting of vehicle damage:
```sql
daily_challans = SUM(CASE 
    WHEN polarity = 'DEBIT' AND NOT (adjustment_category ILIKE '%accident%' OR adjustment_category ILIKE '%damage%') 
    THEN amount ELSE 0.00 END)

daily_accident_recovery = SUM(CASE 
    WHEN polarity = 'DEBIT' AND (adjustment_category ILIKE '%accident%' OR adjustment_category ILIKE '%damage%') 
    THEN amount ELSE 0.00 END)

daily_adjustments = SUM(CASE 
    WHEN polarity = 'CREDIT' 
    THEN amount ELSE 0.00 END)
```

### C. Approval Status Requirement
**Only adjustments with `approval_status = 'Approved'` take financial effect.** Any record marked `Pending`, `Draft`, or `Rejected` is strictly excluded.

---

## 3. VARIABLE 4: TRAFFIC CHALLANS & CUSTODY ATTRIBUTION

### A. Violation Date Matching
Challans are attached to the driver/operator who held the vehicle on the **exact timestamp of the violation** (`violation_date` / `violation_timestamp`):
1. Identify vehicle registration plate.
2. Query `core_daily_vehicle_status` on `status_date = challan.violation_date`.
3. If active custody is confirmed, attribute fine to `partner_id`.
4. If car was in Yard (`RFD`) or Maintenance on violation date, fine remains unallocated or tagged to company fleet ops.

### B. Payment Status Filter
**Paid fines are strictly excluded from Hisaab:**
- Included: `payment_status IN ('UNPAID', 'PARTIALLY_PAID')`
- Excluded: `payment_status = 'PAID'`

---

## 4. SCRIPTS IN THIS MODULE

1. **`audit_adjustments_challans.py` (100% Read-Only):**
   - Scans PostgreSQL for a target week and summarizes total approved debits, credits, accident recoveries, and unpaid challans.
   - Verifies custody attribution match rates.
2. **`sync_adjustments_challans.py` (Execution Runner):**
   - Enforces `HisaabSafetyGuard` to prevent touching protected weeks.
   - Populates `hisaab_adjustments_ledger` for the target settlement cycle.
   - Runs in DRY-RUN mode by default (automatic `ROLLBACK`), requiring `--commit` to persist.
3. **`verify_adjustments_challans.py` (Quality Gate Verifier):**
   - Asserts that 100% of attached adjustments have `approval_status = 'Approved'`.
   - Asserts zero double-counting between accident damages and challans.
   - Asserts that all attached challans have unpaid status.
