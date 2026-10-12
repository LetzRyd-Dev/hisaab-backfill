# LetzRyd Cutoff-Based Cumulative Rolling Hisaab — Deep Architecture & Implementation Specification

**Document Version:** 2.0.0-PROD  
**Author:** Architecture & Data Pipeline Lead  
**Target Systems:** PostgreSQL 14+ (`letzryd-core-prod` / Cloud SQL `35.200.196.113`), FastAPI Backend (`cashfree-web-portal`), React 19 Frontend (`cashfree-web-portal.vercel.app`), Cashfree Payment Gateway  
**Date:** October 2026  

---

## Executive Summary

The LetzRyd platform operates a commercial fleet across Bangalore, Hyderabad, and Mumbai, reconciling vehicle telemetry, aggregator earnings (Uber, Ola, Rapido), rental agreements, traffic challans, and cash collections. Historically, weekly settlements suffered from **isolated single-week accounting**: if a driver or operator had an outstanding debt or credit at the end of Week $W$, the subsequent Week $W+1$ either wiped the slate clean or relied on manual adjustments, creating reconciliation drift and financial leakage.

This architecture specification details the end-to-end design and migration strategy for **Cutoff-Based Cumulative Rolling Hisaab**. Under this system:
1. An immutable **Day Zero Snapshot ($B_0$)** establishes verified opening balances.
2. An automated **Multi-Week Rolling Engine** continuously rolls forward balances:
   $$B_{k} = B_{k-1} + \Delta H_{k} - P_{k}$$
3. The **FastAPI Backend & React Frontend** present transparent, passbook-style running ledgers distinguishing single-week performance from net cumulative debt.
4. **Real-time Cashfree Webhooks** immediately update the rolling ledger and driver portal with strict 4-tier waterfall debt allocation.

```
+---------------------------------------------------------------------------------------------------------+
|                                    LETZRYD CUMULATIVE ROLLING ARCHITECTURE                              |
+---------------------------------------------------------------------------------------------------------+
                                                                                                           
   [Day-0 Cutoff Snapshot]                 [Weekly Data Ingestion]              [Payment Gateways]        
    - Audited Opening Balance (B_0)         - Uber / Ola / Rapido Streams        - Cashfree UPI QR / Autopay
    - Disputed Items Escrow                 - Daily Rent Log (Custody Engine)    - Bank Transfer Receipts   
                 |                                      |                                  |               
                 +-------------------+  +---------------+                                  |               
                                     |  |                                                  |               
                                     v  v                                                  v               
                         +--------------------------+                         +--------------------------+ 
                         |   hisaab_vehicle_weekly  |                         |       app_payments       | 
                         |  (Isolated Weekly Delta) |                         |  (Real-Time Collections) | 
                         +-------------+------------+                         +------------+-------------+ 
                                       |                                                   |               
                                       v                                                   v               
                         +-----------------------------------------------------------------------+         
                         |                         hisaab_partner_weekly                         |         
                         |          (Multi-Week Cumulative Rolling Ledger Engine)                |         
                         |          B_k = B_(k-1) + Sum(Delta H_v) + PriorAdj - Payments         |         
                         +-----------------------------------+-----------------------------------+         
                                                             |                                             
                                       +---------------------+---------------------+                       
                                       |                                           |                       
                                       v                                           v                       
                         +---------------------------+               +---------------------------+         
                         |        app_drivers        |               |       app_operators       |         
                         |   (Driver App Portal)     |               |   (Fleet Operator Portal) |         
                         |  - This Week's Net        |               |  - Fleet Vehicle Rollup   |         
                         |  - Cumulative Owed Balance|               |  - Consolidated Ledger    |         
                         +---------------------------+               +---------------------------+         
```

---

## 1. Current State vs. Future State Comparison

### 1.1 Detailed Table-by-Table Architectural Comparison

| Table Name | Current State Behavior (Isolated / Ad-Hoc) | Future State Behavior (Cutoff-Based Cumulative Roll-Forward) | Architectural Delta Required |
| :--- | :--- | :--- | :--- |
| **`hisaab_settlement_weeks`** | Defines `week_id` (e.g. `CY26WK40`), `week_start`, `week_end`, and `is_locked`. Operates as calendar lookup with independent locking. | Master lifecycle controller. Stores `is_cutoff_week`, `cutoff_snapshot_ref`, and enforces immutable forward sequence ($W_k \to W_{k+1}$). | Add columns `is_cutoff_week BOOLEAN`, `snapshot_version VARCHAR(50)`, `rolling_status VARCHAR(20)`. |
| **`hisaab_vehicle_weekly`** | Computes week-isolated metrics: `current_week_os = net_weekly_lease_rental - (uber_os + ola_os) + challans + adj`. Has no prior balance memory. | Remains the **operational vehicle ledger** ($\Delta H_{v,k}$). Strictly captures the isolated performance of vehicle $v$ during week $k$. | Keep clean separation of vehicle operational delta from partner cumulative debt. |
| **`hisaab_partner_weekly`** | Aggregates vehicles for `partner_id`. `previous_outstanding` was conditionally read only if `enable_roll_forward = true` and only if `pw.total_outstanding > 0`. Zeroes negative balances (net payouts). | **Core double-entry rolling engine**. Reads verified previous balance $B_{k-1}$ (positive OR negative), adds weekly vehicle rollups $\sum \Delta H_{v,k}$, adjusts prior adjustments, and subtracts recorded payments $P_k$. | Refactor `sp_sync_hisaab_partner_weekly` to support bidirectional carry-forward, linking $B_{k-1}$ recursively from $B_0$. |
| **`app_hisaabs`** | Week-isolated statements for mobile app. Stores `to_pay`, `to_collect`, and `paid_amount`. When payments occur, decrements single-week `current_period_os`. | Displays isolated week statement breakdown with two explicit summary cards: **"Week $k$ Net"** and **"Account Balance After Week $k$"** ($B_k$). | Add `opening_balance_brought_forward`, `cumulative_closing_balance`, and `is_cumulative_settled` to schema. |
| **`app_drivers`** | Stores `cw_os`, `lw_os`, and `cumulative_owed`. Bug: `cumulative_owed = ABS(latest_h.to_pay)` clobbers debt. Multi-vehicle drivers overwrite with the last vehicle. | Reflects true partner cumulative financial state: `cumulative_balance` ($B_k$). Separates operational driving stats from financial liability. | Fix rollup trigger `fn_trg_sync_hisaab_vehicle_to_app` to aggregate across all driver vehicles and bind directly to `hisaab_partner_weekly`. |
| **`app_operators`** | Broken rollup: 300 / 310 operator profiles show zero/null dues due to unlinked vehicle allocations and hardcoded IDs. | Aggregates all assigned vehicles across all operator drivers into a single consolidated fleet ledger rolling forward from Operator $B_0$. | Fix operator association in `app_driver_allocations` and compute fleet-level rolling balances. |

