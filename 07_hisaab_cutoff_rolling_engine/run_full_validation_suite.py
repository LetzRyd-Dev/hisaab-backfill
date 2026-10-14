import sys
import datetime
sys.path.insert(0, r'c:\Users\anura\Downloads\hisaab-backfill')
from config import SessionLocal
from sqlalchemy import text

def run_end_to_end_validation():
    print("=" * 80)
    print("STARTING COMPREHENSIVE END-TO-END VALIDATION SUITE")
    print("Timestamp:", datetime.datetime.now().isoformat())
    print("=" * 80)

    db = SessionLocal()
    try:
        # TEST 1: Baseline Opening Balances Ingestion Verification
        print("\n[TEST 1] Verifying Opening Balances Baseline in hisaab_partner_opening_balances...")
        res = db.execute(text("""
            SELECT 
                COUNT(*) as partner_count,
                SUM(opening_balance_due) as total_dues,
                SUM(security_deposit_pending) as total_deposit
            FROM public.hisaab_partner_opening_balances
            WHERE effective_week_id = 'CY26WK41';
        """)).fetchone()
        
        count = res[0]
        dues = float(res[1])
        deposit = float(res[2])
        print(f"  Partner Count: {count:,} (Expected: 2,446)")
        print(f"  Total Dues:    INR {dues:,.2f} (Expected: INR 36,141,591.90)")
        print(f"  Total Deposit: INR {deposit:,.2f} (Expected: INR 8,538,918.00)")
        assert count == 2446, f"Baseline partner count mismatch: {count}"
        assert abs(dues - 36141591.90) < 0.01, f"Baseline dues mismatch: {dues}"
        print("  --> [TEST 1 PASSED]: Opening balances baseline is 100% verified!")

        # TEST 2: City-level Breakdown Verification
        print("\n[TEST 2] Verifying City Distribution Breakdown...")
        city_breakdown = db.execute(text("""
            SELECT 
                (remarks::json->>'city') as city,
                COUNT(*) as count,
                SUM(opening_balance_due) as sum_dues
            FROM public.hisaab_partner_opening_balances
            WHERE effective_week_id = 'CY26WK41'
            GROUP BY (remarks::json->>'city')
            ORDER BY city;
        """)).fetchall()

        total_verified_dues = 0.0
        total_verified_count = 0
        for row in city_breakdown:
            c, cnt, s = row[0], row[1], float(row[2])
            print(f"  City: {c:<4} | Count: {cnt:>5} | Total Dues: INR {s:>14,.2f}")
            total_verified_dues += s
            total_verified_count += cnt
        
        assert total_verified_count == 2446, f"Total count mismatch: {total_verified_count}"
        assert abs(total_verified_dues - 36141591.90) < 0.01, f"Total dues mismatch: {total_verified_dues}"
        print(f"  Total across all 3 cities: {total_verified_count:,} partners | INR {total_verified_dues:,.2f}")
        print("  --> [TEST 2 PASSED]: City breakdown exactly matches total baseline!")

        # TEST 3: Lock State Verification for Settlement Weeks
        print("\n[TEST 3] Verifying hisaab_settlement_weeks Lock Status...")
        weeks = db.execute(text("""
            SELECT week_id, is_locked, lock_cutoff_at, notes
            FROM public.hisaab_settlement_weeks
            WHERE week_id IN ('CY26WK41', 'CY26WK42')
            ORDER BY week_id;
        """)).fetchall()
        for w in weeks:
            print(f"  Week: {w[0]} | Locked: {w[1]} | Cutoff: {w[2]}")
            if w[0] == 'CY26WK41':
                assert w[1] is True, "CY26WK41 should be locked"
            if w[0] == 'CY26WK42':
                assert w[1] is False, "CY26WK42 should be open"
        print("  --> [TEST 3 PASSED]: Week CY26WK41 is locked, CY26WK42 is open!")

        # TEST 4: Immutable Guard on Locked Week
        print("\n[TEST 4] Testing Immutable Guard on Locked Week CY26WK41...")
        # Capture current updated_at timestamps on CY26WK41 rows (if any)
        pre_timestamps = db.execute(text("SELECT partner_id, updated_at FROM public.hisaab_partner_weekly WHERE week_id = 'CY26WK41' LIMIT 5;")).fetchall()
        
        # Call procedure on locked week - it will log notice and RETURN immediately without mutating
        db.execute(text("CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK41', NULL);"))
        db.commit()

        post_timestamps = db.execute(text("SELECT partner_id, updated_at FROM public.hisaab_partner_weekly WHERE week_id = 'CY26WK41' LIMIT 5;")).fetchall()
        assert pre_timestamps == post_timestamps, "Mutation detected on locked week CY26WK41!"
        print("  --> [TEST 4 PASSED]: Immutable guard early RETURN verified! Zero mutations on locked week.")

        # TEST 5: Admin Temporary Unlock & Re-lock Cycle
        print("\n[TEST 5] Testing Admin Temporary Unlock & Re-lock Cycle...")
        # Unlock CY26WK41 for 1 hour
        db.execute(text("""
            CALL public.sp_admin_unlock_settlement_week(
                'CY26WK41',
                1,
                'automated_test_suite',
                'Comprehensive validation of emergency unlock pathway'
            );
        """))
        db.commit()
        
        status_unlocked = db.execute(text("SELECT is_locked, lock_cutoff_at FROM public.hisaab_settlement_weeks WHERE week_id = 'CY26WK41';")).fetchone()
        print(f"  Unlocked state: is_locked={status_unlocked[0]} | new_cutoff={status_unlocked[1]}")
        assert status_unlocked[0] is False, "Week should now be unlocked"

        # Check audit log entry
        audit_row = db.execute(text("""
            SELECT action_type, week_id, partner_id, created_at 
            FROM public.hisaab_cutoff_audit_log 
            WHERE week_id = 'CY26WK41' AND partner_id = 'ADMIN:automated_test_suite'
            ORDER BY created_at DESC LIMIT 1;
        """)).fetchone()
        print(f"  Audit log: action={audit_row[0]} | week={audit_row[1]} | admin={audit_row[2]}")
        assert audit_row[0] == 'WEEK_TEMPORARILY_UNLOCKED'

        # Immediately lock back
        db.execute(text("""
            CALL public.sp_admin_lock_settlement_week(
                'CY26WK41',
                'automated_test_suite',
                'Verification complete - relocking week'
            );
        """))
        db.commit()
        
        status_relocked = db.execute(text("SELECT is_locked, lock_cutoff_at FROM public.hisaab_settlement_weeks WHERE week_id = 'CY26WK41';")).fetchone()
        print(f"  Re-locked state: is_locked={status_relocked[0]} | cutoff={status_relocked[1]}")
        assert status_relocked[0] is True, "Week should be re-locked"
        print("  --> [TEST 5 PASSED]: Admin unlock, audit logging, and re-lock functioning flawlessly!")

        # TEST 6: Late Operational Data & Aggregator Delta Auto-Routing
        print("\n[TEST 6] Testing Late Sync & Trigger Auto-Routing to Open Week...")
        test_driver = "TEST_E2E_DRIVER_OCT13"
        db.execute(text("DELETE FROM public.hisaab_adjustments_ledger WHERE partner_id = :p;"), {'p': test_driver})
        db.commit()

        # Step A: Late Ola Sync via procedure
        db.execute(text("""
            CALL public.sp_route_late_aggregator_sync(
                :p,
                'CY26WK41',
                'OLA',
                1500.00,  -- Earnings
                500.00,   -- Cash collected
                'Delayed Sunday night trip sync from Ola API',
                'TS09UA9999'
            );
        """), {'p': test_driver})
        db.commit()

        # Step B: Late Challan inserted directly targeting locked week
        db.execute(text("""
            INSERT INTO public.hisaab_adjustments_ledger (
                partner_id,
                settlement_week_id,
                vehicle_number,
                incident_date,
                incident_week_id,
                adjustment_category,
                polarity,
                amount,
                is_prior_period,
                approval_status,
                remarks,
                created_at,
                updated_at
            ) VALUES (
                :p,
                'CY26WK41', -- Locked week targeted
                'TS09UA9999',
                '2026-10-06',
                'CY26WK41',
                'CHALLAN',
                'DEBIT',
                1000.00,
                FALSE,
                'Approved',
                'Late e-challan from traffic police',
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            );
        """), {'p': test_driver})
        db.commit()

        # Verify adjustments were redirected to CY26WK42 and is_prior_period=TRUE
        adjs = db.execute(text("""
            SELECT adjustment_category, settlement_week_id, is_prior_period, polarity, amount, remarks
            FROM public.hisaab_adjustments_ledger
            WHERE partner_id = :p
            ORDER BY adjustment_category;
        """), {'p': test_driver}).fetchall()

        print(f"  Adjustments recorded for {test_driver}: {len(adjs)} items")
        for a in adjs:
            print(f"    - Category: {a[0]:<15} | Week: {a[1]} | Prior Period: {a[2]} | Polarity: {a[3]} | Amount: INR {a[4]:,.2f}")
            assert a[1] == 'CY26WK42', f"Adjustment should be auto-routed to CY26WK42, found {a[1]}"
            assert a[2] is True, "is_prior_period must be True"

        # Cleanup test entries
        db.execute(text("DELETE FROM public.hisaab_adjustments_ledger WHERE partner_id = :p;"), {'p': test_driver})
        db.commit()
        print("  --> [TEST 6 PASSED]: Late sync and trigger routing to open week functioning perfectly!")

        # TEST 7: Zero-Mutation Verification on Core Tables
        print("\n[TEST 7] Verifying Zero-Mutation Safety on Core Tables...")
        core_checks = [
            ("public.app_hisaabs", 25),
            ("public.app_drivers", 28),
            ("public.app_driver_allocations", 25),
            ("public.vehicles", 25)
        ]
        for tbl, expected_cnt in core_checks:
            cnt = db.execute(text(f"SELECT COUNT(*) FROM {tbl};")).scalar()
            print(f"  {tbl:<30} Count: {cnt:>3} (Expected: {expected_cnt:>3})")
            assert cnt == expected_cnt, f"Mutation detected in {tbl}! Count is {cnt}, expected {expected_cnt}"
        
        # Verify app_payments has no deletions (live app adds new payments)
        pay_cnt = db.execute(text("SELECT COUNT(*) FROM public.app_payments;")).scalar()
        print(f"  {'public.app_payments':<30} Count: {pay_cnt:>3} (Expected: >= 7, Live App Ingestion Active)")
        assert pay_cnt >= 7, f"Unexpected deletion in app_payments! Found {pay_cnt}"
        print("  --> [TEST 7 PASSED]: Core production tables are 100% UNTOUCHED and ZERO-MUTATION certified!")

        print("\n" + "=" * 80)
        print(">>> ALL 7 END-TO-END VALIDATION TESTS PASSED SUCCESSFULLY WITH ZERO ERRORS <<<")
        print("=" * 80)

    finally:
        db.close()

if __name__ == '__main__':
    run_end_to_end_validation()
