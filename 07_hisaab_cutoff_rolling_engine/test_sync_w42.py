import sys
sys.path.insert(0, r'c:\Users\anura\Downloads\hisaab-backfill')
from config import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    print("Executing sp_sync_hisaab_cumulative_cutoff('CY26WK42')...")
    db.execute(text("CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK42');"))
    db.commit()
    print("Sync complete! Now inspecting results for CY26WK42...")

    # 1. Row count and financial totals
    totals = db.execute(text("""
        SELECT 
            count(*),
            sum(previous_outstanding),
            sum(current_week_os),
            sum(total_outstanding),
            sum(net_amount_to_collect),
            sum(net_bank_payout),
            sum(pending_deposit),
            sum(security_deposit_paid)
        FROM public.hisaab_partner_weekly
        WHERE week_id = 'CY26WK42';
    """)).fetchone()

    print("\n=== VERIFICATION RESULTS FOR CY26WK42 ===")
    print(f"  Total Partners:               {totals[0]:,} (Expected: 2,446)")
    print(f"  Sum Previous Outstanding:     INR {float(totals[1]):,.2f} (Expected: INR 36,141,591.90)")
    print(f"  Sum Current Week OS:          INR {float(totals[2]):,.2f}")
    print(f"  Sum Total Outstanding:        INR {float(totals[3]):,.2f} (Expected: INR 36,141,591.90)")
    print(f"  Sum Net Amount To Collect:    INR {float(totals[4]):,.2f} (Expected: INR 39,314,588.09)")
    print(f"  Sum Net Bank Payout (Credit): INR {float(totals[5]):,.2f} (Expected: INR 3,172,996.19)")
    print(f"  Sum Pending Security Deposit: INR {float(totals[6]):,.2f} (Expected: INR 8,538,918.00)")

    assert totals[0] == 2446, f"Expected 2446 partners, got {totals[0]}"
    assert abs(float(totals[3]) - 36141591.90) < 0.05, f"Expected 36141591.90, got {totals[3]}"
    assert abs(float(totals[4]) - 39314588.09) < 0.05, f"Expected 39314588.09, got {totals[4]}"
    assert abs(float(totals[5]) - 3172996.19) < 0.05, f"Expected 3172996.19, got {totals[5]}"
    assert abs(float(totals[6]) - 8538918.00) < 0.05, f"Expected 8538918.00, got {totals[6]}"

    # 2. City breakdown
    cities = db.execute(text("""
        SELECT city, count(*), sum(total_outstanding), sum(pending_deposit)
        FROM public.hisaab_partner_weekly
        WHERE week_id = 'CY26WK42'
        GROUP BY city
        ORDER BY city;
    """)).fetchall()

    print("\n=== CITY DISTRIBUTION IN CY26WK42 ===")
    for c in cities:
        print(f"  City: {c[0]:<4} | Partners: {c[1]:>5} | Total Due: INR {float(c[2]):>14,.2f} | Pending Deposit: INR {float(c[3]):>12,.2f}")

    assert len(cities) >= 3, "Expected at least 3 cities!"

    # 3. Sample row with resolved name
    sample = db.execute(text("""
        SELECT partner_id, partner_name, partner_type, city, total_outstanding, pending_deposit, settlement_status
        FROM public.hisaab_partner_weekly
        WHERE week_id = 'CY26WK42'
        ORDER BY total_outstanding DESC
        LIMIT 3;
    """)).fetchall()
    print("\n=== SAMPLE TOP ROWS IN CY26WK42 ===")
    for s in sample:
        print(" ", s)

    print("\n>>> ALL TESTS PASSED! CY26WK42 PERFECTLY REFLECTS COMPLETE BASELINE! <<<")

finally:
    db.close()
