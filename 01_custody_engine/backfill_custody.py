"""
Backfill Custody Runner (Variable 1)
------------------------------------
Executes sp_refresh_vehicle_status_range for a historical settlement cycle.

Safety Protections:
- Hard-blocked against PROTECTED_PRODUCTION_WEEKS (W26, W27, W36-W41).
- Aborts if week is locked in hisaab_settlement_weeks.
- Runs in DRY-RUN mode by default (auto-rollback). Requires explicit --commit to persist.
- Performs pre- and post-run snapshot comparison to assert ZERO cross-contamination.

Usage:
    # Dry-Run (Safe, no database changes saved):
    python backfill_custody.py --week CY26WK28

    # Live Execution (Persists changes after safety verification):
    python backfill_custody.py --week CY26WK28 --commit
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard, SafetyViolationError


def run_custody_backfill(week_id: str, commit: bool = False, force_override: bool = False):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"CUSTODY BACKFILL RUNNER: {week_id}")
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

        # 3. Execution Phase
        print(f"\n[STEP 3/4] Calling sp_refresh_vehicle_status_range('{week_start}', '{week_end}')...")
        trans = conn.begin()
        try:
            conn.execute(
                text("CALL public.sp_refresh_vehicle_status_range(:start_d, :end_d)"),
                {"start_d": week_start, "end_d": week_end}
            )

            # Check rows generated for target week
            cnt = conn.execute(
                text("SELECT count(*) FROM public.core_daily_vehicle_status WHERE status_date BETWEEN :s AND :e"),
                {"s": week_start, "e": week_end}
            ).scalar()
            print(f"  * Processed: {cnt:,} daily vehicle status records for {week_id}.")

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
            print("\n[SUCCESS] Variable 1 backfill completed and 100% verified isolated!")
        else:
            print("\n[NOTE] Dry-run complete. Re-run with --commit when ready to persist.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill vehicle custody for historical cycles.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    parser.add_argument("--commit", action="store_true", help="Explicitly commit changes (default is dry-run)")
    parser.add_argument("--force-override-protected", action="store_true", help="DANGER: Override protection for production weeks")
    args = parser.parse_args()

    run_custody_backfill(args.week, commit=args.commit, force_override=args.force_override_protected)