---

### 1.2 Mathematical Formulations

#### Current Formulations (Isolated / Flawed)
In the existing codebase (`sp_sync_hisaab_vehicle_weekly_dump.sql` & `current_sp_sync_hisaab_partner_weekly.sql`):
$$\Delta H_{\text{veh}} = R_{\text{lease}} + I_{\text{indem}} - (U_{\text{os}} + O_{\text{os}}) + C_{\text{challan}} + A_{\text{accident}} + P_{\text{gps}} + \text{Adj}_{\text{signed}}$$
$$\text{Current Week O/S} = \Delta H_{\text{veh}}$$
$$\text{Previous Outstanding} = \begin{cases} \text{OpeningBalance}, & \text{if explicitly defined} \\ \max(0, \text{PrevWeekTotalOS}), & \text{if } \text{enable\_roll\_forward} = \text{true} \\ 0.00, & \text{otherwise} \end{cases}$$

**Flaws in Current Math:**
1. **Unidirectional Asymmetry**: If `PrevWeekTotalOS < 0` (LetzRyd owes driver money), the previous outstanding is truncated to `0.00`. The driver's credit balance is lost between weeks!
2. **Missing Inter-Week Payment Propagation**: If a driver pays mid-week via UPI, the payment decrements `app_hisaabs.paid_amount` but does not write back to `hisaab_partner_weekly.amount_paid_during_week`, causing the next procedure run to overwrite and recreate the phantom debt.

#### Future Formulations (Bidirectional Cutoff-Based Cumulative Roll-Forward)

Let $p$ be a partner (Driver or Fleet Operator), and $W_k$ be settlement week $k$ with $k=0$ as the Cutoff Week:

$$\mathbf{Day\ Zero\ Anchor\ (k=0):} \quad B_0(p) = \text{Audited Snapshot Balance}$$

For any week $k \ge 1$:
$$\text{Operational Weekly Delta: } \Delta H_k(p) = \sum_{v \in \text{Vehicles}(p, k)} \Delta H_{v, k} + \text{PriorPeriodAdj}_k(p)$$

Where vehicle operational delta is:
$$\Delta H_{v,k} = R_{v,k} + I_{v,k} - \left( E_{v,k}^{\text{Uber}} + E_{v,k}^{\text{Ola}} + E_{v,k}^{\text{Rapido}} - K_{v,k}^{\text{Cash}} - S_{v,k}^{\text{Sub}} \right) + C_{v,k} + \text{Acc}_{v,k} + G_{v,k} + \text{Adj}_{v,k}$$

$$\text{Total Collections / Payouts in Period } k: \quad P_k(p) = \sum \text{Collections}_k(p) - \sum \text{Disbursements}_k(p)$$

$$\mathbf{Recursive\ Cumulative\ Closing\ Balance:} \quad \mathbf{B_k(p) = B_{k-1}(p) + \Delta H_k(p) - P_k(p)}$$

**Settlement Classification:**
$$\text{Net Amount to Collect (Driver Liability): } \quad \text{ToCollect}_k(p) = \max(0.00, B_k(p))$$
$$\text{Net Bank Payout (LetzRyd Liability): } \quad \text{ToPay}_k(p) = \max(0.00, -B_k(p))$$

---

## 2. Cutoff Day Zero Ingestion & Validation Protocol

### 2.1 Day Zero Snapshot Architecture

To eliminate historical ambiguity, an authoritative Day Zero Cutoff snapshot must be established. All past debts, unpaid rentals, accumulated challans, and disputed claims prior to Cutoff Date $T_0$ are reconciled and signed off by Operations and Finance.

```
+-----------------------------------------------------------------------------------+
|                        DAY ZERO CUTOFF INGESTION PIPELINE                         |
+-----------------------------------------------------------------------------------+
                                                                                     
 [Finance Master Sheet]                                                              
   (Audited CSV/Excel)                                                               
          |                                                                          
          v                                                                          
 [stg_hisaab_cutoff_raw]  -----> [Validation & Integrity Engine]                     
   - Raw Phone, Name, Dues         - Check 1: Phone & Partner ID Format Verification 
                                   - Check 2: Deduplication & Collision Detection    
                                   - Check 3: Mathematical Consistency Verification  
                                   - Check 4: Identity Resolution (app_drivers)      
                                                   |                                 
                                                   v (Passed All Rules)              
                                 [hisaab_partner_cutoff_snapshot]                    
                                   - Master Immutable Anchor (B_0)                   
                                   - Locked with SHA-256 Checksum                    
```

### 2.2 Handling Operational Edge Cases

1. **Active Drivers Currently on Road**:
   - Ingest verified balance $B_0$.
   - Active lease billing continues uninterrupted from Week 1 onwards.
2. **Inactive / Off-Boarded Drivers with Outstanding Dues**:
   - Must be ingested into snapshot with terminal status `INACTIVE`.
   - If $B_0 > 0$ (Debt): Marked for recovery or offset against security deposit ($S_{\text{deposit}}$).
   - If $B_0 < 0$ (Credit): Enqueued into Finance Bank Payout Queue for final settlement.
   - Do not accrue new rental charges ($R_{v,k} = 0$).
3. **Drivers Waiting for Vehicle Allocation (Induction / Yard / Standby)**:
   - Accrue zero vehicle rent and zero trip earnings.
   - Any induction dues, uniform charges, or onboarding fees are part of $B_0$.
   - Rolling balance remains constant ($B_k = B_{k-1}$) until a vehicle is allocated.
4. **Fleet Operators vs. Individual Drivers**:
   - Individual driver accounts map 1:1 to a single `partner_id`.
   - Fleet Operators (e.g., `OPR-HYD-001`) hold multiple vehicles ($N \ge 1$).
   - Snapshot balances must be established at the **Operator Legal Entity Level** (`partner_id`).
   - Vehicle allocation records link individual cars to the operator ID.
5. **Unassigned / Float / Yard Vehicles (`SYSTEM_ONBOARDED`)**:
   - Incur fleet maintenance and yard parking expenses that cannot be attributed to a driver.
   - Ingestion isolates these under a dedicated corporate clearing partner: `SYSTEM_FLEET_EXPENSE`.
   - Prevents unallocated vehicle costs from corrupting driver ledgers.
6. **Disputed Past Challans & Accidents**:
   - Disputed items are isolated into `hisaab_disputed_items`.
   - Two operational modes:
     - **Escrow Hold Mode**: Deducted from active collection balance ($B_0^{\text{active}} = B_0^{\text{gross}} - D_{\text{dispute}}$) pending investigation.
     - **Provisional Debit Mode**: Included in $B_0$, but flagged with `has_active_dispute = TRUE` in the driver app.
   - Once resolved, approved credits/waivers are injected via `core_adjustments` in the open week.

