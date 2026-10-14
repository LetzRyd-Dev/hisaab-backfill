import sys
sys.path.insert(0, r'c:\Users\anura\Downloads\hisaab-backfill')
from config import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    print("Testing full Admin Unlock / Lock Cycle on CY26WK41...")

    # 1. Unlock week CY26WK41
    print("  * Calling sp_admin_unlock_settlement_week...")
    db.execute(text("CALL public.sp_admin_unlock_settlement_week('CY26WK41', 1, 'automated_test', 'Testing trigger unlock transition');"))
    db.commit()

    # Check status
    is_locked = db.execute(text("SELECT is_locked FROM public.hisaab_settlement_weeks WHERE week_id = 'CY26WK41'")).scalar()
    partner_statuses = db.execute(text("SELECT settlement_status, count(*) FROM public.hisaab_partner_weekly WHERE week_id = 'CY26WK41' GROUP BY settlement_status")).fetchall()
    print(f"  * Week CY26WK41 is_locked: {is_locked} (Expected: False)")
    print(f"  * Partner statuses after unlock: {partner_statuses}")
    assert is_locked == False, "Week was not unlocked!"

    # 2. Re-lock week CY26WK41
    print("  * Calling sp_admin_lock_settlement_week...")
    db.execute(text("CALL public.sp_admin_lock_settlement_week('CY26WK41', 'automated_test', 'Testing trigger lock transition');"))
    db.commit()

    is_locked_after = db.execute(text("SELECT is_locked FROM public.hisaab_settlement_weeks WHERE week_id = 'CY26WK41'")).scalar()
    partner_statuses_after = db.execute(text("SELECT settlement_status, count(*) FROM public.hisaab_partner_weekly WHERE week_id = 'CY26WK41' GROUP BY settlement_status")).fetchall()
    print(f"  * Week CY26WK41 is_locked: {is_locked_after} (Expected: True)")
    print(f"  * Partner statuses after lock: {partner_statuses_after}")
    assert is_locked_after == True, "Week was not re-locked!"

    print("\nADMIN UNLOCK / RELOCK CYCLE TEST PASSED 100%!")
finally:
    db.close()
