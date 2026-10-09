"""
Lock Settlement Week Runner (Immutability Enforcer)
---------------------------------------------------
Transitions a finalized backfilled settlement cycle to LOCKED state
via sp_check_and_enforce_monday_lock to prevent any subsequent accidental modifications.

Safety Protections:
- Requires explicit --confirm flag.
- Verifies that settlement rows exist before locking.

Usage:
    python lock_settlement_week.py --week CY26WK28 --confirm
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration & safety guard
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def lock_week(week_id: str, confirm: bool = False):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"SETTLEMENT WEEK IMMUTABILITY LOCK RUNNER: {week_id}")
    print("=" * 80)

    if not confirm:
        print("[ABORT] You must pass --confirm to lock a settlement week.")
        print("Note: Locking renders the week permanent and immutable.")
        sys.exit(1)

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        if is_locked:
            print(f"[NOTE] Week {week_id} is ALREADY locked in hisaab_settlement_weeks.")
            return

        # Check that records exist
        cnt = conn.execute(
            text("SELECT count(*) FROM public.hisaab_vehicle_weekly WHERE week_id = :w"),
            {"w": week_id}
        ).scalar()

        if cnt == 0:
            print(f"[ERROR] Cannot lock week {week_id}: Zero records in hisaab_vehicle_weekly!")
            sys.exit(1)

        print(f"Enforcing Monday Hard Lock on {week_id} ({cnt:,} vehicle records)...")
        trans = conn.begin()
        try:
            conn.execute(
                text("CALL public.sp_check_and_enforce_monday_lock(:w, 'BACKFILL_ADMIN')"),
                {"w": week_id}
            )
            trans.commit()
            print(f"\n[SUCCESS] Week {week_id} is now permanently LOCKED and immutable in PostgreSQL.")
        except Exception as e:
            trans.rollback()
            print(f"[ERROR] Failed to lock week: {e}")
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lock a finalized backfilled settlement cycle.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    parser.add_argument("--confirm", action="store_true", help="Explicit confirmation to lock the week")
    args = parser.parse_args()

    lock_week(args.week, confirm=args.confirm)