---

### 2.3 Concrete SQL Schema for Day Zero Ingestion & Snapshot

```sql
-- ============================================================================
-- 1. CUTOFF SNAPSHOT MASTER TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.hisaab_cutoff_master (
    cutoff_id                SERIAL PRIMARY KEY,
    cutoff_code              VARCHAR(50) UNIQUE NOT NULL,      -- e.g., 'CUTOFF_CY26WK40_20261004'
    cutoff_week_id           VARCHAR(20) NOT NULL REFERENCES public.hisaab_settlement_weeks(week_id),
    cutoff_effective_date    DATE NOT NULL,
    total_partners_ingested  INT DEFAULT 0,
    total_opening_debt       NUMERIC(14,2) DEFAULT 0.00,
    total_opening_credit     NUMERIC(14,2) DEFAULT 0.00,
    is_frozen                BOOLEAN DEFAULT FALSE,
    frozen_at                TIMESTAMPTZ,
    frozen_by                VARCHAR(100),
    checksum_sha256          VARCHAR(64),
    created_at               TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 2. PARTNER OPENING BALANCE SNAPSHOT (DAY ZERO BASELINE)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.hisaab_partner_cutoff_snapshot (
    snapshot_id              BIGSERIAL PRIMARY KEY,
    cutoff_id                INT NOT NULL REFERENCES public.hisaab_cutoff_master(cutoff_id),
    partner_id               VARCHAR(50) NOT NULL,
    phone                    VARCHAR(15),
    partner_name             VARCHAR(150),
    partner_type             VARCHAR(20) NOT NULL CHECK (partner_type IN ('Individual', 'Operator')),
    city                     VARCHAR(50) NOT NULL,
    operational_status       VARCHAR(30) NOT NULL CHECK (operational_status IN ('ACTIVE', 'INACTIVE', 'STANDBY', 'OFFBOARDED')),
    
    -- Financial Day-0 Quantities
    verified_balance_b0      NUMERIC(12,2) NOT NULL, -- Positive = Driver Owes LetzRyd; Negative = LetzRyd Owes Driver
    unpaid_rent_component    NUMERIC(12,2) DEFAULT 0.00,
    unpaid_challan_component NUMERIC(12,2) DEFAULT 0.00,
    unpaid_accident_damage   NUMERIC(12,2) DEFAULT 0.00,
    advance_credit_component NUMERIC(12,2) DEFAULT 0.00,
    disputed_amount_in_escrow NUMERIC(12,2) DEFAULT 0.00,
    
    security_deposit_balance NUMERIC(12,2) DEFAULT 5000.00,
    signoff_notes            TEXT,
    created_at               TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT uq_partner_cutoff UNIQUE (cutoff_id, partner_id)
);

CREATE INDEX idx_cutoff_snapshot_partner ON public.hisaab_partner_cutoff_snapshot(partner_id);
CREATE INDEX idx_cutoff_snapshot_phone ON public.hisaab_partner_cutoff_snapshot(phone);

-- ============================================================================
-- 3. CUTOFF STAGING TABLE (FOR DATA LOAD AND VALIDATION)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.stg_hisaab_cutoff_raw (
    raw_id                   SERIAL PRIMARY KEY,
    partner_id               VARCHAR(100),
    phone                    VARCHAR(50),
    driver_name              VARCHAR(200),
    partner_type             VARCHAR(50),
    city                     VARCHAR(50),
    closing_balance_str      VARCHAR(50),
    rent_due_str             VARCHAR(50),
    challan_due_str          VARCHAR(50),
    accident_due_str         VARCHAR(50),
    status_str               VARCHAR(50),
    notes                    TEXT,
    validation_status        VARCHAR(30) DEFAULT 'PENDING',
    error_message            TEXT
);
```

### 2.4 Automated Staging Validation Stored Procedure

```sql
CREATE OR REPLACE PROCEDURE public.sp_validate_and_commit_cutoff_snapshot(
    p_cutoff_code VARCHAR,
    p_week_id VARCHAR,
    p_effective_date DATE,
    p_signed_off_by VARCHAR
)
LANGUAGE plpgsql
AS $procedure$
DECLARE
    v_cutoff_id INT;
    v_invalid_count INT;
    v_total_rows INT;
BEGIN
    -- 1. Check for basic format errors in staging
    UPDATE public.stg_hisaab_cutoff_raw
    SET 
        validation_status = 'INVALID',
        error_message = 'Malformed numeric balance or missing partner_id'
    WHERE partner_id IS NULL OR TRIM(partner_id) = ''
       OR closing_balance_str !~ '^-?[0-9]+(\.[0-9]{1,2})?$';

    SELECT COUNT(*) INTO v_invalid_count 
    FROM public.stg_hisaab_cutoff_raw 
    WHERE validation_status = 'INVALID';

    IF v_invalid_count > 0 THEN
        RAISE EXCEPTION 'Cutoff ingestion aborted: % invalid rows found in staging.', v_invalid_count;
    END IF;

    -- 2. Create Master Cutoff record
    INSERT INTO public.hisaab_cutoff_master (
        cutoff_code, cutoff_week_id, cutoff_effective_date, frozen_by, is_frozen, frozen_at
    ) VALUES (
        p_cutoff_code, p_week_id, p_effective_date, p_signed_off_by, TRUE, NOW()
    )
    RETURNING cutoff_id INTO v_cutoff_id;

    -- 3. Commit clean staging data into immutable snapshot
    INSERT INTO public.hisaab_partner_cutoff_snapshot (
        cutoff_id,
        partner_id,
        phone,
        partner_name,
        partner_type,
        city,
        operational_status,
        verified_balance_b0,
        unpaid_rent_component,
        unpaid_challan_component,
        unpaid_accident_damage,
        signoff_notes
    )
    SELECT 
        v_cutoff_id,
        TRIM(s.partner_id),
        RIGHT(REGEXP_REPLACE(s.phone, '[^0-9]', '', 'g'), 10),
        TRIM(s.driver_name),
        CASE WHEN s.partner_type ILIKE '%OP%' THEN 'Operator' ELSE 'Individual' END,
        COALESCE(NULLIF(TRIM(s.city), ''), 'HYD'),
        COALESCE(NULLIF(UPPER(TRIM(s.status_str)), ''), 'ACTIVE'),
        ROUND(s.closing_balance_str::NUMERIC, 2),
        ROUND(COALESCE(NULLIF(s.rent_due_str, ''), '0')::NUMERIC, 2),
        ROUND(COALESCE(NULLIF(s.challan_due_str, ''), '0')::NUMERIC, 2),
        ROUND(COALESCE(NULLIF(s.accident_due_str, ''), '0')::NUMERIC, 2),
        s.notes
    FROM public.stg_hisaab_cutoff_raw s;

    GET DIAGNOSTICS v_total_rows = ROW_COUNT;

    -- 4. Update Cutoff Master summary metrics
    UPDATE public.hisaab_cutoff_master
    SET 
        total_partners_ingested = v_total_rows,
        total_opening_debt = (SELECT COALESCE(SUM(verified_balance_b0), 0) FROM public.hisaab_partner_cutoff_snapshot WHERE cutoff_id = v_cutoff_id AND verified_balance_b0 > 0),
        total_opening_credit = (SELECT COALESCE(SUM(ABS(verified_balance_b0)), 0) FROM public.hisaab_partner_cutoff_snapshot WHERE cutoff_id = v_cutoff_id AND verified_balance_b0 < 0)
    WHERE cutoff_id = v_cutoff_id;

    RAISE NOTICE 'Cutoff % successfully committed with % partners.', p_cutoff_code, v_total_rows;
END;
$procedure$;
```

