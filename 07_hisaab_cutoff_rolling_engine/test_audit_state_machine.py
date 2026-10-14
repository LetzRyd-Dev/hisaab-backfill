import sys
from pathlib import Path
sys.path.insert(0, r'c:\Users\anura\Downloads\hisaab-backfill')
from config import SessionLocal
from sqlalchemy import text
from decimal import Decimal

def run_audit():
    print("=" * 80)
    print("SUBAGENT 3: MULTI-WEEK ROLL-FORWARD & STATE MACHINE DEEP AUDIT")
    print("=" * 80)
    
    db = SessionLocal()
    try:
        # We run tests inside a transaction and ROLLBACK at the end so we don't pollute prod data
        # Note: PostgreSQL supports transactional DDL and procedure calls!
        trans = db.begin()

        # Clean any prior test remnants
        test_partners = ['AUDIT_DRV_INACT', 'AUDIT_DRV_DEP_ONLY', 'AUDIT_DRV_CREDIT', 'AUDIT_OP_FLEET', 'AUDIT_DRV_SWAP']
        db.execute(text("DELETE FROM public.hisaab_partner_weekly WHERE partner_id = ANY(:p);"), {"p": test_partners})
        db.execute(text("DELETE FROM public.hisaab_vehicle_weekly WHERE partner_id = ANY(:p);"), {"p": test_partners})
        db.execute(text("DELETE FROM public.hisaab_partner_opening_balances WHERE partner_id = ANY(:p);"), {"p": test_partners})
        db.execute(text("DELETE FROM public.hisaab_adjustments_ledger WHERE partner_id = ANY(:p);"), {"p": test_partners})
        db.execute(text("DELETE FROM public.hisaab_settlement_weeks WHERE week_id IN ('CY26WK43', 'CY26WK44');"))

        # Setup test settlement weeks
        # CY26WK41: Cutoff baseline (locked)
        # CY26WK42: Active/Locked
        # CY26WK43: Subsequent rolling week
        # CY26WK44: Subsequent rolling week
        db.execute(text("""
            INSERT INTO public.hisaab_settlement_weeks (week_id, settlement_year, settlement_week, week_start, week_end, lock_cutoff_at, is_locked)
            VALUES 
                ('CY26WK43', 2026, 43, '2026-10-19', '2026-10-25', '2026-10-26 12:00:00+05:30', FALSE),
                ('CY26WK44', 2026, 44, '2026-10-26', '2026-11-01', '2026-11-02 12:00:00+05:30', FALSE)
            ON CONFLICT (week_id) DO NOTHING;
        """))

        # ---------------------------------------------------------------------
        # TEST 1: What happens if a partner was inactive in W42 (debt carried from W41)?
        # Does their debt roll forward into W43?
        # ---------------------------------------------------------------------
        print("\n--- TEST 1: Inactive Partner Debt Roll-forward (W41 -> W42 -> W43) ---")
        # In W41: opening balance ₹5,000
        db.execute(text("""
            INSERT INTO public.hisaab_partner_opening_balances (partner_id, effective_week_id, opening_balance_due, security_deposit_pending, remarks)
            VALUES ('AUDIT_DRV_INACT', 'CY26WK41', 5000.00, 2000.00, '{"name": "Inactive Driver", "city": "HYD", "type": "Individual"}');
        """))

        # Sync W42 for AUDIT_DRV_INACT (no vehicle trips in W42)
        db.execute(text("CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK42', 'AUDIT_DRV_INACT');"))
        w42_row = db.execute(text("SELECT total_outstanding, pending_deposit, previous_outstanding FROM public.hisaab_partner_weekly WHERE week_id = 'CY26WK42' AND partner_id = 'AUDIT_DRV_INACT';")).fetchone()
        print(f"  W42 Result: prev_os={w42_row[2]}, total_os={w42_row[0]}, pending_dep={w42_row[1]}")

        # Now sync W43 (partner still inactive in W43)
        db.execute(text("CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK43', 'AUDIT_DRV_INACT');"))
        w43_row = db.execute(text("SELECT total_outstanding, pending_deposit, previous_outstanding FROM public.hisaab_partner_weekly WHERE week_id = 'CY26WK43' AND partner_id = 'AUDIT_DRV_INACT';")).fetchone()
        print(f"  W43 Result: prev_os={w43_row[2] if w43_row else None}, total_os={w43_row[0] if w43_row else None}, pending_dep={w43_row[1] if w43_row else None}")
        if w43_row and w43_row[0] == Decimal('5000.00'):
            print("  --> Inactive partner with debt rolls forward into W43 successfully.")
        else:
            print("  --> BUG: Inactive partner with debt failed to roll forward into W43!")

        # ---------------------------------------------------------------------
        # TEST 2: Partner with total_outstanding = 0.00 but pending_deposit > 0.00
        # Check lines 218-222: Will they be dropped in Week 43?
        # ---------------------------------------------------------------------
        print("\n--- TEST 2: Partner with total_outstanding == 0.00 but pending_deposit > 0.00 in W42 ---")
        # In W42: AUDIT_DRV_DEP_ONLY has 0 outstanding, but owes 3000 pending deposit
        db.execute(text("""
            INSERT INTO public.hisaab_partner_weekly (
                settlement_year, settlement_week, week_id, week_start, week_end, partner_id, partner_name, partner_type, city,
                allotted_cars_count, total_onroad_days, total_trips, total_net_rent_billed, total_platform_earnings, total_cash_collected,
                total_platform_incentives, total_adjustments, total_challans, total_accidents, total_tds, total_dead_mile_penalty,
                current_week_os, previous_outstanding, amount_paid_during_week, prior_period_adjustments,
                security_deposit_target, security_deposit_paid, deposit_deduction_current_week, pending_deposit,
                total_outstanding, net_bank_payout, net_amount_to_collect, settlement_status
            ) VALUES (
                2026, 42, 'CY26WK42', '2026-10-12', '2026-10-18', 'AUDIT_DRV_DEP_ONLY', 'Deposit Only Driver', 'Individual', 'HYD',
                0, 0, 0, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00,
                0.00, 0.00, 0.00, 0.00,
                5000.00, 2000.00, 0.00, 3000.00,
                0.00, 0.00, 0.00, 'SETTLED'
            ) ON CONFLICT (week_id, partner_id) DO UPDATE SET total_outstanding = 0.00, pending_deposit = 3000.00;
        """))

        # Now sync W43 for AUDIT_DRV_DEP_ONLY (inactive in W43)
        db.execute(text("CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK43', 'AUDIT_DRV_DEP_ONLY');"))
        w43_dep = db.execute(text("SELECT total_outstanding, pending_deposit FROM public.hisaab_partner_weekly WHERE week_id = 'CY26WK43' AND partner_id = 'AUDIT_DRV_DEP_ONLY';")).fetchone()
        print(f"  W43 Result for AUDIT_DRV_DEP_ONLY: {w43_dep}")
        if w43_dep is None:
            print("  --> CRITICAL BUG CONFIRMED: Partner with total_outstanding = 0.00 and pending_deposit > 0.00 was DROPPED in Week 43!")
            print("      Root Cause: Line 221 only filters `total_outstanding <> 0.00`, omitting partners who only have pending deposit!")
        else:
            print("  --> Partner was preserved with pending_deposit:", w43_dep[1])

        # ---------------------------------------------------------------------
        # TEST 3: Negative Balance (Credit) in W42 - Does it roll forward to W43 as negative previous_outstanding?
        # ---------------------------------------------------------------------
        print("\n--- TEST 3: Negative Balance (Advance / Credit) in W42 ---")
        # In W42: AUDIT_DRV_CREDIT has total_outstanding = -1500.00 (credit/payout due)
        db.execute(text("""
            INSERT INTO public.hisaab_partner_weekly (
                settlement_year, settlement_week, week_id, week_start, week_end, partner_id, partner_name, partner_type, city,
                allotted_cars_count, total_onroad_days, total_trips, total_net_rent_billed, total_platform_earnings, total_cash_collected,
                total_platform_incentives, total_adjustments, total_challans, total_accidents, total_tds, total_dead_mile_penalty,
                current_week_os, previous_outstanding, amount_paid_during_week, prior_period_adjustments,
                security_deposit_target, security_deposit_paid, deposit_deduction_current_week, pending_deposit,
                total_outstanding, net_bank_payout, net_amount_to_collect, settlement_status
            ) VALUES (
                2026, 42, 'CY26WK42', '2026-10-12', '2026-10-18', 'AUDIT_DRV_CREDIT', 'Credit Driver', 'Individual', 'HYD',
                1, 6, 40, 5000.00, 6500.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00,
                -1500.00, 0.00, 0.00, 0.00,
                5000.00, 5000.00, 0.00, 0.00,
                -1500.00, 1500.00, 0.00, 'OPEN'
            ) ON CONFLICT (week_id, partner_id) DO UPDATE SET total_outstanding = -1500.00;
        """))

        # In W43: Partner drives and incurs Rs 1000 rent debt (current_week_os = +1000.00)
        db.execute(text("""
            INSERT INTO public.hisaab_vehicle_weekly (
                week_id, week_start, week_end, vehicle_number, partner_id, partner_name, city,
                allotted_days, onroad_days, daily_rent_applied, weekly_lease_rental, weekly_indemnity_fees, net_weekly_lease_rental,
                uber_trips, uber_total_earnings, uber_cash_collection, uber_toll, uber_driver_sub_charge, uber_incentive, uber_week_os,
                ola_trips, ola_net_revenue, ola_cash_collection, ola_toll, ola_gst, ola_online_payment, ola_incentive, ola_week_os,
                current_week_os, settlement_status
            ) VALUES (
                'CY26WK43', '2026-10-19', '2026-10-25', 'TS09UB9001', 'AUDIT_DRV_CREDIT', 'Credit Driver', 'HYD',
                6, 6, 600, 3600, 400, 4000,
                20, 3000, 0, 0, 0, 0, 1000,
                0, 0, 0, 0, 0, 0, 0, 0,
                1000.00, 'OPEN'
            );
        """))

        # Sync W43
        db.execute(text("CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK43', 'AUDIT_DRV_CREDIT');"))
        w43_cred = db.execute(text("""
            SELECT previous_outstanding, current_week_os, total_outstanding, net_bank_payout, net_amount_to_collect, settlement_status
            FROM public.hisaab_partner_weekly 
            WHERE week_id = 'CY26WK43' AND partner_id = 'AUDIT_DRV_CREDIT';
        """)).fetchone()
        print(f"  W43 Result for AUDIT_DRV_CREDIT:")
        print(f"    prev_os={w43_cred[0]}, cur_os={w43_cred[1]}, total_os={w43_cred[2]}, payout={w43_cred[3]}, to_collect={w43_cred[4]}, status={w43_cred[5]}")
        # Expected:
        # prev_os = -1500.00
        # cur_os = +1000.00
        # total_os = -500.00
        # net_bank_payout = 500.00, net_amount_to_collect = 0.00
        if w43_cred[0] == Decimal('-1500.00') and w43_cred[2] == Decimal('-500.00') and w43_cred[3] == Decimal('500.00'):
            print("  --> PASS: Negative credit rolled forward seamlessly as negative previous_outstanding (-1500) and offset new debt (+1000 = -500).")
        else:
            print("  --> FAIL: Negative balance failed to roll forward properly!")

        # ---------------------------------------------------------------------
        # TEST 4: Operator with 10 vehicles: Netting across vehicles
        # 4 vehicles have positive earnings (current_week_os = -2000 each = -8000)
        # 6 vehicles have rent debt (current_week_os = +3000 each = +18000)
        # Net current_week_os should be +10000.00
        # ---------------------------------------------------------------------
        print("\n--- TEST 4: Operator with 10 Vehicles Fleet Netting ---")
        for i in range(1, 11):
            cur_os = -2000.00 if i <= 4 else 3000.00
            db.execute(text("""
                INSERT INTO public.hisaab_vehicle_weekly (
                    week_id, week_start, week_end, vehicle_number, partner_id, partner_name, city,
                    allotted_days, onroad_days, daily_rent_applied, weekly_lease_rental, weekly_indemnity_fees, net_weekly_lease_rental,
                    uber_trips, uber_total_earnings, uber_cash_collection, uber_toll, uber_driver_sub_charge, uber_incentive, uber_week_os,
                    ola_trips, ola_net_revenue, ola_cash_collection, ola_toll, ola_gst, ola_online_payment, ola_incentive, ola_week_os,
                    current_week_os, settlement_status
                ) VALUES (
                    'CY26WK43', '2026-10-19', '2026-10-25', :veh, 'AUDIT_OP_FLEET', 'Fleet Operator Alpha', 'HYD',
                    6, 6, 600, 3600, 400, 4000,
                    20, 4000, 0, 0, 0, 0, :cur_os,
                    0, 0, 0, 0, 0, 0, 0, 0,
                    :cur_os, 'OPEN'
                );
            """), {"veh": f"TS09OP{1000+i}", "cur_os": cur_os})

        db.execute(text("CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK43', 'AUDIT_OP_FLEET');"))
        op_row = db.execute(text("""
            SELECT allotted_cars_count, total_onroad_days, current_week_os, total_outstanding, net_amount_to_collect, net_bank_payout, partner_type
            FROM public.hisaab_partner_weekly 
            WHERE week_id = 'CY26WK43' AND partner_id = 'AUDIT_OP_FLEET';
        """)).fetchone()
        print(f"  Operator Result in W43:")
        print(f"    allotted_cars={op_row[0]}, onroad_days={op_row[1]}, partner_type={op_row[6]}")
        print(f"    current_week_os={op_row[2]} (Expected: +10,000.00)")
        print(f"    total_outstanding={op_row[3]}, to_collect={op_row[4]}, payout={op_row[5]}")
        assert op_row[0] == 10, f"Expected 10 allotted cars, got {op_row[0]}"
        assert op_row[1] == 60, f"Expected 60 total onroad days, got {op_row[1]}"
        assert op_row[2] == Decimal('10000.00'), f"Expected 10000.00 current_week_os, got {op_row[2]}"
        print("  --> PASS: Operator fleet netting perfectly consolidated 4 credit cars (-8k) and 6 debt cars (+18k) to net +10k.")

        # ---------------------------------------------------------------------
        # TEST 5: Vehicle Swap Mid-week
        # Driver drives V1 for 3 days (rent 2000, cur_os = +500)
        # Driver then swaps to V2 for 3 days (rent 2000, cur_os = -300)
        # ---------------------------------------------------------------------
        print("\n--- TEST 5: Vehicle Swap Mid-Week for Individual Driver ---")
        db.execute(text("""
            INSERT INTO public.hisaab_vehicle_weekly (
                week_id, week_start, week_end, vehicle_number, partner_id, partner_name, city,
                allotted_days, onroad_days, daily_rent_applied, weekly_lease_rental, weekly_indemnity_fees, net_weekly_lease_rental,
                uber_trips, uber_total_earnings, uber_cash_collection, uber_toll, uber_driver_sub_charge, uber_incentive, uber_week_os,
                ola_trips, ola_net_revenue, ola_cash_collection, ola_toll, ola_gst, ola_online_payment, ola_incentive, ola_week_os,
                current_week_os, settlement_status
            ) VALUES 
            ('CY26WK43', '2026-10-19', '2026-10-25', 'TS09SWAP01', 'AUDIT_DRV_SWAP', 'Swap Driver', 'HYD',
             3, 3, 600, 1800, 200, 2000, 15, 1500, 0, 0, 0, 0, 500, 0, 0, 0, 0, 0, 0, 0, 0, 500.00, 'OPEN'),
            ('CY26WK43', '2026-10-19', '2026-10-25', 'TS09SWAP02', 'AUDIT_DRV_SWAP', 'Swap Driver', 'HYD',
             3, 3, 600, 1800, 200, 2000, 18, 2300, 0, 0, 0, 0, -300, 0, 0, 0, 0, 0, 0, 0, 0, -300.00, 'OPEN');
        """))

        db.execute(text("CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK43', 'AUDIT_DRV_SWAP');"))
        swap_row = db.execute(text("""
            SELECT allotted_cars_count, total_onroad_days, current_week_os, total_outstanding, partner_type
            FROM public.hisaab_partner_weekly 
            WHERE week_id = 'CY26WK43' AND partner_id = 'AUDIT_DRV_SWAP';
        """)).fetchone()
        print(f"  Swap Driver Result in W43:")
        print(f"    allotted_cars={swap_row[0]}, total_onroad_days={swap_row[1]}, partner_type={swap_row[4]}")
        print(f"    current_week_os={swap_row[2]} (Expected: +200.00)")
        print(f"    total_outstanding={swap_row[3]}")
        if swap_row[0] == 2:
            print("  --> NOTE/EDGE CASE: allotted_cars_count = 2 for driver who swapped vehicles (COUNT(DISTINCT vehicle_number)).")
            print("      total_onroad_days = 6 (properly aggregated sum of days across both vehicles).")
        
        # Rollback all test data cleanly
        trans.rollback()
        print("\n" + "=" * 80)
        print("TRANSACTION ROLLED BACK CLEANLY (No test data persisted).")
        print("=" * 80)

    except Exception as e:
        db.rollback()
        print("ERROR IN AUDIT EXECUTION:", e)
        raise
    finally:
        db.close()

if __name__ == '__main__':
    run_audit()
