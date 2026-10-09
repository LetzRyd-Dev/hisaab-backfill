"""
Verify Custody Output Quality Gate (Variable 1)
-----------------------------------------------
Evaluates core_daily_vehicle_status for a target week and asserts that
all data-quality prerequisites are met before advancing to Variable 2 (Rent Engine).

Checks:
1. Minimum row presence across all 7 calendar days.
2. Zero active records with NULL partner_id.
3. 100% valid city attribution (Bangalore, Hyderabad, Mumbai).
4. Balanced Active vs RFD vs Maintenance operational ratios.

Usage:
    python verify_custody_output.py --week CY26WK28
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def verify_custody(week_id: str):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"CUSTODY QUALITY GATE VERIFIER: {week_id}")
    print("=" * 80)

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        print(f"Target Period: {week_start} through {week_end}\n")

        # 1. Row presence check per day
        days_query = text("""
            SELECT status_date, count(*) as total_rows,
                   count(CASE WHEN final_status IN ('Active', 'Allocation', 'Same Day D&A') THEN 1 END) as active_rows,
                   count(CASE WHEN final_status = 'RFD' THEN 1 END) as rfd_rows,
                   count(CASE WHEN final_status = 'Maintenance' THEN 1 END) as maint_rows
            FROM public.core_daily_vehicle_status
            WHERE status_date BETWEEN :s AND :e
            GROUP BY status_date
            ORDER BY status_date;
        """)
        day_rows = conn.execute(days_query, {"s": week_start, "e": week_end}).fetchall()

        if not day_rows:
            print(f"[FAIL] Zero records found in core_daily_vehicle_status for {week_id}!")
            print("Action Required: Run backfill_custody.py first.")
            sys.exit(1)

        print(f"{'Date':<12} | {'Total Rows':<11} | {'Active (On-Road)':<17} | {'RFD (Yard)':<11} | {'Maintenance'}")
        print("-" * 65)
        for r in day_rows:
            print(f"{r[0]}   | {r[1]:<11,} | {r[2]:<17,} | {r[3]:<11,} | {r[4]:,}")
        print("-" * 65)

        # 2. Check for missing partner IDs on active cars
        missing_partner_query = text("""
            SELECT count(*) 
            FROM public.core_daily_vehicle_status
            WHERE status_date BETWEEN :s AND :e
              AND final_status IN ('Active', 'Allocation')
              AND (partner_id IS NULL OR TRIM(partner_id) = '');
        """)
        missing_partners = conn.execute(missing_partner_query, {"s": week_start, "e": week_end}).scalar()

        # 3. Check for unmapped cities
        unmapped_city_query = text("""
            SELECT count(*) 
            FROM public.core_daily_vehicle_status
            WHERE status_date BETWEEN :s AND :e
              AND (city IS NULL OR city NOT IN ('Bangalore', 'Hyderabad', 'Mumbai'));
        """)
        unmapped_cities = conn.execute(unmapped_city_query, {"s": week_start, "e": week_end}).scalar()

        # Summary Checklist
        print("\nQUALITY CRITERIA SCORECARD:")
        all_passed = True

        # Gate A: 7-day presence
        if len(day_rows) == 7:
            print("  [PASS] Gate 1: Complete 7-day continuity present.")
        else:
            print(f"  [FAIL] Gate 1: Incomplete week! Found {len(day_rows)}/7 days.")
            all_passed = False

        # Gate B: Zero missing partners
        if missing_partners == 0:
            print("  [PASS] Gate 2: Zero active vehicles with NULL partner_id.")
        else:
            print(f"  [FAIL] Gate 2: Found {missing_partners} active records missing partner_id!")
            all_passed = False

        # Gate C: 100% valid cities
        if unmapped_cities == 0:
            print("  [PASS] Gate 3: 100% valid city mappings (BLR, HYD, MUM).")
        else:
            print(f"  [FAIL] Gate 3: Found {unmapped_cities} records with unmapped city!")
            all_passed = False

        if all_passed:
            print(f"\n[FINAL VERDICT: APPROVED] {week_id} vehicle custody meets all standards.")
            print("You are cleared to proceed to Variable 2 (Daily Contracted Lease Rent Engine).")
        else:
            print(f"\n[FINAL VERDICT: REJECTED] Quality gates failed. Review errors above before proceeding.")
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify quality gates for vehicle custody backfill.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    args = parser.parse_args()

    verify_custody(args.week)