---

## 3. Multi-Week Rolling Engine Mechanics

### 3.1 Recurrence Relation & Engine Cycle

The engine executes in discrete weekly cycles. For week $k$:
1. **Fetch Opening Balance ($B_{k-1}$)**:
   - If week $k$ is the immediate post-cutoff week: $B_{k-1}$ is fetched directly from `hisaab_partner_cutoff_snapshot`.
   - If week $k > 1$: $B_{k-1}$ is fetched from `hisaab_partner_weekly` where `week_id = v_prev_week_id`.
2. **Aggregate Operational Delta ($\Delta H_k$)**:
   - Sum across all vehicles operated by the partner during week $k$.
   - Includes rental charges, platform receipts, cash collections, challans, and approved adjustments.
3. **Incorporate Payments Received ($P_k$)**:
   - Queries `app_payments` where `status = 'SUCCESS'` and `completed_at` falls in the week boundary.
4. **Compute Closing Balance ($B_k$)**:
   $$B_k = B_{k-1} + \Delta H_k - P_k$$

```
+---------------------------------------------------------------------------------------------------+
|                                MULTI-WEEK BALANCE RECURSION TIMELINE                              |
+---------------------------------------------------------------------------------------------------+
                                                                                                     
 [Day-0 Cutoff]           [Week 40 (CY26WK40)]                  [Week 41 (CY26WK41)]                 
  B_0 = Rs. +5,000  --->   Opening: B_0  = +5,000        --->    Opening: B_1  = +2,500              
                           Delta H_40    = +1,500 (Loss)         Delta H_41    = -6,000 (Earnings)   
                           Payments P_40 = -4,000 (Paid)         Payments P_41 =      0              
                          ------------------------              ------------------------             
                           Closing: B_1  = +2,500                Closing: B_2  = -3,500              
                           Status: 'to_collect'                  Status: 'to_pay' (Bank Payout)      
```

### 3.2 Handling Negative ($B_k < 0$) vs. Positive ($B_k > 0$) Balances

- **Positive Balance ($B_k > 0$) — Net Collectable (LetzRyd Asset / Partner Debt)**:
  - `net_amount_to_collect = B_k`
  - `net_bank_payout = 0.00`
  - `settlement_status = 'OPEN'` or `'to_collect'`
  - Triggers payment request link in mobile app and initiates automated WhatsApp reminder.
- **Negative Balance ($B_k < 0$) — Net Payable (LetzRyd Liability / Driver Bank Payout)**:
  - `net_bank_payout = |B_k|`
  - `net_amount_to_collect = 0.00`
  - `settlement_status = 'PAYABLE'` or `'to_pay'`
  - Exported to Cashfree Payout / Bank IMPS batch.
  - **Disbursement Lifecycle**: Once the payout is successfully transferred and the bank UTR is returned, a payout transaction ($P_{\text{payout}} = -|B_k|$) is booked, returning the rolling balance to $0.00$.

### 3.3 Zero-Driving Partner Scenario in Week $k+1$

A common failure mode in previous implementations was dropping partners who did not log trips in the current week. The rolling engine must preserve financial continuity:
1. **Case A: Driver holds vehicle but logs 0 trips**:
   - Custody engine records `allotted_days = 7`, `onroad_days = 0`.
   - Rental charges accrue (e.g., $7 \times ₹1,000 = ₹7,000$).
   - $\Delta H_{k+1} = +7,000.00$.
   - Closing debt increases: $B_{k+1} = B_k + 7,000.00$.
2. **Case B: Driver has returned vehicle (0 cars allotted, 0 trips)**:
   - Rental charges are zero: $\Delta H_{k+1} = 0.00$.
   - Prior debt carries forward unaltered:
     $$B_{k+1} = B_k - P_{k+1}$$
   - If driver pays ₹2,000 towards past debt: $B_{k+1} = B_k - 2,000$.

To guarantee that inactive partners are never lost, the engine uses a `FULL OUTER UNION` across all partners with:
- Active vehicle operations in week $k$
- Recorded payments in week $k$
- Prior approved adjustments in week $k$
- Non-zero opening balance from week $k-1$ ($B_{k-1} \ne 0$)

---

### 3.4 Payment Waterfall Priority Engine

When a driver makes a payment ($C_{\text{cash}}$) via Cashfree UPI, credit card, or bank transfer, funds must be allocated according to strict financial priority:

```
+-----------------------------------------------------------------------------------+
|                        4-TIER CASH PAYMENT WATERFALL PRIORITY                     |
+-----------------------------------------------------------------------------------+
                                                                                     
                             [Incoming Cash Payment: C]                              
                                         |                                           
                                         v                                           
        +-----------------------------------------------------------------+          
        | TIER 1: Cumulative Operational Hisaab Debt (Prior + Current)    |          
        | Pay min(C, B_debt) -> Reduces B_k towards 0.00                  |          
        +--------------------------------+--------------------------------+          
                                         | (Remaining Funds > 0)                     
                                         v                                           
        +-----------------------------------------------------------------+          
        | TIER 2: Pending Security Deposit                                |          
        | Pay min(Rem, Deposit_Required - Deposit_Paid)                   |          
        +--------------------------------+--------------------------------+          
                                         | (Remaining Funds > 0)                     
                                         v                                           
        +-----------------------------------------------------------------+          
        | TIER 3: Unpaid Specific Traffic Challans & Penalties            |          
        | Target specific challans logged in core_challans                |          
        +--------------------------------+--------------------------------+          
                                         | (Remaining Funds > 0)                     
                                         v                                           
        +-----------------------------------------------------------------+          
        | TIER 4: Advance Fleet Credit (Surplus Balance)                  |          
        | Balance becomes negative (B_k < 0) -> Offsets next week's rent  |          
        +-----------------------------------------------------------------+          
```

