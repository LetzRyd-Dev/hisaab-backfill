# Payment Gateway Integration Guide (Cashfree & UPI)
===================================================

**Document Version:** 2.1.0-PROD  
**Target Systems:** FastAPI Backend (`cashfree-web-portal`), PostgreSQL (`letzryd-core-prod`), React 19 Driver/Operator Portal  
**Scope:** Real-Time Payment Deduction, 4-Tier Debt Waterfall Allocation, and Monday 12:00 PM Noon Cutoff Routing  

---

## 1. Executive Summary & Core Principles

When a driver or fleet operator makes a payment via the LetzRyd App (Cashfree Payment Gateway, Dynamic UPI QR, or Bank Transfer), the funds must credit against their cumulative hisaab ledger **in real time** rather than waiting for manual weekend reconciliations.

The payment engine operates on two foundational architectural pillars:
1. **Real-Time Debt Reduction**: The moment a payment transaction is verified via the Cashfree webhook or order verification API, the partner's active `total_outstanding` and `amount_paid_during_week` are decremented immediately.
2. **Monday 12:00 PM Noon Cutoff & Accounting Immutability**:
   - The weekly settlement cycle lock occurs strictly at **Monday 12:00 PM (Noon) IST**.
   - Payments received **on or before Monday 12:00 PM Noon** apply directly to the active week ($W_k$).
   - At Monday 12:00 PM Noon, the week is locked (`is_locked = TRUE`), freezing closing balances.
   - Any payment received **after Monday 12:00 PM Noon** is automatically routed and credited to the **subsequent week ($W_{k+1}$)**. It **never mutates a locked historical week**.

---

## 2. The 4-Tier Cumulative Payment Waterfall Rule

Incoming payments are allocated across liability tiers in strict financial priority:

```text
               Incoming Payment Received (P)
                             │
                             ▼
     ┌─────────────────────────────────────────────────────────┐
     │ TIER 1: Active Operational Hisaab Debt (Prior + Current)│
     │ - Credit towards oldest carried-forward / cutoff dues   │
     │ - Real-time reduction: total_outstanding = max(0, O/S - P)
     │ - Update: amount_paid_during_week += P                  │
     └───────────────────────────┬─────────────────────────────┘
                                 │
                                 ▼ (If P > Active Debt, Surplus S1)
     ┌─────────────────────────────────────────────────────────┐
     │ TIER 2: Pending Security Deposit Target                 │
     │ - Credit surplus towards standing deposit deficit:      │
     │   Deposit_Credit = min(S1, Deposit_Target - Deposit_Paid)
     │ - Reduce deposit_pending -= Deposit_Credit              │
     └───────────────────────────┬─────────────────────────────┘
                                 │
                                 ▼ (If S1 > Deposit Deficit, Surplus S2)
     ┌─────────────────────────────────────────────────────────┐
     │ TIER 3: Specific Traffic Challans & Penalties           │
     │ - Settle unbilled challans logged in core_challans      │
     └───────────────────────────┬─────────────────────────────┘
                                 │
                                 ▼ (If Remaining Surplus S3 > 0)
     ┌─────────────────────────────────────────────────────────┐
     │ TIER 4: Advance Fleet Credit (Surplus Balance)          │
     │ - Balance becomes negative (B_k < 0)                    │
     │ - Carries forward as credit offset against next week    │
     └─────────────────────────────────────────────────────────┘
```

### Mathematical Allocation Formula:
$$\text{Alloc}_{\text{Tier1}} = \min(P, \max(0.00, B_k))$$
$$S_1 = P - \text{Alloc}_{\text{Tier1}}$$
$$\text{Alloc}_{\text{Tier2}} = \min(S_1, \max(0.00, \text{Deposit}_{\text{target}} - \text{Deposit}_{\text{paid}}))$$
$$S_2 = S_1 - \text{Alloc}_{\text{Tier2}}$$
$$\text{Alloc}_{\text{Tier3}} = \min(S_2, \text{UnbilledChallans})$$
$$\text{Alloc}_{\text{Tier4}} = S_2 - \text{Alloc}_{\text{Tier3}} \quad (\text{Stored as Advance Credit for } W_{k+1})$$

---

## 3. Monday 12:00 PM Noon Cutoff & Week Locking Architecture

### 3.1 Settlement Cycle Timeline

| Phase | Time Window (IST) | System Behavior |
| :--- | :--- | :--- |
| **Operational Driving Period** | Monday 00:00:00 to Sunday 23:59:59 | Trips, earnings, rentals, challans accumulate in `hisaab_vehicle_weekly`. |
| **Hisaab Generation & Review** | Monday 00:00:00 to Monday 11:59:59 AM | Hisaabs generated. Drivers view dues in app and make UPI/Cashfree payments. |
| **Monday 12:00 PM Noon Cutoff** | **Monday 12:00:00 PM Sharp** | **Weekly cycle locks (`is_locked = TRUE`).** Final closing debt freezes. |
| **Next Cycle ($W_{k+1}$) Active** | Monday 12:00:01 PM onwards | Week $k+1$ begins. Inherits previous week's locked balance as `previous_outstanding`. |

