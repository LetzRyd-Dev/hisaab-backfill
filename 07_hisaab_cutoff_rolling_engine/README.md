# LetzRyd Cutoff-Based Cumulative Hisaab & Outstanding Engine
========================================================================

## Overview
This standalone repository contains the complete implementation and execution blueprint for transitioning LetzRyd from **isolated weekly hisaab cycles** to a **Cutoff-Based Cumulative Rolling Ledger**.

### When To Use This
Whenever management decides on a specific **Cutoff Date** (e.g., as of yesterday/tomorrow), and has the finalized outstanding balances for each driver/partner:
1. Load the finalized balance into the Cutoff Baseline Table (`hisaab_partner_opening_balances`).
2. Run the migration script to enable automatic cumulative roll-forward from that cutoff week onward.
3. Every subsequent week automatically adds the new weekly hisaab to any unpaid balance until payments are made.

---

## Repository Structure

* `schema_cutoff_baseline.sql` — PostgreSQL DDL tables for cutoff opening balances, audit history, and configuration flags.
* `sp_sync_hisaab_cumulative_cutoff.sql` — Complete PostgreSQL stored procedure that:
  - Freezes all weeks before the cutoff.
  - Injects the baseline opening balances on the cutoff week.
  - For every week post-cutoff, carries forward unpaid debt:
    $$\text{Previous Outstanding}_{W} = \max(0, \text{Total Outstanding}_{W-1} - \text{Payments Received}_{W-1})$$
* `cumulative_engine.py` — Python backend settlement engine with dry-run capabilities, validation checks, and CSV/JSON cutoff loader.
* `api_payment_integration_guide.md` — Explains how the payment gateway (Cashfree/UPI) credits cumulative debt in real time.
* `sample_cutoff_input.csv` — Template format for uploading driver & operator cutoff numbers.
* `test_simulation.py` — Automated verification script testing multiple weeks of non-payment, partial payment, and full payment.