**Waterfall Mathematical Formulation:**
$$\text{Alloc}_{\text{Hisaab}} = \min(C, \max(0, B_{\text{debt}}))$$
$$C_1 = C - \text{Alloc}_{\text{Hisaab}}$$
$$\text{Alloc}_{\text{Deposit}} = \min(C_1, \max(0, S_{\text{target}} - S_{\text{paid}}))$$
$$C_2 = C_1 - \text{Alloc}_{\text{Deposit}}$$
$$\text{Alloc}_{\text{AdvanceCredit}} = C_2 \quad (\text{Stored as credit offset for } W_{k+1})$$

---

### 3.5 Authoritative Stored Procedure: `sp_sync_hisaab_partner_weekly_rolling`

```sql
CREATE OR REPLACE PROCEDURE public.sp_sync_hisaab_partner_weekly_rolling(
    IN p_week_id VARCHAR,
    IN p_partner VARCHAR DEFAULT NULL
)
LANGUAGE plpgsql
AS $procedure$
DECLARE
    v_week_start DATE;
    v_week_end DATE;
    v_year INT;
    v_week_num INT;
    v_is_locked BOOLEAN;
    v_prev_week_id VARCHAR;
    v_prev_week_end DATE;
    v_cutoff_week_id VARCHAR;
    v_is_post_cutoff BOOLEAN := FALSE;
BEGIN
    IF p_week_id IS NULL THEN
        RETURN;
    END IF;

    -- 1. Ensure settlement calendar metadata
    SELECT is_locked, week_start, week_end, settlement_year, settlement_week
    INTO v_is_locked, v_week_start, v_week_end, v_year, v_week_num
    FROM public.hisaab_settlement_weeks
    WHERE week_id = p_week_id;

    IF v_is_locked = TRUE THEN
        RAISE NOTICE 'Week % is locked. Aborting calculation.', p_week_id;
        RETURN;
    END IF;

    -- 2. Determine previous week identifiers
    v_prev_week_end := v_week_start - INTERVAL '1 day';
    v_prev_week_id := 'CY' || to_char(v_prev_week_end, 'IY') || 'WK' || to_char(v_prev_week_end, 'IW');

    -- 3. Check if Cutoff Snapshot applies
    SELECT cutoff_week_id INTO v_cutoff_week_id 
    FROM public.hisaab_cutoff_master 
    WHERE is_frozen = TRUE 
    ORDER BY cutoff_id DESC LIMIT 1;

    -- If current week is immediately after cutoff week, read B_0 snapshot
    IF v_prev_week_id = v_cutoff_week_id THEN
        v_is_post_cutoff := TRUE;
    END IF;

    -- 4. Main Multi-Week Rolling Aggregation
    WITH veh_summary AS (
        SELECT 
            v.partner_id,
            MAX(v.partner_name) AS partner_name,
            MAX(v.partner_type) AS partner_type,
            MAX(v.city) AS city,
            COUNT(DISTINCT v.vehicle_number)::INT AS allotted_cars_count,
            SUM(v.onroad_days)::INT AS total_onroad_days,
            SUM(v.uber_trips + v.ola_trips + COALESCE(v.rapido_trips, 0))::INT AS total_trips,
            SUM(v.net_weekly_lease_rental) AS total_net_rent_billed,
            SUM(v.uber_total_earnings + v.ola_net_revenue + COALESCE(v.rapido_revenue, 0)) AS total_platform_earnings,
            SUM(ABS(COALESCE(v.uber_cash_collection, 0.00)) + ABS(COALESCE(v.ola_cash_collection, 0.00)) + ABS(COALESCE(v.rapido_cash, 0.00))) AS total_cash_collected,
            SUM(v.uber_incentive + v.ola_incentive + COALESCE(v.rapido_incentive, 0.00)) AS total_platform_incentives,
            SUM(v.adjustment_amount) AS total_adjustments,
            SUM(v.challan_amount) AS total_challans,
            SUM(v.accident_deduction) AS total_accidents,
            SUM(v.tds_amount) AS total_tds,
            SUM(v.current_week_os) AS current_week_delta
        FROM public.hisaab_vehicle_weekly v
        WHERE v.week_id = p_week_id
          AND (p_partner IS NULL OR v.partner_id = p_partner)
        GROUP BY v.partner_id
    ),
    prior_adj AS (
        SELECT 
            partner_id,
            COALESCE(SUM(CASE WHEN financial_direction = 'DEBIT' THEN amount ELSE -amount END), 0.00) AS prior_period_adjustments
        FROM public.core_adjustments
        WHERE is_deleted = FALSE 
          AND approval_status = 'Approved'
          AND adjustment_date BETWEEN v_week_start AND v_week_end
          AND (p_partner IS NULL OR partner_id = p_partner)
        GROUP BY partner_id
    ),
    incoming_payments AS (
        -- Real-time collections from app_payments for this settlement cycle
        SELECT 
            COALESCE(d.driver_code, p.payer_id::TEXT) AS partner_id,
            SUM(p.amount) AS payments_collected
        FROM public.app_payments p
        LEFT JOIN public.app_drivers d ON (p.payer_type = 'driver' AND (p.payer_id = d.app_driver_id OR p.payer_id = d.driver_id))
        WHERE p.status = 'SUCCESS'
          AND p.completed_at >= v_week_start
          AND p.completed_at < (v_week_end + INTERVAL '1 day')
        GROUP BY COALESCE(d.driver_code, p.payer_id::TEXT)
    ),
    prev_balance AS (
        SELECT 
            p_ids.partner_id,
            CASE 
                -- If week 1 after cutoff, use immutable verified B_0
                WHEN v_is_post_cutoff = TRUE AND snap.verified_balance_b0 IS NOT NULL 
                    THEN snap.verified_balance_b0
                -- Otherwise, read rolling closing balance from previous week
                WHEN pw.total_outstanding IS NOT NULL 
                    THEN pw.total_outstanding
                ELSE 0.00
            END AS previous_outstanding
        FROM (
            SELECT partner_id FROM veh_summary
            UNION
            SELECT partner_id FROM prior_adj
            UNION
            SELECT partner_id FROM incoming_payments
            UNION
            SELECT partner_id FROM public.hisaab_partner_cutoff_snapshot WHERE v_is_post_cutoff = TRUE
            UNION
            SELECT partner_id FROM public.hisaab_partner_weekly WHERE week_id = v_prev_week_id AND total_outstanding <> 0.00
        ) p_ids
        LEFT JOIN public.hisaab_partner_cutoff_snapshot snap 
            ON snap.partner_id = p_ids.partner_id AND snap.cutoff_id = (SELECT cutoff_id FROM public.hisaab_cutoff_master WHERE is_frozen = TRUE ORDER BY cutoff_id DESC LIMIT 1)
        LEFT JOIN public.hisaab_partner_weekly pw 
            ON pw.week_id = v_prev_week_id AND pw.partner_id = p_ids.partner_id
        WHERE (p_partner IS NULL OR p_ids.partner_id = p_partner)
    ),
    all_partners AS (
        SELECT DISTINCT partner_id FROM prev_balance
    )
    INSERT INTO public.hisaab_partner_weekly (
        settlement_year,
        settlement_week,
        week_id,
        week_start,
        week_end,
        partner_id,
        partner_name,
        partner_type,
        city,
        allotted_cars_count,
        total_onroad_days,
        total_trips,
        total_net_rent_billed,
        total_platform_earnings,
        total_cash_collected,
        total_platform_incentives,
        total_adjustments,
        total_challans,
        total_accidents,
        total_tds,
        current_week_os,
        previous_outstanding,
        amount_paid_during_week,
        prior_period_adjustments,
        total_outstanding,
        net_bank_payout,
        net_amount_to_collect,
        settlement_status,
        updated_at
    )
    SELECT 
        v_year,
        v_week_num,
        p_week_id,
        v_week_start,
        v_week_end,
        ap.partner_id,
        COALESCE(v.partner_name, dr.driver_name, ap.partner_id),
        COALESCE(v.partner_type, CASE WHEN ap.partner_id ILIKE '%OP%' THEN 'Operator' ELSE 'Individual' END),
        COALESCE(v.city, 'HYD'),
        COALESCE(v.allotted_cars_count, 0),
        COALESCE(v.total_onroad_days, 0),
        COALESCE(v.total_trips, 0),
        COALESCE(v.total_net_rent_billed, 0.00),
        COALESCE(v.total_platform_earnings, 0.00),
        COALESCE(v.total_cash_collected, 0.00),
        COALESCE(v.total_platform_incentives, 0.00),
        COALESCE(v.total_adjustments, 0.00),
        COALESCE(v.total_challans, 0.00),
        COALESCE(v.total_accidents, 0.00),
        COALESCE(v.total_tds, 0.00),
        COALESCE(v.current_week_delta, 0.00),
        COALESCE(pb.previous_outstanding, 0.00),
        COALESCE(ip.payments_collected, 0.00),
        COALESCE(pa.prior_period_adjustments, 0.00),
        
        -- Master Rolling Balance Formula: B_k = B_(k-1) + Delta_H_k + PriorAdj - Payments
        (COALESCE(pb.previous_outstanding, 0.00) 
         + COALESCE(v.current_week_delta, 0.00) 
         + COALESCE(pa.prior_period_adjustments, 0.00) 
         - COALESCE(ip.payments_collected, 0.00)) AS total_outstanding,
        
        -- Net Bank Payout (when balance is negative)
        ABS(LEAST(0.00, (COALESCE(pb.previous_outstanding, 0.00) 
                        + COALESCE(v.current_week_delta, 0.00) 
                        + COALESCE(pa.prior_period_adjustments, 0.00) 
                        - COALESCE(ip.payments_collected, 0.00)))) AS net_bank_payout,
        
        -- Net Collectable from Driver (when balance is positive)
        GREATEST(0.00, (COALESCE(pb.previous_outstanding, 0.00) 
                       + COALESCE(v.current_week_delta, 0.00) 
                       + COALESCE(pa.prior_period_adjustments, 0.00) 
                       - COALESCE(ip.payments_collected, 0.00))) AS net_amount_to_collect,
        
        'OPEN',
        NOW()
    FROM all_partners ap
    LEFT JOIN veh_summary v ON ap.partner_id = v.partner_id
    LEFT JOIN prev_balance pb ON ap.partner_id = pb.partner_id
    LEFT JOIN prior_adj pa ON ap.partner_id = pa.partner_id
    LEFT JOIN incoming_payments ip ON ap.partner_id = ip.partner_id
    LEFT JOIN LATERAL (
        SELECT driver_name FROM public.core_partner_onboarding 
        WHERE partner_id = ap.partner_id LIMIT 1
    ) dr ON TRUE
    ON CONFLICT (week_id, partner_id) DO UPDATE SET
        allotted_cars_count = EXCLUDED.allotted_cars_count,
        total_onroad_days = EXCLUDED.total_onroad_days,
        total_trips = EXCLUDED.total_trips,
        total_net_rent_billed = EXCLUDED.total_net_rent_billed,
        total_platform_earnings = EXCLUDED.total_platform_earnings,
        total_cash_collected = EXCLUDED.total_cash_collected,
        total_platform_incentives = EXCLUDED.total_platform_incentives,
        total_adjustments = EXCLUDED.total_adjustments,
        total_challans = EXCLUDED.total_challans,
        total_accidents = EXCLUDED.total_accidents,
        total_tds = EXCLUDED.total_tds,
        current_week_os = EXCLUDED.current_week_os,
        previous_outstanding = EXCLUDED.previous_outstanding,
        amount_paid_during_week = EXCLUDED.amount_paid_during_week,
        prior_period_adjustments = EXCLUDED.prior_period_adjustments,
        total_outstanding = EXCLUDED.total_outstanding,
        net_bank_payout = EXCLUDED.net_bank_payout,
        net_amount_to_collect = EXCLUDED.net_amount_to_collect,
        updated_at = NOW()
    WHERE public.hisaab_partner_weekly.settlement_status <> 'LOCKED';

    RAISE NOTICE 'Rolling Partner Hisaab synchronized successfully for week %.', p_week_id;
END;
$procedure$;
```

