import os
import sys
import csv
sys.path.insert(0, r'c:\Users\anura\Downloads\hisaab-backfill')
from config import SessionLocal
from sqlalchemy import text

def load_baseline():
    csv_path = r'c:\Users\anura\Downloads\hisaab-backfill\07_hisaab_cutoff_rolling_engine\cutoff_baseline_oct12_snapshot.csv'
    print(f"Reading snapshot from {csv_path}...")

    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = list(csv.DictReader(f))

    print(f"Loaded {len(reader):,} rows from CSV. Starting database upsert...")

    db = SessionLocal()
    try:
        upsert_stmt = text("""
            INSERT INTO public.hisaab_partner_opening_balances (
                partner_id,
                driver_code,
                phone,
                effective_week_id,
                cutoff_date,
                cutoff_timestamp,
                opening_balance_due,
                security_deposit_pending,
                remarks,
                created_by,
                verified_by,
                is_active,
                created_at,
                updated_at
            ) VALUES (
                :partner_id,
                :driver_code,
                :phone,
                :effective_week_id,
                CAST(:cutoff_date AS date),
                CAST(:cutoff_timestamp AS timestamptz),
                :opening_balance_due,
                :security_deposit_pending,
                :remarks,
                :created_by,
                :created_by,
                TRUE,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (partner_id, effective_week_id) DO UPDATE SET
                opening_balance_due = EXCLUDED.opening_balance_due,
                security_deposit_pending = EXCLUDED.security_deposit_pending,
                driver_code = EXCLUDED.driver_code,
                phone = EXCLUDED.phone,
                cutoff_date = EXCLUDED.cutoff_date,
                cutoff_timestamp = EXCLUDED.cutoff_timestamp,
                remarks = EXCLUDED.remarks,
                updated_at = CURRENT_TIMESTAMP;
        """)

        for r in reader:
            db.execute(upsert_stmt, {
                'partner_id': r['partner_id'],
                'driver_code': r.get('driver_name', ''),
                'phone': r.get('phone', ''),
                'effective_week_id': r['effective_week_id'],
                'cutoff_date': r.get('cutoff_date', '2026-10-12'),
                'cutoff_timestamp': '2026-10-12 12:00:00+05:30',
                'opening_balance_due': float(r['opening_balance_due']),
                'security_deposit_pending': float(r.get('security_deposit_pending', 0.0)),
                'remarks': r['remarks'],
                'created_by': 'finance_admin_cutoff_oct12_etl'
            })

        db.commit()
        print(f"Successfully upserted {len(reader):,} rows into hisaab_partner_opening_balances!")

        # Verify
        check = db.execute(text("""
            SELECT 
                count(*), 
                sum(opening_balance_due), 
                sum(security_deposit_pending) 
            FROM public.hisaab_partner_opening_balances 
            WHERE effective_week_id = 'CY26WK41';
        """)).fetchone()

        print("\n=== POSTGRESQL VERIFICATION ===")
        print(f"  Count:                        {check[0]:,} (Expected: 2,446)")
        print(f"  Total Opening Balance Due:    INR {float(check[1]):,.2f} (Expected: INR 36,141,591.90)")
        print(f"  Total Security Deposit Due:   INR {float(check[2]):,.2f} (Expected: INR 8,538,918.00)")

    finally:
        db.close()

if __name__ == '__main__':
    load_baseline()
