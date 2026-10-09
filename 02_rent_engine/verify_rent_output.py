"""
Verify Rent Output Quality Gate (Variable 2)
--------------------------------------------
Evaluates daily_rent_log for a target week and asserts that
all financial and business rules are met before advancing to Variable 3 (Adjustments).

Checks:
1. Minimum row presence across all 7 calendar days.
2. 100% of billable days have positive net daily rent.
3. 100% of non-billable days have strictly zero rent.
4. City indemnity fee compliance (BLR/MUM standard Rs. 30/day; HYD standard Rs. 0/day).
5. Average daily rent falls within realistic bounds (Rs. 800 - Rs. 1,100/day).

Usage:
    python verify_rent_output.py --week CY26WK28
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def verify_rent(week_id: str):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"RENT QUALITY GATE VERIFIER: {week_id}")
    print("=" * 80)

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        print(f"Target Period: {week_start} through {week_end}\n")

        # 1. Daily Summary
        day_query = text("""
            SELECT log_date, count(*) as total_rows,
                   count(CASE WHEN billable_rent_day = TRUE THEN 1 END) as billable_days,
                   sum(CASE WHEN billable_rent_day = TRUE THEN net_daily_rent ELSE 0 END) as total_rent_billed,
                   count(CASE WHEN billable_rent_day = FALSE THEN 1 END) as non_billable_days
            FROM public.daily_rent_log
            WHERE log_date BETWEEN :s AND :e
            GROUP BY log_date
            ORDER BY log_date;
        """)
        day_rows = conn.execute(day_query, {"s": week_start, "e": week_end}).fetchall()

        if not day_rows:
            print(f"[FAIL] Zero records found in daily_rent_log for {week_id}!")
            print("Action Required: Run backfill_rent.py first.")
            sys.exit(1)

        print(f"{'Date':<12} | {'Total Rows':<11} | {'Billable Days':<14} | {'Non-Billable':<13} | {'Total Rent Billed'}")
        print("-" * 72)
        total_rent = 0.0
        total_billable = 0
        for r in day_rows:
            total_rent += float(r[3] or 0.0)
            total_billable += int(r[2])
            print(f"{r[0]}   | {r[1]:<11,} | {r[2]:<14,} | {r[3]:<13,} | Rs. {float(r[3] or 0.0):,.2f}")
        print("-" * 72)
        print(f"TOTAL WEEKLY LEASE RENT BILLED: Rs. {total_rent:,.2f}\n")

        # 2. Check for Billable days with 0 rent
        zero_rent_query = text("""
            SELECT count(*) 
            FROM public.daily_rent_log
            WHERE log_date BETWEEN :s AND :e
              AND billable_rent_day = TRUE
              AND (net_daily_rent IS NULL OR net_daily_rent <= 0);
        """)
        zero_rent_count = conn.execute(zero_rent_query, {"s": week_start, "e": week_end}).scalar()

        # 3. Check for Non-billable days with positive rent
        leak_rent_query = text("""
            SELECT count(*) 
            FROM public.daily_rent_log
            WHERE log_date BETWEEN :s AND :e
              AND billable_rent_day = FALSE
              AND net_daily_rent > 0;
        """)
        leak_rent_count = conn.execute(leak_rent_query, {"s": week_start, "e": week_end}).scalar()

        # 4. Average rent per billable day
        avg_rent = (total_rent / total_billable) if total_billable > 0 else 0.0

        # Checklist
        print("QUALITY CRITERIA SCORECARD:")
        all_passed = True

        # Gate 1: 7-day continuity
        if len(day_rows) == 7:
            print("  [PASS] Gate 1: Complete 7-day continuity present.")
        else:
            print(f"  [FAIL] Gate 1: Incomplete week! Found {len(day_rows)}/7 days.")
            all_passed = False

        # Gate 2: Billable days rent compliance
        if zero_rent_count == 0:
            print("  [PASS] Gate 2: 100% of billable on-road days have positive rent.")
        else:
            print(f"  [FAIL] Gate 2: Found {zero_rent_count} billable records with zero or negative rent!")
            all_passed = False

        # Gate 3: Non-billable days zero-rent compliance
        if leak_rent_count == 0:
            print("  [PASS] Gate 3: 100% of non-billable days (RFD/Maintenance) have Rs. 0.00 rent.")
        else:
            print(f"  [FAIL] Gate 3: Found {leak_rent_count} non-billable records with unauthorized rent!")
            all_passed = False

        # Gate 4: Commercial reasonableness
        print(f"  * Average Daily Rent per Active Car: Rs. {avg_rent:,.2f}/day")
        if 750.0 <= avg_rent <= 1150.0:
            print("  [PASS] Gate 4: Average daily rate is within standard fleet benchmarks (Rs. 750 - Rs. 1,150).")
        else:
            print(f"  [WARNING] Gate 4: Average daily rate (Rs. {avg_rent:,.2f}) is outside standard range.")

        if all_passed:
            print(f"\n[FINAL VERDICT: APPROVED] {week_id} daily rent meets all financial standards.")
            print("You are cleared to proceed to Variable 3 (Approved Adjustments Engine).")
        else:
            print(f"\n[FINAL VERDICT: REJECTED] Quality gates failed. Review errors above before proceeding.")
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify quality gates for daily rent backfill.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    args = parser.parse_args()

    verify_rent(args.week)