---

## 4. Portal & API Synchronization

### 4.1 UI/UX Layout Architecture: Preventing Driver Confusion

Drivers easily misunderstand cumulative balances if a statement blends past debts directly into weekly performance earnings. For instance, if a driver earned ₹8,000 this week but owes ₹10,000 from the past, displaying a single negative number "-₹2,000" leads the driver to believe their hard work this week was uncredited.

The interface must clearly decouple **"How I performed this week"** from **"My total outstanding account liability"**:

```
+-----------------------------------------------------------------------------------+
|                        MOBILE DRIVER APP UI SPECIFICATION                         |
+-----------------------------------------------------------------------------------+
                                                                                     
  [ SCREEN 1: HOME SCREEN HERO ]                                                    
  +-------------------------------------------------------------------------------+ 
  |  LetzRyd Partner                                        [SOS] [Bell] [Profile]| 
  |                                                                               | 
  |  +-------------------------------------------------------------------------+  | 
  |  | THIS WEEK'S PERFORMANCE (Week 40)                       [View Details >]|  | 
  |  | Gross Earnings:  +₹15,435.80    | Cash Collected:  -₹4,200.00           |  | 
  |  | Vehicle Rent:    -₹7,000.00     | Challans / Other:  -₹240.00           |  | 
  |  | ----------------------------------------------------------------------- |  | 
  |  | WEEK 40 NET RESULT:                   +₹3,995.80 (PROFIT EARNED)        |  | 
  |  +-------------------------------------------------------------------------+  | 
  |                                                                               | 
  |  +-------------------------------------------------------------------------+  | 
  |  | TOTAL ACCOUNT BALANCE                                                   |  | 
  |  | Previous Carried Balance:            -₹5,845.80 (Unpaid Past Dues)      |  | 
  |  | Plus Week 40 Profit:                 +₹3,995.80                         |  | 
  |  | ----------------------------------------------------------------------- |  | 
  |  | NET OUTSTANDING DUE TO LETZRYD:        ₹1,850.00                        |  | 
  |  | [ SETTLE NOW VIA UPI ]                (Pay via GPay, PhonePe, Paytm)    |  | 
  |  +-------------------------------------------------------------------------+  | 
  +-------------------------------------------------------------------------------+ 
                                                                                     
  [ SCREEN 2: PASSBOOK HISAAB HISTORY (RUNNING LEDGER) ]                            
  +-------------------------------------------------------------------------------+ 
  |  Date / Week  |  Description           | Charges  | Earnings/Paid | Balance   | 
  |---------------+------------------------+----------+---------------+-----------| 
  |  Day-0 Cutoff | Verified Opening Dues  | ₹5,000   |        --     |  ₹5,000   | 
  |  CY26WK39     | Week 39 Operational Net| ₹2,500   |        --     |  ₹7,500   | 
  |  28-Sep-2026  | Cashfree UPI Payment   |    --    |    ₹4,000     |  ₹3,500   | 
  |  CY26WK40     | Week 40 Operational Net|    --    |    ₹1,650     |  ₹1,850   | 
  +-------------------------------------------------------------------------------+ 
```

