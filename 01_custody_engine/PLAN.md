# VARIABLE 1: VEHICLE CUSTODY & ATTENDANCE ENGINE

**Module Directory:** `01_custody_engine/`  
**Target Persistence Table:** `public.core_daily_vehicle_status`  
**Upstream Ground Truth Feeds:**
- `public.core_vehicle_allocation` (Allocations dating back to Jan 2024)
- `public.core_dropoffs` (Returns & Maintenance handovers dating back to Nov 2024)
- `public.core_vehicle_onboarding` (Fleet vehicle registration & metadata)
- `public.sheet_vehicle_status` (Aug 2026 onwards, ground truth when available)

---

## 1. OBJECTIVE & ARCHITECTURAL ROLE
Vehicle Custody is **Variable 1** and the foundational layer of the entire Hisaab Engine. 

Before lease rent can be calculated (Variable 2) or platform earnings offset (Variable 5), the system must determine:
1. **Who had the vehicle on each calendar day?** (`partner_id`, `partner_name`, `partner_phone`, `partner_type`).
2. **What was the vehicle's operational state?** (`Active`, `RFD`, `Maintenance`, `Same Day D&A`).
3. **Was this day billable for lease rent?** (`billable_rent_day = TRUE / FALSE`).

If a target week has zero records in `core_daily_vehicle_status`, downstream rent calculation (`sp_calculate_daily_rent`) and Hisaab settlement (`sp_sync_hisaab_vehicle_weekly`) cannot execute.

---

## 2. THE CUSTODY RESOLUTION WATERFALL

For each day in the target cycle `[week_start, week_end]` and for every fleet vehicle registered in `core_vehicle_onboarding`:

```
+-----------------------------------------------------------------------------+
| Check: Does a record exist in sheet_vehicle_status for (date, vehicle)?     |
+-----------------------------------------------------------------------------+
      │                                                     │
     YES (August 2026 onwards)                             NO (Historical dates)
      ▼                                                     ▼
Use Sheet Ground Truth:                               Trigger PORTAL_FALLBACK:
- final_status = svs.final_status                     Evaluate core_vehicle_allocation
- partner_id = svs.partner_id                         matched with core_dropoffs
- source_origin = 'SHEET_GROUND_TRUTH'                - source_origin = 'PORTAL_FALLBACK'
```

### Detailed PORTAL_FALLBACK Decision Logic:
1. **Allocation Match:** Find the latest allocation record where `allocation_date <= target_date` and `is_deleted = FALSE`.
2. **Drop-Off Match:** Find the earliest return record after that allocation where `return_date >= allocation_date` and `is_deleted = FALSE`.
3. **State Classification:**
   - **`Same Day D&A`:** If an allocation event AND a dropoff event occurred on the exact same date.
   - **`Allocation`:** If target date is the exact allocation date.
   - **`Maintenance`:** If dropped off for `'Repair and Maintenance'` or `'Vehicle Breakdown / Maintenance'` and still in maintenance.
   - **`Drop Off`:** If dropped off on target date.
   - **`Active` (Billable On-Road):** If allocated and not dropped off (or dropoff date > target date).
   - **`RFD` (Yard / Ready For Deployment):** Unassigned vehicle or returned vehicle sitting in hub.

---

## 3. BILLABILITY RULES & ATTRIBUTION

| Classified Status | `billable_rent_day` | Partner Attribution (`partner_id`) | Daily Lease Rent |
| :--- | :---: | :--- | :---: |
| **`Active`** | **`TRUE`** | Custodian Partner ID (`LETZ...`) | Contracted Rent |
| **`Allocation`** | **`TRUE`** | New Custodian Partner ID | Contracted Rent |
| **`Same Day D&A`** | **`TRUE`** | Dropping Partner (or primary custodian) | Contracted Rent |
| **`Maintenance`** | **`FALSE`** | `NULL` | ₹0.00 |
| **`Drop Off`** | **`FALSE`** | `NULL` | ₹0.00 |
| **`RFD` (Yard)** | **`FALSE`** | `NULL` | ₹0.00 |

---

## 4. SCRIPTS IN THIS MODULE

1. **`audit_custody_dryrun.py` (100% Read-Only):**
   - Simulates custody reconstruction in memory without writing a single row to PostgreSQL.
   - Displays daily fleet counts (Active vs RFD vs Maintenance), city distribution, and identifies any data gaps.
2. **`backfill_custody.py` (Execution Runner):**
   - Enforces `HisaabSafetyGuard` pre-flight checks.
   - Executes `sp_refresh_vehicle_status_range(start_date, end_date)`.
   - Safe by default: runs in a transaction and issues an automatic `ROLLBACK` unless `--commit` is explicitly passed.
3. **`verify_custody_output.py` (Quality Gate Verifier):**
   - Asserts that all rows in `core_daily_vehicle_status` for the target week pass data-quality standards before moving to Variable 2 (Rent).
