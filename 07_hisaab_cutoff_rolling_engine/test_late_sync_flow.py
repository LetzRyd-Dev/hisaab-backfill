import sys
sys.path.insert(0, r'c:\Users\anura\Downloads\hisaab-backfill')
from config import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    # 1. Deploy the updated sp_route_late_aggregator_sync procedure
    with open(r'c:\Users\anura\Downloads\hisaab-backfill\07_hisaab_cutoff_rolling_engine\sp_route_late_aggregator_sync.sql', 'r') as f:
        sql = f.read()
    db.execute(text(sql))
    db.commit()
    print("Deployed sp_route_late_aggregator_sync procedure.")

    test_partner = "TEST_PARTNER_LATE_SYNC_99"

    # Clean up any leftover test data first
    db.execute(text("DELETE FROM public.hisaab_adjustments_ledger WHERE partner_id = :pid;"), {'pid': test_partner})
    db.commit()

    print("\n--- TEST CASE 1: Aggregator Late Sync (Ola trip delta arriving after lock) ---")
    # Call procedure for locked week CY26WK41:
    # Net earnings delta: INR 1200.00, Cash delta: INR 400.00 -> Net credit = INR 800.00
    db.execute(text("""
        CALL public.sp_route_late_aggregator_sync(
            :pid,
            'CY26WK41',
            'OLA',
            1200.00,
            400.00,
            'Late Sunday night trip batch received on Tuesday',
            'TS09UA1234'
        );
    """), {'pid': test_partner})
    db.commit()

    row1 = db.execute(text("""
        SELECT partner_id, settlement_week_id, adjustment_category, polarity, amount, is_prior_period, remarks
        FROM public.hisaab_adjustments_ledger
        WHERE partner_id = :pid AND adjustment_category = 'OLA_LATE_SYNC'
        ORDER BY created_at DESC LIMIT 1;
    """), {'pid': test_partner}).fetchone()

    print(f"  Partner:            {row1[0]}")
    print(f"  Settlement Week:    {row1[1]} (Targeted locked CY26WK41 -> Successfully Routed to {row1[1]}!)")
    print(f"  Category:           {row1[2]}")
    print(f"  Polarity:           {row1[3]}")
    print(f"  Amount:             INR {row1[4]:,.2f}")
    print(f"  Is Prior Period:    {row1[5]}")
    print(f"  Remarks:            {row1[6]}")

    assert row1[1] == 'CY26WK42', f"Expected CY26WK42, got {row1[1]}"
    assert row1[5] is True, "Expected is_prior_period to be True"
    print("  -> TEST CASE 1 PASSED!")

    print("\n--- TEST CASE 2: Late Operational Variable (Challan from Oct 5 arriving after lock) ---")
    # Someone inserts an adjustment (e.g. Challan) directly with settlement_week_id = 'CY26WK41'
    # Trigger fn_trg_auto_route_prior_period_adjustment should intercept it BEFORE INSERT and redirect to CY26WK42!
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
            :pid,
            'CY26WK41',  -- Targeted to locked week!
            'TS09UA1234',
            '2026-10-05',
            'CY26WK41',
            'CHALLAN',
            'DEBIT',
            1000.00,
            FALSE,       -- Caller didn't know it's prior period
            'Approved',
            'Over-speeding fine received late from traffic portal',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        );
    """), {'pid': test_partner})
    db.commit()

    row2 = db.execute(text("""
        SELECT partner_id, settlement_week_id, adjustment_category, polarity, amount, is_prior_period, remarks
        FROM public.hisaab_adjustments_ledger
        WHERE partner_id = :pid AND adjustment_category = 'CHALLAN'
        ORDER BY created_at DESC LIMIT 1;
    """), {'pid': test_partner}).fetchone()

    print(f"  Partner:            {row2[0]}")
    print(f"  Settlement Week:    {row2[1]} (Targeted locked CY26WK41 -> Trigger Intercepted & Routed to {row2[1]}!)")
    print(f"  Category:           {row2[2]}")
    print(f"  Polarity:           {row2[3]}")
    print(f"  Amount:             INR {row2[4]:,.2f}")
    print(f"  Is Prior Period:    {row2[5]}")
    print(f"  Remarks:            {row2[6]}")

    assert row2[1] == 'CY26WK42', f"Expected CY26WK42, got {row2[1]}"
    assert row2[5] is True, "Expected is_prior_period to be True"
    print("  -> TEST CASE 2 PASSED!")

    # Clean up test rows
    db.execute(text("DELETE FROM public.hisaab_adjustments_ledger WHERE partner_id = :pid;"), {'pid': test_partner})
    db.commit()
    print("\nAll test adjustments cleaned up cleanly. Zero test rows remaining.")

finally:
    db.close()
