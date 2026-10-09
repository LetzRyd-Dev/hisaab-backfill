# VARIABLE 2: CONTRACTED LEASE RENT ENGINE

**Module Directory:** `02_rent_engine/`  
**Target Persistence Table:** `public.daily_rent_log`  
**Upstream Input Tables:**
- `public.core_daily_vehicle_status` (Custody & Attendance from Variable 1)
- `public.rental_custom_partner_plans` (Partner-specific customized daily rates, 343 overrides)
- `public.rental_model_baselines` (Vehicle model and city baseline rates, 31 models)
- `public.core_rental_plans` (Master enterprise rental plans, 20 plans)
- `public.rental_rate_slabs` (Trip-tiered rental slabs, 91 slabs)
- `public.rental_fee_rules` (City and contract indemnity rules)

---

## 1. OBJECTIVE & ARCHITECTURAL ROLE
The Contracted Lease Rent Engine is **Variable 2** in the Hisaab pipeline. 

Once Variable 1 confirms vehicle custody and attendance on each calendar date, Variable 2 evaluates:
1. **Is the vehicle billable on this date?** (`billable_rent_day = TRUE / FALSE`).
2. **What contracted rate card applies?** (Partner custom agreement $\to$ Model baseline $\to$ Plan slab $\to$ Standard fallback).
3. **What statutory/insurance indemnity fee applies?** (BLR/MUM: ₹30/day; HYD: ₹0/day).
4. **Did an off-road car generate active revenue?** (Anti-leakage *Trip Override*).

The output table `public.daily_rent_log` is the root anchor joined by the weekly Hisaab engine (`sp_sync_hisaab_vehicle_weekly`).

---

## 2. RATE CARD SELECTION WATERFALL

For every vehicle record in `core_daily_vehicle_status` for `p_target_date`:

```
+-----------------------------------------------------------------------------+
| Check: attendance_status IN ('Active', 'Allocation', 'Same Day D&A')?      |
+-----------------------------------------------------------------------------+
      │                                                     │
     YES                                                    NO (RFD, Maintenance, Drop Off)
      ▼                                                     ▼
Determine Contracted Rate:                             Non-Billable Day:
1. rental_custom_partner_plans                         - billable_rent_day = FALSE
   (Match partner_id + vehicle_number)                 - daily_rent_applied = 0.00
2. rental_model_baselines                              - daily_indemnity_fee = 0.00
   (Match car_model + city)                            - net_daily_rent = 0.00
3. core_rental_plans                                        │
   (Plan assigned on onboarding)                            ▼
4. Standard Baseline Fallback:                         Trip Override Check:
   - BLR / HYD: ₹856.00/day                            If platform trips > 0 or earnings > 0,
   - MUM: ₹999.00 / ₹1,029.00/day                      force billable_rent_day = TRUE and
                                                       bill standard rent (anti-leakage).
```

---

## 3. INDEMNITY FEE POLICY MATRIX

| City | Standard Daily Fee | Special Custom Agreement | Billing Condition |
| :--- | :---: | :---: | :--- |
| **Bengaluru (BLR)** | **₹30.00/day** | ₹15.00 or ₹20.00/day | Only on billable on-road days (`billable_rent_day = TRUE`) |
| **Mumbai (MUM)** | **₹30.00/day** | ₹0.00 (exempt packages) | Only on billable on-road days (`billable_rent_day = TRUE`) |
| **Hyderabad (HYD)** | **₹0.00/day** | ₹0.00 (standard policy) | Indemnity fee is ₹0.00 across standard HYD contracts |

---

## 4. MATHEMATICAL FORMULATION

$$\text{Net Daily Rent} = \begin{cases}
0.00, & \text{if } \text{billable\_rent\_day} = \text{FALSE} \\
\text{daily\_rent\_applied} + \text{daily\_indemnity\_fee}, & \text{if } \text{billable\_rent\_day} = \text{TRUE}
\end{cases}$$

$$\text{Net Weekly Lease Rental} = \sum_{d=1}^{7} \text{Net Daily Rent}$$

---

## 5. SCRIPTS IN THIS MODULE

1. **`audit_rental_plans.py` (100% Read-Only):**
   - Scans all custom partner plans and model baselines in PostgreSQL.
   - Identifies partners or models that would fall through to the baseline default (₹856/day).
2. **`backfill_rent.py` (Execution Runner):**
   - Enforces `HisaabSafetyGuard` to prevent touching protected weeks.
   - Executes `sp_calculate_daily_rent(start_date, end_date)`.
   - Runs in DRY-RUN mode by default (automatic `ROLLBACK`), requiring `--commit` to persist.
3. **`verify_rent_output.py` (Quality Gate Verifier):**
   - Asserts that 100% of billable days have positive rent.
   - Asserts that all non-billable days have zero rent.
   - Verifies average daily rent falls within expected commercial bands (₹800 to ₹1,050/day).
