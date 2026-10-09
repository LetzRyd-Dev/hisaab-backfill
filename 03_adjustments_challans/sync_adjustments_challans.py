"""
Sync Adjustments & Traffic Challans Runner (Variables 3 & 4)
-----------------------------------------------------------
Synchronizes approved adjustments into hisaab_adjustments_ledger for a historical cycle.

Safety Protections:
- Hard-blocked against PROTECTED_PRODUCTION_WEEKS (W26, W27, W36-W41).
- Aborts if week is locked in hisaab_settlement_weeks.
- Strictly filters for approval_status = 'Approved'.
- Runs in DRY-RUN mode by default (auto-rollback). Requires explicit --commit to persist.
- Performs pre- and post-run snapshot comparison to assert ZERO cross-contamination.

Usage:
    # Dry-Run (Safe, no database changes saved):
    python sync_adjustments_challans.py --week CY26WK28

    # Live Execution (Persists changes after safety verification):
    python sync_adjustments_challans.py --week CY26WK28 --commit
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard, SafetyViolationError


def run_adjustments_sync(week_id: str, commit: bool = False, force_override: bool = False):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"ADJUSTMENTS & CHALLANS SYNC RUNNER: {week_id}")
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
        print(f"\n[STEP 3/4] Syncing Approved Adjustments into hisaab_adjustments_ledger for {week_id}...")
        trans = conn.begin()
        try:
            # Sync approved adjustments
            sync_sql = text("""
            INSERT INTO public.hisaab_adjustments_ledger (
                incident_date,
                settlement_week_id,
                vehicle_number,
                partner_id,
                adjustment_category,
                polarity,
                amount,
                remarks,
                is_prior_period,
                approval_status,
                effective_date,
                created_at,
                updated_at
            )
            SELECT 
                ca.adjustment_date AS incident_date,
                :week_id AS settlement_week_id,
                UPPER(REPLACE(REPLACE(ca.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
                ca.partner_id,
                COALESCE(ca.adjustment_related_to, ca.adjustment_type, 'General Adjustment') AS adjustment_category,
                ca.polarity,
                ca.amount,
                'HIST_BACKFILL:' || ca.adjustment_id || ' ' || COALESCE(ca.remarks, '') AS remarks,
                FALSE AS is_prior_period,
                ca.approval_status,
                ca.adjustment_date AS effective_date,
                NOW(),
                NOW()
            FROM public.core_adjustments ca
            WHERE ca.adjustment_date BETWEEN :s AND :e
              AND ca.approval_status = 'Approved'
              AND ca.is_deleted = FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM public.hisaab_adjustments_ledger hal
                  WHERE hal.remarks LIKE 'HIST_BACKFILL:' || ca.adjustment_id || '%'
              );
            """)

            res = conn.execute(sync_sql, {"week_id": week_id, "s": week_start, "e": week_end})
            inserted_count = res.rowcount

            # Check total synced
            total_active = conn.execute(
                text("SELECT count(*) FROM public.hisaab_adjustments_ledger WHERE settlement_week_id = :w"),
                {"w": week_id}
            ).scalar()

            print(f"  * Newly Ingested Adjustments: {inserted_count:,} records.")
            print(f"  * Total Active Ledger Rows for {week_id}: {total_active:,} records.")

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
            print("\n[SUCCESS] Variables 3 & 4 sync completed and 100% verified isolated!")
        else:
            print("\n[NOTE] Dry-run complete. Re-run with --commit when ready to persist.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync approved adjustments and challans for historical cycles.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    parser.add_argument("--commit", action="store_true", help="Explicitly commit changes (default is dry-run)")
    parser.add_argument("--force-override-protected", action="store_true", help="DANGER: Override protection for production weeks")
    args = parser.parse_args()

    run_adjustments_sync(args.week, commit=args.commit, force_override=args.force_override_protected)
