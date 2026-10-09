# VARIABLES 5 & 6: PLATFORM FARES & GPS TELEMATICS ENGINE

**Module Directory:** `04_platform_feeds/`  
**Target Persistence Tables:**
- `public.hisaab_daily_ledger` (Daily platform revenues, trips, cash, and dead miles)
- `public.hisaab_vehicle_weekly` (Weekly aggregation of Uber, Ola, Rapido, and GPS penalty)
**Upstream Input Tables:**
- **Uber:** `core_uber_daily`, `core_uber_weekly`, `uber_pipeline_trips`, `raw_uber_data`, `z_uber_raw_1`, `z_uber_raw_15`
- **Ola:** `core_ola_daily`, `core_ola_weekly`, `ola_raw_crns`, `raw_ola_data`, `z_ola_raw_15`
- **Rapido:** `core_rapido_daily` (mandated ₹0.00 revenue; trips/km feed GPS buffer)
- **GPS Telematics:** `core_gps`, `sheet_gps_telematics`

---

## 1. OBJECTIVE & ARCHITECTURAL ROLE
Variables 5 & 6 incorporate operational ride-hailing revenues and vehicle telemetry:
- **Variable 5 (Platform Fares & Cash):** Captures digital fare earnings collected by LetzRyd to offset vehicle lease rent, and passenger cash pocketed by the driver (which increases driver dues).
- **Variable 6 (GPS Telematics):** Reconciles total vehicle odometer movement against authorized trip kilometers to penalize excessive personal/unauthorized usage.

---

## 2. VARIABLE 5: FINANCIAL FORMULATIONS & SIGN POLARITIES

### A. Uber Week Outstanding
$$\text{Uber Week O/S} = \text{Uber Earnings} + \text{Incentives} + \text{Tolls} - \text{Uber Cash Collected} - \text{Subscription Charges}$$
- **Earnings/Tolls/Incentives ($-$ to balance):** Credits towards and pays down driver lease rent.
- **Passenger Cash ($+$ to balance):** Pocketed directly by driver; increases driver dues to LetzRyd.

### B. Ola Week Outstanding
$$\text{Ola Week O/S} = \text{Ola Net Revenue} + \text{Incentives} + \text{Tolls} - \text{Ola Cash Collected}$$
- Direct online bank deposits by drivers into Ola credit via `ola_online_payment`.

### C. Rapido Revenue Policy
- Mandated as **₹0.00 revenue** across weekly settlement outputs. Completed trips and trip km are incorporated into ideal GPS buffers.

### D. Defensive Zero-Fill Fallback
If third-party feeds are missing or partially ingested for any historical cycle:
- Earnings, cash, tolls, and trips **default cleanly to `0.00`**.
- The core financial settlement (Custody + Rent + Adjustments + Challans) completes with **zero null pointer exceptions**.

---

## 3. VARIABLE 6: GPS DEAD MILE PENALTY FORMULATION

$$\text{In-Trip KM} = \text{Uber KM} + \text{Ola KM} + \text{Rapido KM}$$
$$\text{Ideal GPS KM} = (\text{In-Trip KM} \times 1.05) + [(\text{Uber Trips} + \text{Ola Trips} + \text{Rapido Trips}) \times 3.0\text{ km}] + (\text{Onroad Days} \times 30.0\text{ km})$$
$$\text{Dead Mile KM} = \max(0, \text{Total GPS KM} - \text{Ideal GPS KM})$$
$$\text{Dead Mile Penalty} = \begin{cases}
\text{Dead Mile KM} \times ₹3.00, & \text{for Individual drivers in Bengaluru (BLR)} \\
₹0.00, & \text{for Fleet Operators (multi-car) and all vehicles in HYD & MUM}
\end{cases}$$

- **Defensive Fallback:** If GPS telemetry is missing for a week, `total_gps_km = 0.0` and penalty defaults to **₹0.00**.

---

## 4. SCRIPTS IN THIS MODULE

1. **`audit_platform_feeds.py` (100% Read-Only):**
   - Scans PostgreSQL for Uber, Ola, Rapido, and GPS coverage for any historical week.
   - Evaluates row counts, earnings, cash collected, and checks for available staging tables (`raw_uber_data`, `z_uber_raw_1`, etc.).
2. **`ingest_platform_feeds.py` (Execution Runner):**
   - Maps available aggregator data into core feeds or prepares zero-default summaries.
   - Enforces `HisaabSafetyGuard` (aborts if protected week, dry-run by default).
3. **`verify_platform_feeds.py` (Quality Gate Verifier):**
   - Asserts mathematical consistency (cash collected $\le$ gross earnings).
   - Asserts that all missing platform values evaluate to `0.00` with zero `NULL` entries.
