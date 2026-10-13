# LetzRyd Cutoff-Based Cumulative Rolling Hisaab Engine
## Master Knowledge Base & Operational Guide

---

## 1. Executive Summary

The **LetzRyd Cutoff-Based Cumulative Rolling Hisaab Engine** solves the historical fragmentation of partner balances across legacy Google Sheets and previous billing periods. 

### Core Principles
1. **Monday 12:00 PM Noon IST Cutoff:** Settlement weeks cover Monday 00:00 to Sunday 23:59:59. A grace period is provided until Monday 12:00 PM (Noon) to ingest weekend trips and cash payments before the week is permanently locked.
2. **Permanent Immature Week Lock:** Once locked (`is_locked = TRUE`), a historical week is **immutable**. It cannot be recalculated, re-run, or modified retroactively.
3. **Automatic Open-Week Flow for Late Data (Method 1):** If any operational variables belonging to a locked week arrive after Monday 12:00 PM (Ola/Uber delayed trips, traffic challans, accident deductions, bonuses, manual adjustments), they **never mutate the locked week**. Instead, they flow directly into the **active open week** as prior-period adjustments.
4. **Administrative Temporary Unlock:** In extraordinary operational emergencies (e.g., massive trip reconciliation failure), finance admins can temporarily unlock a week with an automated expiry timer and a mandatory audit trail.
5. **Zero Mutation on Core Tables:** Core production tables (`app_hisaabs`, `app_drivers`, `app_driver_allocations`, `vehicles`, `app_payments`) remain 100% untouched and preserved.

---

## 2. Weekly Settlement Timeline & Cutoff Mechanics

```
 Monday 00:00                    Sunday 23:59:59         Monday 12:00 PM Noon IST
      |                                 |                          |
      v                                 v                          v
+-----------------------------------------------+------------------+------------------------>
|           OPERATING WEEK (Days 1 to 7)        |   GRACE WINDOW   | NEW OPEN WEEK STARTS
|        Drivers drive, earn, collect cash      | Sync weekend     | Week N is LOCKED!
|                                               | payments & trips | Late data flows here.
+-----------------------------------------------+------------------+------------------------>
```

### The Weekly Cycle
* **Operating Window:** Monday 00:00:00 to Sunday 23:59:59 (Days 1 to 7).
* **Grace Window:** Sunday midnight to Monday 12:00 PM (Noon). Operations teams and cron jobs push payments and weekend trip data.
* **The Lock Moment:** Exactly at Monday 12:00 PM (Noon) IST:
  * Week $N$ transitions to `is_locked = TRUE`.
  * Week $N+1$ becomes the active open week (`is_locked = FALSE`).
  * Drivers' PDF statements for Week $N$ become final and static.

---

## 3. Single Source of Truth Baseline (Day Zero)

On **October 12, 2026, at 12:00 PM Noon IST**, the baseline opening dues were extracted from the audited city payment workbooks (Hyderabad, Bangalore, Mumbai) and ingested into `public.hisaab_partner_opening_balances`:

* **Target Week ID:** `CY26WK41`
* **Total Partners Ingested:** 2,446
* **Total Baseline Operational Dues ($B_0$):** **INR 36,141,591.90**
* **City Distribution:**
  * **Hyderabad (HYD):** 479 partners | INR 12,976,581.26
  * **Bangalore (BLR):** 1,698 partners | INR 19,124,238.27
  * **Mumbai (MUM):** 269 partners | INR 4,040,772.37

### Schema: `public.hisaab_partner_opening_balances`
| Column | Type | Description |
| :--- | :--- | :--- |
| `partner_id` | `VARCHAR` | Driver or Operator code (e.g., `HYD102`, `BLR-OP-04`) |
| `effective_week_id`| `VARCHAR` | Cutoff baseline week (`CY26WK41`) |
| `opening_balance_due` | `NUMERIC(12,2)` | Net verified operational debt owed to LetzRyd |
| `remarks` | `TEXT` | JSON payload containing city, phone, name, vehicle count, status |
| `created_by` | `VARCHAR` | Admin/auditor responsible for ingestion |

