"""
Multi-Grain Parity Audit Verifier (Module 06)
---------------------------------------------
100% READ-ONLY diagnostic tool that audits exact mathematical parity
between Tier 2 (Vehicle Weekly) and Tier 3 (Partner Consolidated) for any settlement cycle.

Checks:
1. Exact zero-variance parity: sum(vehicle current_week_os) == sum(partner current_week_os).
2. Standalone Mode isolation: sum(previous_outstanding) == 0.00.
3. Bank detail attribution for partners entitled to net payouts.

Usage:
    python audit_multi_grain_parity.py --week CY26WK28
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def audit_parity(week_id: str):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"MULTI-GRAIN FINANCIAL PARITY AUDIT: {week_id}")
    print("Mode: 100% READ-ONLY (Zero writes, zero mutation)")
    print("=" * 80)

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        print(f"Target Period: {week_start} through {week_end} (Status: {'LOCKED' if is_locked else 'OPEN'})\n")

        # 1. Tier 2: Vehicle Weekly Aggregation
        veh = conn.execute(text("""
            SELECT 
                count(*) as vehicle_count,
                COALESCE(sum(net_weekly_lease_rental), 0.0) as total_rent,
                COALESCE(sum(total_platform_earnings), 0.0) as total_earnings,
                COALESCE(sum(total_cash_collected), 0.0) as total_cash,
                COALESCE(sum(challan_amount), 0.0) as total_challans,
                COALESCE(sum(adjustment_amount), 0.0) as total_adj,
                COALESCE(sum(dead_mile_charges), 0.0) as total_dead_miles,
                COALESCE(sum(tds_amount), 0.0) as total_tds,
                COALESCE(sum(current_week_os), 0.0) as vehicle_os,
                COALESCE(sum(to_collect), 0.0) as to_collect,
                COALESCE(sum(to_payout), 0.0) as to_payout
            FROM public.hisaab_vehicle_weekly
            WHERE week_id = :w;
        """), {"w": week_id}).fetchone()

        if veh[0] == 0:
            print(f"[FAIL] Zero records found in hisaab_vehicle_weekly for {week_id}!")
            print("Action Required: Run 05_hisaab_generator/generate_weekly_hisaab.py first.")
            sys.exit(1)

        # 2. Tier 3: Partner Weekly Aggregation
        part = conn.execute(text("""
            SELECT 
                count(*) as partner_count,
                COALESCE(sum(current_week_os), 0.0) as partner_os,
                COALESCE(sum(net_amount_to_collect), 0.0) as partner_collect,
                COALESCE(sum(net_bank_payout), 0.0) as partner_payout,
                COALESCE(sum(previous_outstanding), 0.0) as prev_os,
                count(CASE WHEN net_bank_payout > 0 AND (payout_account_number IS NULL OR TRIM(payout_account_number) = '') THEN 1 END) as payout_missing_bank
            FROM public.hisaab_partner_weekly
            WHERE week_id = :w;
        """), {"w": week_id}).fetchone()

        # Print Side-by-Side Financial Comparison
        print(f"{'Metric':<35} | {'Tier 2 (Vehicles)':<20} | {'Tier 3 (Partners)'}")
        print("-" * 75)
        print(f"{'Entities Settled':<35} | {veh[0]:<20,} | {part[0]:,}")
        print(f"{'Net Current Week O/S':<35} | Rs. {float(veh[8]):<16,.2f} | Rs. {float(part[1]):,.2f}")
        print(f"{'Total Dues To Collect':<35} | Rs. {float(veh[9]):<16,.2f} | Rs. {float(part[2]):,.2f}")
        print(f"{'Total Treasury Payout':<35} | Rs. {float(veh[10]):<16,.2f} | Rs. {float(part[3]):,.2f}")
        print(f"{'Historical Debt (Prev O/S)':<35} | {'N/A (Tier 3 Only)':<20} | Rs. {float(part[4]):,.2f}")
        print("-" * 75)

        # Parity Variance Computation
        diff = abs(float(veh[8]) - float(part[1]))

        print("\nPARITY & FINANCIAL SCORECARD:")
        all_passed = True

        # Gate 1: Zero-Variance Parity
        if diff < 0.01:
            print(f"  [PASS] Gate 1: Exact Mathematical Parity (diff = Rs. {diff:.2f}).")
        else:
            print(f"  [FAIL] Gate 1: Parity variance detected! Diff = Rs. {diff:,.2f}")
            all_passed = False

        # Gate 2: Standalone Mode Isolation
        if abs(float(part[4])) < 0.01:
            print("  [PASS] Gate 2: Standalone Mode Verified (previous_outstanding == 0.00).")
        else:
            print(f"  [FAIL] Gate 2: Non-zero previous debt detected (Rs. {float(part[4]):,.2f})!")
            all_passed = False

        # Gate 3: Bank Account Readiness
        if part[5] == 0:
            print("  [PASS] Gate 3: 100% of partners entitled to payout have bank details.")
        else:
            print(f"  [WARNING] Gate 3: Found {part[5]} partners with net payout missing bank details.")

        print("=" * 80)
        if all_passed:
            print(f"[FINAL VERDICT: 100% PARITY APPROVED] Week {week_id} is mathematically verified.")
            print("You are cleared to export Excel statements and lock the settlement cycle.")
        else:
            print(f"[FINAL VERDICT: REJECTED] Parity audit failed. Review variances above.")
            sys.exit(1)
        print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit multi-grain financial parity.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    args = parser.parse_args()

    audit_parity(args.week)
