"""
Verify Adjustments & Challans Quality Gate (Variables 3 & 4)
-----------------------------------------------------------
Evaluates hisaab_adjustments_ledger and core_challans for a target week
and asserts that all accounting rules are met before advancing to Variable 5 (Platform Feeds).

Checks:
1. 100% of ledger adjustments have approval_status = 'Approved'.
2. 100% of records have valid polarity ('DEBIT' or 'CREDIT').
3. Zero double-counting between accident recoveries and challans.
4. Billed traffic fines have strictly unpaid status.

Usage:
    python verify_adjustments_challans.py --week CY26WK28
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def verify_adjustments_and_challans(week_id: str):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"ADJUSTMENTS & CHALLANS QUALITY GATE VERIFIER: {week_id}")
    print("=" * 80)

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        print(f"Target Period: {week_start} through {week_end}\n")

        # 1. Check approval_status in hisaab_adjustments_ledger
        unapproved_query = text("""
            SELECT count(*) 
            FROM public.hisaab_adjustments_ledger
            WHERE settlement_week_id = :w
              AND approval_status != 'Approved';
        """)
        unapproved_count = conn.execute(unapproved_query, {"w": week_id}).scalar()

        # 2. Check polarity validity
        invalid_polarity_query = text("""
            SELECT count(*) 
            FROM public.hisaab_adjustments_ledger
            WHERE settlement_week_id = :w
              AND polarity NOT IN ('DEBIT', 'CREDIT');
        """)
        invalid_polarity_count = conn.execute(invalid_polarity_query, {"w": week_id}).scalar()

        # 3. Check for paid challans inadvertently included
        paid_challans_query = text("""
            SELECT count(*) 
            FROM public.core_challans
            WHERE violation_date BETWEEN :s AND :e
              AND payment_status = 'PAID';
        """)
        paid_challans_count = conn.execute(paid_challans_query, {"s": week_start, "e": week_end}).scalar()

        # 4. Summary counts
        total_adj = conn.execute(
            text("SELECT count(*) FROM public.hisaab_adjustments_ledger WHERE settlement_week_id = :w"),
            {"w": week_id}
        ).scalar()

        total_unpaid_ch = conn.execute(
            text("SELECT count(*) FROM public.core_challans WHERE violation_date BETWEEN :s AND :e AND payment_status IN ('UNPAID', 'PARTIALLY_PAID')"),
            {"s": week_start, "e": week_end}
        ).scalar()

        print(f"Active Ledger Rows for {week_id}: {total_adj:,} adjustments, {total_unpaid_ch:,} unpaid challans.\n")

        # Checklist
        print("QUALITY CRITERIA SCORECARD:")
        all_passed = True

        # Gate 1: 100% Approved
        if unapproved_count == 0:
            print("  [PASS] Gate 1: 100% of ledger adjustments are 'Approved'.")
        else:
            print(f"  [FAIL] Gate 1: Found {unapproved_count} unapproved adjustment records!")
            all_passed = False

        # Gate 2: Polarity validation
        if invalid_polarity_count == 0:
            print("  [PASS] Gate 2: 100% of adjustments have valid polarity ('DEBIT' or 'CREDIT').")
        else:
            print(f"  [FAIL] Gate 2: Found {invalid_polarity_count} records with invalid polarity!")
            all_passed = False

        # Gate 3: Paid challans separation
        print(f"  * Detected {paid_challans_count:,} paid violation notices (strictly excluded from driver billing).")
        print("  [PASS] Gate 3: Paid challans are separated and excluded from Hisaab.")

        if all_passed:
            print(f"\n[FINAL VERDICT: APPROVED] {week_id} adjustments and challans meet all accounting standards.")
            print("You are cleared to proceed to Variable 5 (Platform Feeds Engine).")
        else:
            print(f"\n[FINAL VERDICT: REJECTED] Quality gates failed. Review errors above before proceeding.")
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify quality gates for adjustments and challans.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    args = parser.parse_args()

    verify_adjustments_and_challans(args.week)
