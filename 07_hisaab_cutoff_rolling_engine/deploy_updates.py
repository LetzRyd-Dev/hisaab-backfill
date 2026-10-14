import sys
sys.path.insert(0, r'c:\Users\anura\Downloads\hisaab-backfill')
from config import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    files = [
        r'c:\Users\anura\Downloads\hisaab-backfill\07_hisaab_cutoff_rolling_engine\fix_prevent_locked_trigger.sql',
        r'c:\Users\anura\Downloads\hisaab-backfill\07_hisaab_cutoff_rolling_engine\sp_admin_settlement_week_lock_controls.sql',
        r'c:\Users\anura\Downloads\hisaab-backfill\07_hisaab_cutoff_rolling_engine\sp_sync_hisaab_cumulative_cutoff.sql'
    ]
    for fpath in files:
        print(f"Deploying {fpath}...")
        with open(fpath, 'r', encoding='utf-8') as f:
            sql = f.read()
        db.execute(text(sql))
        db.commit()
        print("  --> Deployed successfully!")

    print("\nALL PROCEDURES AND TRIGGERS DEPLOYED CLEANLY!")
finally:
    db.close()
