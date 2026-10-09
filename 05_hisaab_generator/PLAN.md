# VARIABLES 7 & 8: MASTER HISAAB WEEKLY GENERATOR

**Module Directory:** `05_hisaab_generator/`  
**Target Persistence Tables:**
- `public.hisaab_vehicle_weekly` (Tier 2 Vehicle Weekly Reconciliation Statement)
- `public.hisaab_partner_weekly` (Tier 3 Consolidated Operator Payout Statement)
- `public.hisaab_settlement_weeks` (Master Calendar & Immutability Lock Controller)
**Upstream Feeds Joined:**
- `daily_rent_log` (Variables 1 & 2: Custody & Rent)
- `hisaab_adjustments_ledger` (Variable 3: Approved Debits/Credits)
- `core_challans` (Variable 4: Traffic Fines)
- `core_uber_daily` / `core_ola_daily` (Variable 5: Platform Fares & Cash)
- `core_gps` (Variable 6: GPS Dead Mile Penalty)

---

## 1. OBJECTIVE & ARCHITECTURAL ROLE
Variables 7 & 8 form the **master compilation and settlement clearinghouse** of the Hisaab Engine:
- **Variable 7 (Statutory TDS):** Calculates 1% tax deduction under Section 194C / 194O on positive net operational margins.
- **Variable 8 (Master Settlement & Partner Rollup):**
  1. Computes `current_week_os` per vehicle.
  2. Aggregates multi-car operators (1 to 200+ vehicles) into `hisaab_partner_weekly`.
  3. Enforces **Standalone Mode** (`previous_outstanding = 0.00`) to guarantee that historical unverified offline payments never contaminate current balances.
  4. Applies Monday hard immutability locks.

---

## 2. MATHEMATICAL SETTLEMENT FORMULATIONS

### A. Tier 2: Vehicle Weekly Outstanding (`current_week_os`)
$$\begin{aligned}
\text{current\_week\_os} = &\;\text{Net Weekly Lease Rental} \\
&- (\text{Uber Week O/S} + \text{Ola Week O/S}) \\
&+ \text{Challan Amount} \\
&+ \text{Adjustment Amount (Signed)} \\
&+ \text{Accident Deduction} \\
&+ \text{GPS Dead Mile Penalty} \\
&+ \text{TDS Amount}
\end{aligned}$$

$$\text{To Collect from Driver} = \max(0, \text{current\_week\_os})$$
$$\text{To Payout to Driver} = \max(0, -\text{current\_week\_os})$$

### B. Variable 7: Statutory TDS (Section 194C)
$$\text{Taxable Margin} = \max(0.00, \text{Gross Platform Earnings} - \text{Net Weekly Lease Rental})$$
$$\text{TDS Amount} = \begin{cases}
\text{ROUND}(\text{Taxable Margin} \times 1\%, 2), & \text{for Individual drivers with positive margin} \\
₹0.00, & \text{for Fleet Operators (exempt) and negative margin}
\end{cases}$$

### C. Tier 3: Consolidated Partner Settlement (`hisaab_partner_weekly`)
$$\text{Total Outstanding} = \sum \text{Current Week O/S} + \text{Previous Outstanding} + \text{Prior Period Adjustments} - \text{Interim Payments}$$
- **Standalone Mode Enforcement:** For all historical backfills, `previous_outstanding = 0.00`.
$$\text{Net Bank Payout} = |\min(0, \text{Total Outstanding})|$$
$$\text{Net Amount to Collect} = \max(0, \text{Total Outstanding})$$

---

## 3. SCRIPTS IN THIS MODULE

1. **`generate_weekly_hisaab.py` (Master Execution Runner):**
   - Orchestrates `sp_sync_hisaab_vehicle_weekly` and `sp_sync_hisaab_partner_weekly`.
   - Enforces `HisaabSafetyGuard` (hard-blocks protected production weeks `W26, W27, W36–W41`).
   - Runs in DRY-RUN mode by default (automatic `ROLLBACK`), requiring `--commit` to persist.
2. **`lock_settlement_week.py` (Immutability Lock Runner):**
   - Invokes `sp_check_and_enforce_monday_lock` to transition finalized historical weeks to `is_locked = TRUE`.
   - Prevents accidental subsequent modifications.
