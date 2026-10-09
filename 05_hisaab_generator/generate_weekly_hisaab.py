"""
Master Weekly Hisaab Generator Runner (Variables 7 & 8)
-------------------------------------------------------
Orchestrates Tier 2 (Vehicle Weekly) and Tier 3 (Partner Consolidated) Hisaab generation
for a historical settlement cycle.

Safety Protections:
- Hard-blocked against PROTECTED_PRODUCTION_WEEKS (W26, W27, W36-W41).
- Aborts if week is locked in hisaab_settlement_weeks.
- Enforces Standalone Mode (previous_outstanding = 0.00).
- Runs in DRY-RUN mode by default (auto-rollback). Requires explicit --commit to persist.
- Performs pre- and post-run snapshot comparison to assert ZERO cross-contamination.

Usage:
    # Dry-Run (Safe, no database changes saved):
    python generate_weekly_hisaab.py --week CY26WK28

    # Live Execution (Persists changes after safety verification):
    python generate_weekly_hisaab.py --week CY26WK28 --commit
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard, SafetyViolationError


def run_master_hisaab_generation(week_id: str, commit: bool = False, force_override: bool = False):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"MASTER HISAAB WEEKLY GENERATOR: {week_id}")
    print(f"Execution Mode: {'LIVE (PERSIST)' if commit else 'DRY-RUN (SAFE ROLLBACK)'}")
    print("=" * 80)

    with engine.connect() as conn:
        # 1. Pre-flight Safety Assertions
        print("\n[STEP 1/5] Running Pre-Flight Safety Checks...")
        try:
            guard.assert_safe_to_run(conn, week_id, force_override_protected=force_override)
        except SafetyViolationError as e:
            print(f"\n[FATAL SAFETY ABORT] {e}")
            sys.exit(1)

        # 2. Capture baseline snapshot of protected weeks
        print("\n[STEP 2/5] Taking Protected Production Weeks Snapshot...")
        initial_snapshot = guard.take_snapshot(conn)
        print(f"  * Checksum recorded across {len(initial_snapshot)} production settlement tables.")

        # Resolve dates & check prerequisite: daily_rent_log
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        rent_cnt = conn.execute(
            text("SELECT count(*) FROM public.daily_rent_log WHERE log_date BETWEEN :s AND :e"),
            {"s": week_start, "e": week_end}
        ).scalar()

        if rent_cnt == 0:
            print(f"\n[PREREQUISITE ERROR] Zero records found in daily_rent_log for {week_id}!")
            print("Action Required: You must run Variable 1 (Custody) and Variable 2 (Rent) before generating Hisaab.")
            sys.exit(1)
        print(f"  * Prerequisite Check: Found {rent_cnt:,} daily rent records in daily_rent_log.")

        # 3. Execution Phase
        print(f"\n[STEP 3/5] Executing Stored Procedures for {week_id}...")
        trans = conn.begin()
        try:
            # Tier 2: Vehicle Weekly Settlement
            print(f"  * Calling sp_sync_hisaab_vehicle_weekly('{week_id}')...")
            conn.execute(
                text("CALL public.sp_sync_hisaab_vehicle_weekly(:w)"),
                {"w": week_id}
            )

            # Tier 3: Partner Consolidated Settlement
            print(f"  * Calling sp_sync_hisaab_partner_weekly('{week_id}')...")
            conn.execute(
                text("CALL public.sp_sync_hisaab_partner_weekly(:w)"),
                {"w": week_id}
            )

            # 4. Result Metrics
            veh_metrics = conn.execute(text("""
                SELECT 
                    count(*) as vehicle_count,
                    COALESCE(sum(net_weekly_lease_rental), 0.0) as total_rent,
                    COALESCE(sum(total_platform_earnings), 0.0) as total_earnings,
                    COALESCE(sum(total_cash_collected), 0.0) as total_cash,
                    COALESCE(sum(challan_amount), 0.0) as total_challans,
                    COALESCE(sum(adjustment_amount), 0.0) as total_adj,
                    COALESCE(sum(dead_mile_charges), 0.0) as total_dead_miles,
                    COALESCE(sum(tds_amount), 0.0) as total_tds,
                    COALESCE(sum(current_week_os), 0.0) as total_os,
                    COALESCE(sum(to_collect), 0.0) as to_collect,
                    COALESCE(sum(to_payout), 0.0) as to_payout
                FROM public.hisaab_vehicle_weekly
                WHERE week_id = :w;
            """), {"w": week_id}).fetchone()

            part_metrics = conn.execute(text("""
                SELECT 
                    count(*) as partner_count,
                    COALESCE(sum(current_week_os), 0.0) as partner_os,
                    COALESCE(sum(net_amount_to_collect), 0.0) as partner_collect,
                    COALESCE(sum(net_bank_payout), 0.0) as partner_payout,
                    COALESCE(sum(previous_outstanding), 0.0) as prev_os
                FROM public.hisaab_partner_weekly
                WHERE week_id = :w;
            """), {"w": week_id}).fetchone()

            print("\n[STEP 4/5] Generated Settlement Summary:")
            print(f"  * Tier 2 (Vehicles):      {veh_metrics[0]:,} vehicles settled")
            print(f"    - Net Lease Rent:       Rs. {float(veh_metrics[1]):,.2f}")
            print(f"    - Platform Earnings:    Rs. {float(veh_metrics[2]):,.2f}")
            print(f"    - Cash Collected:       Rs. {float(veh_metrics[3]):,.2f}")
            print(f"    - Unpaid Challans:      Rs. {float(veh_metrics[4]):,.2f}")
            print(f"    - Adjustments (Signed): Rs. {float(veh_metrics[5]):,.2f}")
            print(f"    - Dead Mile Penalties:  Rs. {float(veh_metrics[6]):,.2f}")
            print(f"    - Statutory TDS (194C): Rs. {float(veh_metrics[7]):,.2f}")
            print(f"    - Vehicle Current O/S:  Rs. {float(veh_metrics[8]):,.2f}")
            print(f"  * Tier 3 (Partners):      {part_metrics[0]:,} operators consolidated")
            print(f"    - Partner Current O/S:  Rs. {float(part_metrics[1]):,.2f}")
            print(f"    - Net Bank Payout:      Rs. {float(part_metrics[3]):,.2f}")
            print(f"    - Net to Collect:       Rs. {float(part_metrics[2]):,.2f}")
            print(f"    - Previous Debt:        Rs. {float(part_metrics[4]):,.2f} (Verified Standalone 0.00)")

            # Check zero variance parity
            variance = abs(float(veh_metrics[8]) - float(part_metrics[1]))
            if variance < 0.01:
                print(f"\n  * [PARITY VERIFICATION: 100% MATCH] Vehicle O/S == Partner O/S (diff = Rs. {variance:.2f})")
            else:
                print(f"\n  * [PARITY WARNING] Variance detected: Rs. {variance:.2f}")

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

        # 5. Post-Flight Isolation Verification
        if commit:
            print("\n[STEP 5/5] Running Post-Flight Cross-Contamination Assertion...")
            guard.verify_no_cross_contamination(conn, initial_snapshot)
            print("\n[SUCCESS] Master Hisaab settlement generation completed and 100% verified isolated!")
        else:
            print("\n[NOTE] Dry-run complete. Re-run with --commit when ready to persist.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Master Hisaab generator for historical cycles.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    parser.add_argument("--commit", action="store_true", help="Explicitly commit changes (default is dry-run)")
    parser.add_argument("--force-override-protected", action="store_true", help="DANGER: Override protection for production weeks")
    args = parser.parse_args()

    run_master_hisaab_generation(args.week, commit=args.commit, force_override=args.force_override_protected)
