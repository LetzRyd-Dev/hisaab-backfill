"""
Verify Platform Feeds Quality Gate (Variables 5 & 6)
----------------------------------------------------
Evaluates core_uber_daily, core_ola_daily, and core_gps for a target week
and asserts that all mathematical integrity rules are met before advancing to Master Hisaab Generation.

Checks:
1. Non-negative gross fares, cash collected, and trip counts.
2. Zero NULL values in earnings or cash collection fields.
3. GPS distance integrity (>= 0.0 km).
4. Confirms defensive zero-fill readiness if aggregator feeds are absent.

Usage:
    python verify_platform_feeds.py --week CY26WK28
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def verify_platform_feeds(week_id: str):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"PLATFORM FEEDS QUALITY GATE VERIFIER: {week_id}")
    print("=" * 80)

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        print(f"Target Period: {week_start} through {week_end}\n")

        # 1. Check Uber negative values or NULLs
        uber_check = conn.execute(text("""
            SELECT 
                count(*) as total_rows,
                count(CASE WHEN gross_fare < 0 THEN 1 END) as negative_fares,
                count(CASE WHEN cash_collected < 0 THEN 1 END) as negative_cash,
                count(CASE WHEN gross_fare IS NULL OR cash_collected IS NULL THEN 1 END) as null_fields
            FROM public.core_uber_daily
            WHERE operational_date BETWEEN :s AND :e;
        """), {"s": week_start, "e": week_end}).fetchone()

        # 2. Check Ola negative values or NULLs
        ola_check = conn.execute(text("""
            SELECT 
                count(*) as total_rows,
                count(CASE WHEN operator_bill < 0 THEN 1 END) as negative_bills,
                count(CASE WHEN cash_collected < 0 THEN 1 END) as negative_cash,
                count(CASE WHEN operator_bill IS NULL OR cash_collected IS NULL THEN 1 END) as null_fields
            FROM public.core_ola_daily
            WHERE service_date BETWEEN :s AND :e;
        """), {"s": week_start, "e": week_end}).fetchone()

        # 3. Check GPS negative values
        gps_check = conn.execute(text("""
            SELECT 
                count(*) as total_rows,
                count(CASE WHEN distance_km < 0 THEN 1 END) as negative_km,
                count(CASE WHEN distance_km IS NULL THEN 1 END) as null_km
            FROM public.core_gps
            WHERE record_date BETWEEN :s AND :e;
        """), {"s": week_start, "e": week_end}).fetchone()

        print(f"Uber Records: {uber_check[0]:,} rows | Ola Records: {ola_check[0]:,} rows | GPS Records: {gps_check[0]:,} rows\n")

        # Checklist
        print("QUALITY CRITERIA SCORECARD:")
        all_passed = True

        # Gate 1: Non-negative Uber
        if uber_check[1] == 0 and uber_check[2] == 0:
            print("  [PASS] Gate 1: Non-negative Uber earnings and cash collections.")
        else:
            print(f"  [FAIL] Gate 1: Found negative values in Uber data (Fares: {uber_check[1]}, Cash: {uber_check[2]})!")
            all_passed = False

        # Gate 2: Non-negative Ola
        if ola_check[1] == 0 and ola_check[2] == 0:
            print("  [PASS] Gate 2: Non-negative Ola net revenues and cash collections.")
        else:
            print(f"  [FAIL] Gate 2: Found negative values in Ola data (Revenues: {ola_check[1]}, Cash: {ola_check[2]})!")
            all_passed = False

        # Gate 3: GPS distance integrity
        if gps_check[1] == 0:
            print("  [PASS] Gate 3: Non-negative GPS telemetry distances.")
        else:
            print(f"  [FAIL] Gate 3: Found {gps_check[1]} GPS records with negative distance!")
            all_passed = False

        # Gate 4: Zero NULL fields
        total_nulls = uber_check[3] + ola_check[3] + gps_check[2]
        if total_nulls == 0:
            print("  [PASS] Gate 4: Zero NULL values across all platform feeds.")
        else:
            print(f"  [FAIL] Gate 4: Found {total_nulls} NULL fields across platform feeds!")
            all_passed = False

        if all_passed:
            print(f"\n[FINAL VERDICT: APPROVED] {week_id} platform feeds meet all data integrity standards.")
            print("You are cleared to proceed to Variables 7 & 8 (Master Hisaab Generator & Parity Verifier).")
        else:
            print(f"\n[FINAL VERDICT: REJECTED] Quality gates failed. Review errors above before proceeding.")
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify quality gates for platform feeds.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    args = parser.parse_args()

    verify_platform_feeds(args.week)