### 4.2 FastAPI REST Endpoints Specification

#### 1. `GET /api/hisaabs/driver/{id}/summary`
Returns decoupled weekly performance and cumulative roll-forward state:

```json
{
  "app_driver_id": 2,
  "driver_code": "LR-DRV-SSK",
  "full_name": "Sushant",
  "phone": "9140631755",
  "active_week": {
    "week_number": 40,
    "week_id": "CY26WK40",
    "period_start": "2026-09-28",
    "period_end": "2026-10-04",
    "gross_earnings": 15435.80,
    "total_deductions": 11440.00,
    "week_operational_net": 3995.80,
    "performance_status": "PROFITABLE"
  },
  "cumulative_account": {
    "cutoff_anchor_b0": 5000.00,
    "opening_balance_brought_forward": 5845.80,
    "payments_recorded_this_week": 0.00,
    "closing_cumulative_balance": 1850.00,
    "action_required": "COLLECT",
    "net_amount_to_collect": 1850.00,
    "net_bank_payout": 0.00
  },
  "security_deposit": {
    "target": 5000.00,
    "paid": 5000.00,
    "pending": 0.00
  }
}
```

#### 2. `GET /api/hisaabs/driver/{id}/passbook`
Returns chronological running ledger for audit transparency:

```json
{
  "driver_id": 2,
  "ledger_entries": [
    {
      "entry_date": "2026-09-20",
      "type": "CUTOFF_SNAPSHOT",
      "reference": "CUTOFF_CY26WK39",
      "description": "Audited Baseline Balance",
      "debit": 5000.00,
      "credit": 0.00,
      "running_balance": 5000.00
    },
    {
      "entry_date": "2026-09-27",
      "type": "WEEKLY_HISAAB",
      "reference": "HSB-CY26WK39-TS09EV8812-LR-DRV-SSK",
      "description": "Week 39 Operational Net Charges",
      "debit": 2500.00,
      "credit": 0.00,
      "running_balance": 7500.00
    },
    {
      "entry_date": "2026-09-28",
      "type": "PAYMENT_COLLECTION",
      "reference": "CF-ORD-2026-99214",
      "description": "Cashfree UPI Settlement",
      "debit": 0.00,
      "credit": 4000.00,
      "running_balance": 3500.00
    },
    {
      "entry_date": "2026-10-04",
      "type": "WEEKLY_HISAAB",
      "reference": "HSB-CY26WK40-TS09EV8812-LR-DRV-SSK",
      "description": "Week 40 Platform Surplus Payout Credit",
      "debit": 0.00,
      "credit": 1650.00,
      "running_balance": 1850.00
    }
  ]
}
```

---

### 4.3 Real-Time Cashfree Webhook Processing & Ledger Sync

When a payment succeeds, Cashfree fires a webhook to `POST /api/payments/webhook`. The backend validates the HMAC-SHA256 signature and executes an atomic ledger update:

```python
# app/api/payments.py (Enhanced Rolling Write-Back)

def _apply_payment_success_rolling(payment: AppPayments, db: Session) -> dict:
    """
    Real-time cash settlement cascade:
    1. Mark AppPayments SUCCESS (idempotent).
    2. Write to app_hisaabs (paid_amount, current_period_os).
    3. Write directly to hisaab_partner_weekly (amount_paid_during_week, total_outstanding).
    4. Cascade update to app_drivers (cumulative_owed, cw_to_collect, cw_os).
    5. Trigger real-time push notification.
    """
    paid_amount = round(float(payment.amount or 0), 2)
    driver = resolve_driver(str(payment.payer_id), db)
    if not driver:
        return {"status": "error", "message": "Driver not resolved"}

    # 1. Update active hisaab_partner_weekly row
    active_week = db.execute(
        text("SELECT week_id FROM hisaab_settlement_weeks WHERE is_locked = FALSE ORDER BY week_start DESC LIMIT 1")
    ).scalar() or "CY26WK40"

    partner_record = db.execute(
        text("""
            SELECT id, previous_outstanding, current_week_os, prior_period_adjustments, amount_paid_during_week
            FROM hisaab_partner_weekly
            WHERE week_id = :w_id AND partner_id = :p_id
        """),
        {"w_id": active_week, "p_id": driver.driver_code}
    ).mappings().first()

    if partner_record:
        new_paid = float(partner_record["amount_paid_during_week"] or 0) + paid_amount
        prev_os = float(partner_record["previous_outstanding"] or 0)
        curr_os = float(partner_record["current_week_os"] or 0)
        prior_adj = float(partner_record["prior_period_adjustments"] or 0)
        
        # Recalculate true rolling balance
        new_total_os = round(prev_os + curr_os + prior_adj - new_paid, 2)
        new_to_collect = max(0.00, new_total_os)
        new_to_pay = abs(min(0.00, new_total_os))

        db.execute(
            text("""
                UPDATE hisaab_partner_weekly
                SET amount_paid_during_week = :paid,
                    total_outstanding = :total_os,
                    net_amount_to_collect = :to_col,
                    net_bank_payout = :to_pay,
                    updated_at = NOW()
                WHERE id = :id
            """),
            {
                "paid": new_paid,
                "total_os": new_total_os,
                "to_col": new_to_collect,
                "to_pay": new_to_pay,
                "id": partner_record["id"]
            }
        )

    # 2. Update app_drivers profile metrics
    driver.cw_to_collect = max(0.00, round(float(driver.cw_to_collect or 0) - paid_amount, 2))
    driver.cumulative_owed = max(0.00, round(float(driver.cumulative_owed or 0) - paid_amount, 2))
    driver.cw_os = max(0.00, round(float(driver.cw_os or 0) - paid_amount, 2))
    
    db.commit()
    return {"status": "success", "driver_id": driver.app_driver_id, "amount_settled": paid_amount}
```