### 3.2 Cutoff Routing Logic for Payments

When Cashfree sends a payment webhook (`ORDER_PAID`) or the driver completes checkout via `/api/payments/verify-order`:

1. **Check Target Week Lock Status**:
   - Query `hisaab_settlement_weeks` for the active week corresponding to the settlement cycle.
   - If `CURRENT_TIMESTAMP <= week_end_cutoff (Monday 12:00:00 PM)` AND `is_locked = FALSE`:
     - Credit payment directly to **Week $k$** (`week_id = p_week_id`).
2. **Post-Cutoff / Locked Week Routing**:
   - If `CURRENT_TIMESTAMP > week_end_cutoff (Monday 12:00:00 PM)` OR `is_locked = TRUE`:
     - **Credit payment automatically to Week $k+1$** (`week_id = p_next_week_id`).
     - **Week $k$ remains strictly immutable** with its finalized closing outstanding.
     - Week $k+1$ inherits Week $k$'s locked balance as `previous_outstanding`, and the incoming payment reduces Week $k+1$'s live outstanding immediately!

---

## 4. Real-Time DB Hook Implementation

### 4.1 Payment Verification Endpoint (`/api/payments/verify-order`)

When the payment verification succeeds in the backend:

```sql
-- Step 1: Resolve the appropriate settlement week based on Monday 12:00 PM Noon cutoff
WITH target_week AS (
    SELECT 
        CASE 
            -- If past Monday 12:00 PM Noon or week is already locked, route to next week
            WHEN sw.is_locked = TRUE OR CURRENT_TIMESTAMP > (sw.week_start + INTERVAL '7 days 12 hours')
            THEN 'CY' || to_char(sw.week_start + INTERVAL '7 days', 'IY') || 'WK' || to_char(sw.week_start + INTERVAL '7 days', 'IW')
            ELSE sw.week_id
        END AS effective_week_id
    FROM public.hisaab_settlement_weeks sw
    WHERE sw.week_id = :requested_week_id
)
-- Step 2: Atomic Real-Time Outstanding Reduction
UPDATE public.hisaab_partner_weekly pw
SET 
    amount_paid_during_week = pw.amount_paid_during_week + :paid_amount,
    
    -- Real-time total outstanding calculation: (Gross Dues) - (Updated Payments)
    total_outstanding = GREATEST(0.00, 
        (pw.current_week_os + pw.previous_outstanding + pw.prior_period_adjustments) 
        - (pw.amount_paid_during_week + :paid_amount)
    ),
    
    net_amount_to_collect = GREATEST(0.00, 
        (pw.current_week_os + pw.previous_outstanding + pw.prior_period_adjustments) 
        - (pw.amount_paid_during_week + :paid_amount)
    ),
    
    net_bank_payout = ABS(LEAST(0.00, 
        (pw.current_week_os + pw.previous_outstanding + pw.prior_period_adjustments) 
        - (pw.amount_paid_during_week + :paid_amount)
    )),
    
    settlement_status = CASE 
        WHEN (pw.current_week_os + pw.previous_outstanding + pw.prior_period_adjustments) <= (pw.amount_paid_during_week + :paid_amount) 
             THEN 'settled'
        ELSE 'to_collect'
    END,
    
    updated_at = CURRENT_TIMESTAMP
FROM target_week tw
WHERE pw.week_id = tw.effective_week_id 
  AND pw.partner_id = :partner_id;
```

### 4.2 Logging to Payment Audit Ledger

```sql
INSERT INTO public.hisaab_payment_ledger (
    payment_reference_id,
    order_id,
    partner_id,
    allocated_week_id,
    amount,
    payment_mode,
    gateway_status,
    payment_timestamp,
    routed_post_cutoff
) VALUES (
    :cf_payment_id,
    :order_id,
    :partner_id,
    tw.effective_week_id,
    :paid_amount,
    :payment_mode,
    'SUCCESS',
    CURRENT_TIMESTAMP,
    (tw.effective_week_id <> :requested_week_id)
);
```

---

## 5. End-to-End Concrete Scenarios (Validated via `test_simulation.py`)

The table below summarizes the 4 core settlement lifecycle scenarios verified in the test suite:

| Scenario | Opening Cutoff ($B_0$) | Week 1 Hisaab ($\Delta H_1$) | Pre-Payment Due | Payment Amount ($P_1$) | Payment Timing | Week 1 Closing O/S | Week 1 Status | Week 2 Opening O/S ($B_1$) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Scenario A** | ₹2,000.00 | ₹1,000.00 | ₹3,000.00 | **₹2,500.00** | Mon 10:00 AM (Pre-Cutoff) | **₹500.00** | `to_collect` | **₹500.00** |
| **Scenario B** | ₹2,000.00 | ₹1,000.00 | ₹3,000.00 | **₹0.00** | No payment | **₹3,000.00** | `to_collect` | **₹3,000.00** |
| **Scenario C** | ₹2,000.00 | ₹1,000.00 | ₹3,000.00 | **₹3,000.00** | Mon 11:30 AM (Pre-Cutoff) | **₹0.00** | `settled` | **₹0.00** |
| **Scenario D** | ₹2,000.00 | ₹1,000.00 | ₹3,000.00 | **₹2,500.00** | **Mon 12:30 PM (Post-Cutoff)** | **₹3,000.00 (Locked)** | `to_collect` | **₹3,000.00** (₹2,500 credited to W2; net drops to ₹500) |

### Detailed Walkthroughs:

#### Scenario A: Partial Payment Before Cutoff
* **Setup**: Driver has $B_0 = ₹2,000$ baseline cutoff debt and incurs $\Delta H_1 = ₹1,000$ in Week 1. Total due is $₹3,000$.
* **Event**: Driver pays $₹2,500$ at 10:00 AM on Monday (before 12:00 PM Noon).
* **Live Action**: Outstanding immediately drops from $₹3,000$ to $₹500$.
* **Cutoff**: At Monday 12:00 PM Noon, Week 1 locks with $₹500$ final closing balance.
* **Roll-Forward**: Week 2 initializes with `previous_outstanding = ₹500`.

#### Scenario B: Zero Payment
* **Setup**: Driver has $B_0 = ₹2,000$ baseline debt and incurs $\Delta H_1 = ₹1,000$ in Week 1. Total due is $₹3,000$.
* **Event**: Driver makes no payment ($P_1 = ₹0$).
* **Live Action**: Outstanding remains $₹3,000$.
* **Cutoff**: At Monday 12:00 PM Noon, Week 1 locks with $₹3,000$ closing balance.
* **Roll-Forward**: Week 2 initializes with `previous_outstanding = ₹3,000`.

#### Scenario C: Full Clearance Before Cutoff
* **Setup**: Driver has $B_0 = ₹2,000$ baseline debt and incurs $\Delta H_1 = ₹1,000$ in Week 1. Total due is $₹3,000$.
* **Event**: Driver pays full $₹3,000$ at 11:30 AM on Monday (before 12:00 PM Noon).
* **Live Action**: Outstanding immediately drops to $₹0.00$, and status transitions to `'settled'`.
* **Cutoff**: At Monday 12:00 PM Noon, Week 1 locks with $₹0.00$ closing balance.
* **Roll-Forward**: Week 2 initializes with `previous_outstanding = ₹0.00`.

#### Scenario D: Late Payment After Monday 12:00 PM Noon Cutoff
* **Setup**: Driver has $B_0 = ₹2,000$ baseline debt and incurs $\Delta H_1 = ₹1,000$ in Week 1. Total due is $₹3,000$. No payment is made before Monday 12:00 PM Noon.
* **Cutoff Action**: At Monday 12:00 PM Noon, Week 1 locks. Final closing balance is locked at $₹3,000.00$.
* **Event**: Driver pays $₹2,500$ at 12:30 PM on Monday (after cutoff).
* **Routing Action**:
  - The payment engine detects that Week 1 is locked and the payment arrived after Monday 12:00 PM Noon.
  - Week 1 is **not mutated** (`amount_paid_during_week` remains $₹0.00$, `total_outstanding` remains $₹3,000.00$).
  - The payment is routed and credited directly to **Week 2** (`CY26WK42`).
* **Week 2 Impact**:
  - Week 2 starts with `previous_outstanding = ₹3,000.00` (from Week 1 locked closing balance).
  - Week 2 records `payment_received = ₹2,500.00`.
  - Live outstanding in Week 2 immediately reflects the deduction ($₹3,000 - ₹2,500 = ₹500$).
  - When Week 2 hisaab calculation runs later with $₹1,000$ new dues, final Week 2 outstanding is $₹3,000 + ₹1,000 - ₹2,500 = ₹1,500.00$.

---

## 6. Frontend & Driver App Live Sync

To ensure drivers immediately see updated balances after making a payment:
1. **Webhook Callback**: Upon Cashfree `PAYMENT_SUCCESS_WEBHOOK`, trigger PostgreSQL `NOTIFY hisaab_payment_event, '{"partner_id": "...", "amount": 2500}'`.
2. **Portal Cache Invalidation**: The driver mobile app and web portal query `/api/portal/driver-hisaab-summary` with cache-busting headers or listen via Server-Sent Events (SSE) / WebSockets to refresh:
   - "This Week's Net Hisaab"
   - "Total Cumulative Outstanding Due"
   - "Payment Status" (`SETTLED` badge).