---

## 4. Post-Cutoff Late Data Flow (Method 1: Open Week Auto-Flow)

### The Business Requirement
> *"If any variables or data for the last week (Monday to Sunday) arrive after Monday 12:00 PM, they are added as adjustments to the next week's hisaab. Week 1–7 remains locked and untouched. The additions flow to next week as amounts directly."*

### How it Operates in PostgreSQL

#### Layer 1: Dedicated Aggregator Sync Procedure (`sp_route_late_aggregator_sync`)
When Ola, Uber, or Rapido send late reconciliation batches or delayed incentives:
1. The procedure checks `is_locked` on the original week.
2. If `is_locked = TRUE`, it computes the net delta:
   $$\text{Net Delta} = \text{Platform Earnings Delta} - \text{Cash Collected Delta}$$
   * If $\text{Earnings} > \text{Cash}$: Company owes driver $\rightarrow$ `polarity = 'CREDIT'`
   * If $\text{Cash} > \text{Earnings}$: Driver collected cash $\rightarrow$ `polarity = 'DEBIT'`
3. It inserts the delta directly into `public.hisaab_adjustments_ledger` with `settlement_week_id` set to the **active open week** and `is_prior_period = TRUE`.

#### Layer 2: Automatic Interception Database Trigger (`trg_auto_route_adjustment`)
Even if an external service, manual operator, or un-updated cron job attempts to execute an `INSERT` directly targeting a locked week (e.g., a delayed traffic challan from date 5 targeting `CY26WK41`):
* The trigger function `fn_trg_auto_route_prior_period_adjustment()` intercepts the write **BEFORE INSERT**.
* It inspects `is_locked`. If `TRUE`, it re-routes:
  * `NEW.settlement_week_id := v_next_open_week;`
  * `NEW.is_prior_period := TRUE;`
  * `NEW.remarks := ... [Auto-routed from locked week CY26WK41 to CY26WK42];`

#### Layer 3: Rolling Calculation Procedure (`sp_sync_hisaab_cumulative_cutoff`)
When calculating the active open week, all prior-period adjustments are aggregated directly:
$$\text{prior\_period\_adjustments} = \sum (\text{DEBIT}) - \sum (\text{CREDIT})$$
And included into the partner's total outstanding:
$$\text{total\_outstanding} = \max(0, \text{previous\_outstanding} + \text{current\_week\_os} + \mathbf{prior\_period\_adjustments} - \text{amount\_paid\_during\_week})$$

---

## 5. Temporary Administrative Unlock & Re-lock Controls

If an emergency manual correction or historical re-sync is required, authorized administrators can temporarily unlock a locked week.

### Option A: Using the Python Admin CLI
```bash
# 1. Unlock week CY26WK41 for 2 hours with audit reason
python 07_hisaab_cutoff_rolling_engine/admin_manage_lock.py unlock CY26WK41 --hours 2 --reason "Correcting platform incentive delta" --user "finance_lead"

# 2. Check live lock status anytime
python 07_hisaab_cutoff_rolling_engine/admin_manage_lock.py status

# 3. Re-lock immediately once finished
python 07_hisaab_cutoff_rolling_engine/admin_manage_lock.py lock CY26WK41 --reason "Adjustments verified and reconciled" --user "finance_lead"
```

### Option B: Using PostgreSQL Stored Procedures Directly
```sql
-- 1. Unlock week for 3 hours
CALL public.sp_admin_unlock_settlement_week(
    p_week_id => 'CY26WK41',
    p_unlock_duration_hours => 3,
    p_admin_user => 'ops_admin',
    p_reason => 'Emergency trip sync revision'
);

-- 2. Lock back immediately
CALL public.sp_admin_lock_settlement_week(
    p_week_id => 'CY26WK41',
    p_admin_user => 'ops_admin',
    p_reason => 'Re-sync complete'
);
```

