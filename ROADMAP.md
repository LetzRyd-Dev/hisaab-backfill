# LETZRYD HISAAB BACKFILL: ONE-VARIABLE-AT-A-TIME ROADMAP

**Repository:** `https://github.com/aayush-letzryd/hisaab-backfill`  
**Objective:** Backfill historical Hisaab settlement cycles across PostgreSQL using a modular, verifiable, "one-variable-at-a-time" methodology.  
**Guiding Principle:** **The Core of Hisaab is Custody + Attendance + Contracted Rent.** Missing third-party platform feeds (Uber, Ola, GPS) safely evaluate to `0.00` and are layered in incrementally without blocking baseline financial reconciliation.

---

## 1. THE 8-VARIABLE PROGRESSION MATRIX

```
[VARIABLE 1] Vehicle Custody (Who held which car on which date?)
     │
     ▼
[VARIABLE 2] Attendance & Contracted Lease Rent (On-road days, plans, slabs, baseline rent)
     │
     ▼
[VARIABLE 3] Approved Adjustments (Debits, credits, damages from core_adjustments)
     │
     ▼
[VARIABLE 4] Traffic Challans (Unpaid violation notices matched to custody on violation date)
     │
     ▼
[VARIABLE 5] Platform Fares & Cash Collected (Uber, Ola, Rapido earnings & passenger cash)
     │
     ▼
[VARIABLE 6] GPS Telematics & Dead Mile Penalty (Actual vs ideal km for BLR individual drivers)
     │
     ▼
[VARIABLE 7] Statutory TDS (Section 194C: 1% margin withholding)
     │
     ▼
[VARIABLE 8] Partner Consolidated Statement & Parity Audit (Zero-variance sign-off & week lock)
```

---

## 2. DETAILED VARIABLE-BY-VARIABLE SPECIFICATION

### Variable 1: Vehicle Custody & Attendance (The Ground Truth)
* **Question Answered:** Which driver/operator held which vehicle on each calendar day?
* **Upstream Data Sources:**
  - `core_vehicle_allocation` (8,065 rows, Jan 2024 to present)
  - `core_dropoffs` (7,790 rows, Nov 2024 to present)
  - `core_vehicle_onboarding` (Master vehicle catalog)
  - `sheet_vehicle_status` (Aug 2026 onwards, used when present)
* **Target Persistence Table:** `public.core_daily_vehicle_status`
* **Algorithm / Procedure:** `sp_refresh_vehicle_status_range(start_date, end_date)` calling `sp_generate_daily_vehicle_status`.
  - When ground sheets are missing, it triggers `PORTAL_FALLBACK`: matches `allocation_date <= target_date` and `return_date >= allocation_date`.
  - Classifies status: `Active` (in custody), `RFD` (yard/unassigned), `Maintenance`, or `Same Day D&A`.
* **Verification Gate:**
  - Every active car has a valid `partner_id` and `city`.
  - No active status on or after a confirmed `dropoff_date`.

---

### Variable 2: Daily Contracted Lease Rent & Indemnity
* **Question Answered:** What is the daily lease rent and indemnity fee charged for each active day?
* **Upstream Data Sources:**
  - `core_daily_vehicle_status` (from Variable 1)
  - `rental_custom_partner_plans` (343 partner overrides)
  - `rental_model_baselines` (31 vehicle models)
  - `core_rental_plans` (20 master plans)
  - Standard baseline fallback: ₹856.00/day (BLR/HYD) or ₹999.00/day (MUM).
* **Target Persistence Table:** `public.daily_rent_log`
* **Algorithm / Procedure:** `sp_calculate_daily_rent(start_date, end_date)`.
  - Billed days: `Active` $\to$ `billable_rent_day = TRUE`.
  - Non-billable days (`RFD`, `Maintenance`, `Drop Off`) $\to$ `daily_rent = 0.00`, `daily_indemnity_fee = 0.00`.
  - Trip override check: If off-road car logged platform trips, day is forced to billable.
* **Verification Gate:**
  - 100% of billable days have non-zero rent.
  - Zero rent applied to non-billable days.

---

### Variable 3: Approved Adjustments & Deductions
* **Question Answered:** What ad-hoc debits or credits apply to this cycle?
* **Upstream Data Sources:**
  - `core_adjustments` (15,843 approved rows, Dec 2025 to present)
* **Target Persistence Table:** `public.hisaab_adjustments_ledger`
* **Algorithm / Procedure:**
  - Waterfall matching: `partner_id` $\to$ phone number $\to$ vehicle custody on `incident_date`.
  - Sign polarity: `CREDIT` (reduces driver debt) vs `DEBIT` (adds to driver debt).
  - Mutually exclusive partition: Accidents partitioned into `accident_deduction`, regular penalties into `daily_adjustments`.
* **Verification Gate:**
  - Only `approval_status = 'Approved'` records are recognized.
  - Zero double-counting between accident damages and regular adjustments.

---

### Variable 4: Traffic Challans & Fines
* **Question Answered:** What unpaid traffic violations were incurred during driver custody?
* **Upstream Data Sources:**
  - `core_challans` (10,025 notice records, Jan 2026 to present)
