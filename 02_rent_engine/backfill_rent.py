"""
Backfill Daily Rent Runner (Variable 2)
---------------------------------------
Executes sp_calculate_daily_rent for a historical settlement cycle.

Safety Protections:
- Hard-blocked against PROTECTED_PRODUCTION_WEEKS (W26, W27, W36-W41).
- Aborts if week is locked in hisaab_settlement_weeks.
- Asserts that Variable 1 custody records exist before calculating rent.
- Runs in DRY-RUN mode by default (auto-rollback). Requires explicit --commit to persist.
- Performs pre- and post-run snapshot comparison to assert ZERO cross-contamination.

Usage:
    # Dry-Run (Safe, no database changes saved):
    python backfill_rent.py --week CY26WK28

    # Live Execution (Persists changes after safety verification):
    python backfill_rent.py --week CY26WK28 --commit
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard, SafetyViolationError


def run_rent_backfill(week_id: str, commit: bool = False, force_override: bool = False):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"RENT BACKFILL RUNNER: {week_id}")
    print(f"Execution Mode: {'LIVE (PERSIST)' if commit else 'DRY-RUN (SAFE ROLLBACK)'}")
    print("=" * 80)

    with engine.connect() as conn:
        # 1. Pre-flight Safety Assertions
        print("\n[STEP 1/4] Running Pre-Flight Safety Checks...")
        try:
            guard.assert_safe_to_run(conn, week_id, force_override_protected=force_override)
        except SafetyViolationError as e:
            print(f"\n[FATAL SAFETY ABORT] {e}")
            sys.exit(1)

        # 2. Capture baseline snapshot of protected weeks
        print("\n[STEP 2/4] Taking Protected Production Weeks Snapshot...")
        initial_snapshot = guard.take_snapshot(conn)
        print(f"  * Checksum recorded across {len(initial_snapshot)} production settlement tables.")

        # Resolve dates
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        print(f"  * Target Date Range: {week_start} through {week_end}")

        # Check prerequisite: Variable 1 custody presence
        custody_cnt = conn.execute(
            text("SELECT count(*) FROM public.core_daily_vehicle_status WHERE status_date BETWEEN :s AND :e"),
            {"s": week_start, "e": week_end}
        ).scalar()
        if custody_cnt == 0:
            print(f"\n[PREREQUISITE ERROR] Zero records found in core_daily_vehicle_status for {week_id}!")
            print("Action Required: You must run Variable 1 custody backfill before calculating rent.")
            sys.exit(1)
        print(f"  * Prerequisite Check: Found {custody_cnt:,} custody records in core_daily_vehicle_status.")

        # 3. Execution Phase
        print(f"\n[STEP 3/4] Calling sp_calculate_daily_rent('{week_start}', '{week_end}')...")
        trans = conn.begin()
        try:
            conn.execute(
                text("CALL public.sp_calculate_daily_rent(:start_d, :end_d)"),
                {"start_d": week_start, "end_d": week_end}
            )

            # Check rows generated in daily_rent_log
            cnt = conn.execute(
                text("SELECT count(*) FROM public.daily_rent_log WHERE log_date BETWEEN :s AND :e"),
                {"s": week_start, "e": week_end}
            ).scalar()
            total_rent = conn.execute(
                text("SELECT sum(net_daily_rent) FROM public.daily_rent_log WHERE log_date BETWEEN :s AND :e"),
                {"s": week_start, "e": week_end}
            ).scalar() or 0.0

            print(f"  * Processed: {cnt:,} daily rent records for {week_id}.")
            print(f"  * Total Lease Rent Billed: Rs. {total_rent:,.2f}")

            if commit:
                trans.commit()
                print("\n  * [DATABASE TRANSACTION COMMITTED]")
            else:
                trans.rollback()
                print("\n  * [DRY-RUN MODE: TRANSACTION SAFELY ROLLED BACK (No rows persisted)]")

        except Exception as e:
            trans.rollback()
            print(f"\n[ERROR] Execution failed: {e}")
            raise

        # 4. Post-Flight Isolation Verification
        if commit:
            print("\n[STEP 4/4] Running Post-Flight Cross-Contamination Assertion...")
            guard.verify_no_cross_contamination(conn, initial_snapshot)
            print("\n[SUCCESS] Variable 2 rent backfill completed and 100% verified isolated!")
        else:
            print("\n[NOTE] Dry-run complete. Re-run with --commit when ready to persist.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill daily rent for historical cycles.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    parser.add_argument("--commit", action="store_true", help="Explicitly commit changes (default is dry-run)")
    parser.add_argument("--force-override-protected", action="store_true", help="DANGER: Override protection for production weeks")
    args = parser.parse_args()

    run_rent_backfill(args.week, commit=args.commit, force_override=args.force_override_protected)