### Safety Features
* **Auto-Expire Window:** When unlocked, `lock_cutoff_at` is extended by the requested duration (default 2 hours). If the admin forgets to lock it back, the procedure auto-relocks it as soon as the timer elapses.
* **Audit Trail:** Every lock and unlock operation is permanently logged in `public.hisaab_cutoff_audit_log` with user, timestamp, duration, and reason.

---

## 6. Mathematical Formulations & Universal Sign Conventions

### Balance Roll-Forward Equation
For any partner in week $W$:

$$\text{Previous Outstanding}_W = 
\begin{cases} 
\text{opening\_balance\_due} & \text{if } W = \text{CY26WK41 (Baseline)} \\
\text{Total Outstanding}_{W-1} & \text{if } W > \text{CY26WK41}
\end{cases}$$

$$\text{Current Week Operational OS}_W = \text{Net Rent Billed} + \text{Cash Collected} + \text{Challans} + \text{Accidents} + \text{TDS} - \text{Platform Earnings} - \text{Incentives}$$

$$\text{Total Outstanding}_W = \max\Big(0.00, \; \text{Previous Outstanding}_W + \text{Current Week OS}_W + \text{Prior Period Adjustments}_W - \text{Payments During Week}_W\Big)$$

$$\text{Net Bank Payout}_W = 
\begin{cases}
|\text{Total Due}| & \text{if Total Due} < 0.00 \\
0.00 & \text{otherwise}
\end{cases}$$

---

## 7. Core Production Tables Zero-Mutation Protection

The Cutoff Engine guarantees **zero mutation** on core operational production tables.

### Verified Production Baseline
| Table Name | Row Count | Protection Mechanism |
| :--- | :---: | :--- |
| `public.app_hisaabs` | 25 | Fully isolated. Engine writes to `hisaab_partner_weekly`. |
| `public.app_drivers` | 28 | Read-only lookup for driver names and bank details. |
| `public.app_driver_allocations` | 25 | Read-only lookup for assigned vehicle numbers. |
| `public.vehicles` | 25 | Read-only lookup for fleet metadata. |
| `public.app_payments` | 7 | Real-time payment ingestion table. Never updated or dropped by engine. |

Run safety check anytime:
```bash
python 07_hisaab_cutoff_rolling_engine/verify_final_safety.py
```

---

## 8. Verification & Test Suite

The engine includes an automated end-to-end verification suite:
```bash
python 07_hisaab_cutoff_rolling_engine/run_full_validation_suite.py
```

### Verified Test Cases
1. **Opening Balances Baseline:** 2,446 partners matching ₹36,141,591.90.
2. **City Breakdown:** HYD (479), BLR (1,698), MUM (269) matching Excel sources.
3. **Week Lock Status:** `CY26WK41` locked, `CY26WK42` open.
4. **Immutable Guard:** Attempts to recalculate locked week are safely rejected with zero mutations.
5. **Admin Unlock & Relock:** Full unlock, audit logging, and relock cycle verified.
6. **Late Data Auto-Routing:** Late Ola deltas and traffic challans automatically redirected to open week with `is_prior_period = TRUE`.
7. **Zero-Mutation Safety:** Pre/post row counts match 100% across all 5 core tables.

---

## 9. Frequently Asked Questions (FAQ)

**Q: What happens if a driver pays via UPI/Cashfree after Monday 12:00 PM?**
* **A:** The payment credits immediately to the new open week. The driver's live net debt drops instantly.

**Q: Can a driver see why an adjustment was made in the app?**
* **A:** Yes. In the hisaab breakdown, `prior_period_adjustments` clearly displays the category (e.g., `OLA_LATE_SYNC`, `CHALLAN`), amount, and the origin incident date.

**Q: What if Ola takes 3 weeks to send an incentive or toll reimbursement?**
* **A:** No problem. When the file is processed, `sp_route_late_aggregator_sync` detects that the original week is locked, and routes the credit as an adjustment into the currently active open week.

**Q: Where can I review the audit log of all unlocks and locks?**
* **A:** Run `SELECT * FROM public.hisaab_cutoff_audit_log ORDER BY created_at DESC;` in PostgreSQL.
