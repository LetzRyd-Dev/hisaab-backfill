"""
test_simulation.py
==================
Runs an end-to-end verification of cumulative hisaab roll-forwards across 3 successive weeks:
- Week 41 (Cutoff Week): Vivek starts with ₹1,850.50 cutoff debt + ₹1,000 hisaab = ₹2,850.50 total. (No payment)
- Week 42: Previous ₹2,850.50 carries forward + ₹1,200 hisaab = ₹4,050.50 total. (Pays ₹2,000 partial)
- Week 43: Remaining ₹2,050.50 carries forward + ₹800 hisaab = ₹2,850.50 total. (Pays full ₹2,850.50 -> ₹0 settled!)
"""

from cumulative_engine import CumulativeHisaabEngine, WeeklyHisaabRecord

def run_test():
    print("=================================================================")
    print("Testing Cutoff-Based Cumulative Rolling Hisaab Engine")
    print("=================================================================\n")

    engine = CumulativeHisaabEngine(cutoff_week_id="CY26WK41")
    engine.load_cutoff_from_csv("sample_cutoff_input.csv")

    vivek_id = "DRV-HYD-002"

    # --- WEEK 41 (Cutoff Week) ---
    print("\n--- Week 41 (Cutoff Week) ---")
    w41_data = [
        WeeklyHisaabRecord(
            week_id="CY26WK41",
            partner_id=vivek_id,
            gross_earnings=10000.0,
            rent_charges=8000.0,
            cash_collected=3000.0,
            penalties_tds=0.0  # Net Week O/S = 8000 + 3000 - 10000 = +1000
        )
    ]
    w41_payments = {vivek_id: 0.0}  # No payment made
    res41 = engine.process_week("CY26WK41", w41_data, w41_payments)[0]
    print(f"Opening Cutoff: Rs. {res41.previous_outstanding:,.2f}")
    print(f"Current Week O/S Generated: Rs. {res41.current_week_os:,.2f}")
    print(f"Payment Received: Rs. {res41.payment_received:,.2f}")
    print(f"Total Due To Collect: Rs. {res41.to_collect:,.2f} (Status: {res41.status})")
    assert res41.to_collect == 2850.50, f"Expected 2850.50, got {res41.to_collect}"

    # --- WEEK 42 (Unpaid balance rolls forward) ---
    print("\n--- Week 42 (Carried Forward + New Dues + Partial Payment) ---")
    w42_data = [
        WeeklyHisaabRecord(
            week_id="CY26WK42",
            partner_id=vivek_id,
            gross_earnings=12000.0,
            rent_charges=8500.0,
            cash_collected=4700.0,
            penalties_tds=0.0  # Net Week O/S = 8500 + 4700 - 12000 = +1200
        )
    ]
    w42_payments = {vivek_id: 2000.0}  # Vivek pays Rs. 2,000 partial payment
    res42 = engine.process_week("CY26WK42", w42_data, w42_payments)[0]
    print(f"Carried Forward Previous O/S: Rs. {res42.previous_outstanding:,.2f}")
    print(f"Current Week O/S Generated: Rs. {res42.current_week_os:,.2f}")
    print(f"Payment Received: Rs. {res42.payment_received:,.2f}")
    print(f"Total Due To Collect: Rs. {res42.to_collect:,.2f} (Status: {res42.status})")
    assert res42.to_collect == 2050.50, f"Expected 2050.50, got {res42.to_collect}"

    # --- WEEK 43 (Final Clearance) ---
    print("\n--- Week 43 (Carried Forward + New Dues + Full Clearance) ---")
    w43_data = [
        WeeklyHisaabRecord(
            week_id="CY26WK43",
            partner_id=vivek_id,
            gross_earnings=11000.0,
            rent_charges=8000.0,
            cash_collected=3800.0,
            penalties_tds=0.0  # Net Week O/S = 8000 + 3800 - 11000 = +800
        )
    ]
    # Total due before payment would be 2050.50 + 800 = 2850.50.
    w43_payments = {vivek_id: 2850.50}  # Vivek pays full remaining balance!
    res43 = engine.process_week("CY26WK43", w43_data, w43_payments)[0]
    print(f"Carried Forward Previous O/S: Rs. {res43.previous_outstanding:,.2f}")
    print(f"Current Week O/S Generated: Rs. {res43.current_week_os:,.2f}")
    print(f"Payment Received: Rs. {res43.payment_received:,.2f}")
    print(f"Total Due To Collect: Rs. {res43.to_collect:,.2f} (Status: {res43.status})")
    assert res43.to_collect == 0.0, f"Expected 0.0, got {res43.to_collect}"
    assert res43.status == "settled", f"Expected settled, got {res43.status}"

    print("\n=================================================================")
    print(" All simulation checks passed successfully!")
    print("=================================================================")

if __name__ == "__main__":
    run_test()