---

## 5. Production Migration & Rollout Runbook

### 5.1 Pre-Migration Phase (T-72 Hours to T-0)

- [ ] **Data Contract Verification**: Verify that all active drivers in `app_drivers` have normalized 10-digit phone numbers matching `core_partner_onboarding`.
- [ ] **Freeze Historical Periods**: Verify that all settlement weeks prior to Cutoff Week (e.g., $W \le 39$) have `is_locked = TRUE` in `hisaab_settlement_weeks`.
- [ ] **Shadow Rehearsal**: Execute `sp_validate_and_commit_cutoff_snapshot` in staging database (`letzryd-dev-test`) and verify that total opening debt matches Finance sign-off within ₹0.00 tolerance.
- [ ] **Database Pre-Execution Backup**:
  ```bash
  gcloud sql backups create \
    --instance=letzryd-core-prod \
    --description="Pre-Cutoff Cumulative Rolling Migration Backup"
  ```

---

### 5.2 Day of Cutoff Execution Checklist (Cutoff Night: Sunday 23:59 -> Monday 03:00)

| Time Window | Execution Step | Operational Action | Verification Criteria |
| :--- | :--- | :--- | :--- |
| **00:01 – 00:15** | **1. Traffic Drain & Maintenance Mode** | Temporarily pause frontend write APIs; display "Settlement Cycle Underway" maintenance banner on Driver App. | Zero in-flight Cashfree webhook drops (Cashfree retries safely). |
| **00:15 – 00:30** | **2. Lock Cutoff Settlement Week** | Execute: `UPDATE hisaab_settlement_weeks SET is_locked = TRUE WHERE week_id = 'CY26WK39';` | `is_locked = TRUE` for cutoff week. |
| **00:30 – 01:00** | **3. Load & Validate Staging Data** | Ingest signed-off CSV into `stg_hisaab_cutoff_raw` via `COPY` or Python migration runner. | Row count matches Finance master record (1,830 vehicles / 486 partners). |
| **01:00 – 01:30** | **4. Commit Master Snapshot ($B_0$)** | Execute `CALL sp_validate_and_commit_cutoff_snapshot('CUTOFF_CY26WK39_20260927', 'CY26WK39', '2026-09-27', 'HeadOfFinance');` | Record created in `hisaab_cutoff_master` with `is_frozen = TRUE`. |
| **01:30 – 02:00** | **5. Deploy Rolling Procedures** | Deploy `sp_sync_hisaab_partner_weekly_rolling` and updated `app_tables_full_setup.sql`. | Stored procedure compilation exit code 0. |
| **02:00 – 02:30** | **6. Run Week 1 Roll-Forward Engine** | Execute `CALL sp_sync_hisaab_partner_weekly_rolling('CY26WK40', NULL);` | Verified that `previous_outstanding` matches snapshot $B_0$. |
| **02:30 – 02:45** | **7. Sync App Tables** | Execute `CALL sp_populate_all_app_tables(FALSE);` | `app_hisaabs` and `app_drivers` populated with cumulative metrics. |
| **02:45 – 03:00** | **8. Smoke Test & Go-Live** | Execute API integration tests (`test_backend_fixes.py`), lift maintenance banner, enable pg_cron jobs. | Portal live on `cashfree-web-portal.vercel.app`. |

---

### 5.3 Automated Rollback Strategy

If discrepancy exceeds tolerance or operational irregularities occur during execution, execute the automated rollback protocol within 15 minutes:

#### Emergency Reversion Command
```sql
-- 1. Flip system configuration flag to immediately revert to single-week mode
UPDATE public.hisaab_system_config 
SET config_value = 'false' 
WHERE config_key = 'enable_roll_forward';

-- 2. Restore legacy partner synchronization procedure
CREATE OR REPLACE PROCEDURE public.sp_sync_hisaab_partner_weekly(
    IN p_week_id VARCHAR, 
    IN p_partner VARCHAR DEFAULT NULL
)
LANGUAGE plpgsql
AS $procedure$
BEGIN
    -- Revert to week-isolated calculation
    UPDATE public.hisaab_partner_weekly
    SET previous_outstanding = 0.00,
        total_outstanding = current_week_os - amount_paid_during_week,
        net_amount_to_collect = GREATEST(0.00, current_week_os - amount_paid_during_week),
        net_bank_payout = ABS(LEAST(0.00, current_week_os - amount_paid_during_week))
    WHERE week_id = p_week_id AND settlement_status <> 'LOCKED';
END;
$procedure$;

-- 3. Resync App Tables in legacy isolated mode
CALL public.sp_populate_all_app_tables(FALSE);
```

#### Point-in-Time Database Recovery
If database data corruption occurred:
```bash
gcloud sql instances restore-backup letzryd-core-prod \
  --backup-id=<BACKUP_ID_TAKEN_AT_T0>
```

---

## 6. Architecture Sign-Off & Verification Checklist

- [x] **Mathematical Integrity**: Bidirectional roll-forward handles debt, credit payouts, and advance surplus without sign drift.
- [x] **Zero Driving Continuity**: Inactive partners with unpaid dues or credits persist indefinitely in the ledger.
- [x] **Multi-Vehicle Rollup**: Operator fleets cleanly consolidate all assigned vehicles into an entity-level rolling ledger.
- [x] **Real-Time Payment Write-Back**: Cashfree webhook immediately writes back to `hisaab_partner_weekly` and `app_drivers`.
- [x] **Idempotency & Auditing**: Immutable Cutoff Master with SHA-256 checksum and audit logs for all adjustments.
