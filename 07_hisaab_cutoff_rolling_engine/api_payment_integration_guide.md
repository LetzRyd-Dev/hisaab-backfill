# Payment Gateway Integration Guide (Cashfree & UPI)
===================================================

## How Payments Credit Against the Cumulative Cutoff Ledger

When a driver or operator pays via the app (Cashfree PG / UPI QR), the payment must credit against the cumulative balance in real time using the following waterfall tier logic:

### 1. Cumulative Water-Fall Allocation Rule

```text
Payment Amount Received (P)
       │
       ▼
[Tier 1: Active Outstanding Debt]
Credit towards oldest carried-forward / cumulative dues first:
  • Reduce previous_outstanding / current_week_os
  • Update amount_paid_during_week += P
  • Recompute total_outstanding = max(0, total_outstanding - P)
       │
       ▼ (If P > total_outstanding, i.e. Surplus / Overpayment)
[Tier 2: Pending Security Deposit]
  • Credit remainder towards standing deposit (deposit_pending -= Surplus)
  • Increase deposit_paid += Surplus
       │
       ▼ (If Surplus > deposit_pending)
[Tier 3: Advance Credit]
  • Surplus remains as a negative balance (Advance credit offset against next week)
```

### 2. Real-Time DB Hook

When `/api/payments/verify-order` completes successfully:

```sql
UPDATE public.hisaab_partner_weekly
SET 
    amount_paid_during_week = amount_paid_during_week + :paid_amount,
    total_outstanding = GREATEST(0.00, (current_week_os + previous_outstanding + prior_period_adjustments) - (amount_paid_during_week + :paid_amount)),
    net_amount_to_collect = GREATEST(0.00, (current_week_os + previous_outstanding + prior_period_adjustments) - (amount_paid_during_week + :paid_amount)),
    settlement_status = CASE 
        WHEN (current_week_os + previous_outstanding + prior_period_adjustments) <= (amount_paid_during_week + :paid_amount) THEN 'SETTLED'
        ELSE 'PARTIAL'
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE week_id = :active_week_id AND partner_id = :partner_id;
```