* **Target Field:** `hisaab_vehicle_weekly.challan_amount`
* **Algorithm / Procedure:**
  - Matches `vehicle_number` and `violation_date` against driver custody window.
  - Strictly filters for `payment_status IN ('UNPAID', 'PARTIALLY_PAID')`. Paid fines are excluded.
* **Verification Gate:**
  - No fine attributed to a driver if violation occurred outside their custody window.

---

### Variable 5: Platform Fare Earnings & Cash Collected
* **Question Answered:** How much did the vehicle earn digitally, and how much passenger cash was pocketed?
* **Upstream Data Sources:**
  - Uber: `core_uber_daily`, `core_uber_weekly`, `uber_pipeline_trips`, `z_uber_raw_1`, `raw_uber_data`.
  - Ola: `core_ola_daily`, `core_ola_weekly`, `ola_raw_crns`, `raw_ola_data`.
  - Rapido: `core_rapido_daily` (mandated ₹0.00).
* **Target Tables:** `public.hisaab_daily_ledger` & `public.hisaab_vehicle_weekly`
* **Algorithm / Procedure:**
  - Fares collected by LetzRyd credit against and pay down lease rent.
  - Passenger cash collected increases driver debt to LetzRyd.
  - **Defensive Fallback:** If third-party feed is missing for a historical week, earnings and cash evaluate to `0.00` without halting settlement.
* **Verification Gate:**
  - Gross earnings $\ge$ cash collected + net bank deposit.

---

### Variable 6: GPS Telematics & Dead Mile Penalties
* **Question Answered:** Did individual Bangalore drivers incur unauthorized personal/dead mileage?
* **Upstream Data Sources:**
  - `core_gps`, `sheet_gps_telematics`.
* **Target Field:** `hisaab_vehicle_weekly.dead_mile_charges`
* **Algorithm / Procedure:**
  - Ideal GPS KM = $(\text{In-Trip KM} \times 1.05) + (\text{Trips} \times 3.0\text{ km}) + (\text{On-road Days} \times 30.0\text{ km})$.
  - Dead Mile KM = $\max(0, \text{Total GPS KM} - \text{Ideal GPS KM})$.
  - Penalty: ₹3.00/km (Bangalore individual drivers only; ₹0.00 for Operators, Hyderabad & Mumbai).
  - If GPS data is absent, penalty defaults to ₹0.00.
* **Verification Gate:**
  - Operator vehicles and non-BLR fleets strictly evaluate to ₹0.00.

---

### Variable 7: Statutory TDS Deduction (Section 194C)
* **Question Answered:** What statutory tax withholding applies?
* **Target Field:** `hisaab_vehicle_weekly.tds_amount`
* **Algorithm / Procedure:**
  - $\text{Taxable Margin} = \max(0, \text{Gross Revenue} - \text{Net Weekly Lease Rental})$.
  - TDS = $\text{ROUND}(\text{Taxable Margin} \times 1\%, 2)$ for individual drivers; ₹0.00 for Operators.
* **Verification Gate:**
  - TDS applies only when gross revenue exceeds weekly rent.

---

### Variable 8: Multi-Grain Partner Rollup & Parity Audit
* **Question Answered:** What is the net payout or collection amount for each operator across their entire fleet?
* **Target Persistence Table:** `public.hisaab_partner_weekly`
* **Algorithm / Procedure:**
  - Aggregates multi-car operators (1 to 200+ vehicles).
  - Enforces **Standalone Mode** (`enable_roll_forward = 'false'`, `previous_outstanding = 0.00`) for all historical backfills to prevent phantom duplicate payouts.
* **Verification Gate:**
  - **Zero-Variance Parity:** $\sum \text{Vehicle Current O/S} \equiv \sum \text{Partner Current O/S}$ ($\text{diff} = 0.00$).
  - Lock week via `sp_check_and_enforce_monday_lock`.

---

## 3. PHASED ROLLOUT SCHEDULE

| Phase | Target Cycles | Focus & Deliverable | Upstream Data Status |
| :---: | :--- | :--- | :--- |
| **Phase 1** | **CY26WK35** | **Zero-Risk Pilot Run:** Data already 100% complete in DB; execute master procedure and verify output against scorecard. | 100% Ready (Rent, Uber, Ola, GPS, Adjustments present). |
| **Phase 2** | **CY26WK28** | **First Gap Week Proof:** Run Variable 1 (Custody) $\to$ Variable 2 (Rent) $\to$ layer in `raw_uber_data` & `raw_ola_data`. | Custody intact; raw staging feeds available. |
| **Phase 3** | **CY26WK29 to CY26WK34** | **Bridge the Gap:** Complete all missing weeks between Pilot (W27) and Production (W36). | Custody intact; backfill custody + rent + adjustments. |
| **Phase 4** | **CY26WK01 to CY26WK25** | **Historical Archive:** Backfill Q1 & Q2 2026 using allocation logs and legacy assignment records. | Custody intact back to Jan 2024. |
