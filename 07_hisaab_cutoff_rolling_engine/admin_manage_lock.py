"""
admin_manage_lock.py
=============================================================================
Command-line utility for Finance & Engineering to temporarily unlock or lock
settlement weeks in LetzRyd Hisaab Engine.

Usage:
  # Check lock status of a week:
  python admin_manage_lock.py status CY26WK41

  # Temporarily unlock a week for 2 hours (default):
  python admin_manage_lock.py unlock CY26WK41 --hours 2 --user "finance_lead" --reason "Revising adjustments"

  # Immediately re-lock a week:
  python admin_manage_lock.py lock CY26WK41 --user "finance_lead" --reason "Adjustments verified"
=============================================================================
"""

import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import SessionLocal
from sqlalchemy import text


def get_status(week_id: str):
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT 
                week_id,
                settlement_year,
                settlement_week,
                week_start,
                week_end,
                lock_cutoff_at,
                is_locked,
                locked_at,
                locked_by,
                notes
            FROM public.hisaab_settlement_weeks
            WHERE week_id = :w;
        """), {'w': week_id}).fetchone()

        if not row:
            print(f"[ERROR] Week {week_id} not found in public.hisaab_settlement_weeks.")
            return

        print("\n" + "=" * 70)
        print(f"       SETTLEMENT WEEK LOCK STATUS: {week_id}")
        print("=" * 70)
        print(f"  Week Dates:       {row[3]} to {row[4]}")
        print(f"  Lock Cutoff:      {row[5]}")
        print(f"  Is Locked:        {'LOCKED (IMMUTABLE)' if row[6] else 'OPEN (ACTIVE)'}")
        if row[6]:
            print(f"  Locked At:        {row[7]}")
            print(f"  Locked By:        {row[8]}")
        print(f"  Audit Notes:      {row[9]}")
        print("=" * 70 + "\n")
    finally:
        db.close()


def unlock_week(week_id: str, hours: int, user: str, reason: str):
    db = SessionLocal()
    try:
        print(f"\n[ACTION] Unlocking {week_id} for {hours} hours...")
        db.execute(text("""
            CALL public.sp_admin_unlock_settlement_week(
                :w, 
                :h, 
                :u, 
                :r
            );
        """), {'w': week_id, 'h': hours, 'u': user, 'r': reason})
        db.commit()
        print(f"[SUCCESS] Week {week_id} successfully unlocked for {hours} hours.")
        get_status(week_id)
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Failed to unlock week: {e}")
    finally:
        db.close()


def lock_week(week_id: str, user: str, reason: str):
    db = SessionLocal()
    try:
        print(f"\n[ACTION] Locking {week_id}...")
        db.execute(text("""
            CALL public.sp_admin_lock_settlement_week(
                :w, 
                :u, 
                :r
            );
        """), {'w': week_id, 'u': user, 'r': reason})
        db.commit()
        print(f"[SUCCESS] Week {week_id} successfully locked.")
        get_status(week_id)
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Failed to lock week: {e}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Administrative Settlement Week Lock Controls")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Check lock status of a week")
    p_status.add_argument("week_id", help="Week ID (e.g. CY26WK41)")

    # unlock
    p_unlock = subparsers.add_parser("unlock", help="Temporarily unlock a week")
    p_unlock.add_argument("week_id", help="Week ID (e.g. CY26WK41)")
    p_unlock.add_argument("--hours", type=int, default=2, help="Unlock duration in hours (default: 2)")
    p_unlock.add_argument("--user", default="finance_admin", help="Admin user name")
    p_unlock.add_argument("--reason", default="Temporary administrative review", help="Reason for unlock")

    # lock
    p_lock = subparsers.add_parser("lock", help="Immediately lock a week")
    p_lock.add_argument("week_id", help="Week ID (e.g. CY26WK41)")
    p_lock.add_argument("--user", default="finance_admin", help="Admin user name")
    p_lock.add_argument("--reason", default="Manual administrative freeze", help="Reason for lock")

    args = parser.parse_args()

    if args.command == "status":
        get_status(args.week_id)
    elif args.command == "unlock":
        unlock_week(args.week_id, args.hours, args.user, args.reason)
    elif args.command == "lock":
        lock_week(args.week_id, args.user, args.reason)


if __name__ == "__main__":
    main()
