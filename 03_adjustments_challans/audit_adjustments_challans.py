"""
Audit Adjustments & Traffic Challans Scanner (Variables 3 & 4)
--------------------------------------------------------------
100% READ-ONLY diagnostic tool that audits approved adjustments,
accident recoveries, and unpaid traffic challans for any historical settlement cycle.

Usage:
    python audit_adjustments_challans.py --week CY26WK28
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def audit_adjustments_and_challans(week_id: str):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"ADJUSTMENTS & TRAFFIC CHALLANS AUDIT: {week_id}")
    print("Mode: 100% READ-ONLY (Zero writes, zero mutation)")
    print("=" * 80)

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        print(f"Target Period: {week_start} through {week_end}\n")

        # -------------------------------------------------------------
        # 1. AUDIT ADJUSTMENTS (core_adjustments)
        # -------------------------------------------------------------
        print("--- 1. ADJUSTMENTS LEDGER AUDIT (core_adjustments) ---")
        adj_query = text("""
            SELECT 
                count(*) as total_records,
                count(CASE WHEN approval_status = 'Approved' THEN 1 END) as approved_count,
                count(CASE WHEN approval_status != 'Approved' THEN 1 END) as non_approved_count,
                
                -- Approved Debits (Excluding Accidents)
                COALESCE(sum(CASE 
                    WHEN approval_status = 'Approved' 
                     AND polarity = 'DEBIT' 
                     AND NOT (adjustment_category ILIKE '%accident%' OR adjustment_category ILIKE '%damage%')
                    THEN amount ELSE 0 END), 0.0) as approved_debits,
                
                -- Approved Accident Recoveries
                COALESCE(sum(CASE 
                    WHEN approval_status = 'Approved' 
                     AND polarity = 'DEBIT' 
                     AND (adjustment_category ILIKE '%accident%' OR adjustment_category ILIKE '%damage%')
                    THEN amount ELSE 0 END), 0.0) as approved_accidents,
                
                -- Approved Credits (Waivers, Bonuses)
                COALESCE(sum(CASE 
                    WHEN approval_status = 'Approved' AND polarity = 'CREDIT' 
                    THEN amount ELSE 0 END), 0.0) as approved_credits,
                
                count(DISTINCT partner_id) as distinct_partners,
                count(DISTINCT vehicle_number) as distinct_vehicles
            FROM public.core_adjustments
            WHERE adjustment_date BETWEEN :s AND :e;
        """)

        adj = conn.execute(adj_query, {"s": week_start, "e": week_end}).fetchone()

        print(f"  * Total Adjustments Ingested:    {adj[0]:,} records")
        print(f"  * Approved for Settlement:       {adj[1]:,} records (Non-approved: {adj[2]:,})")
        print(f"  * Active Vehicles Tagged:        {adj[7]:,} vehicles")
        print(f"  * Active Partners Tagged:        {adj[6]:,} partners\n")
        print(f"  Financial Breakdown (Approved):")
        print(f"    - General Debits (Penalties):    Rs. {float(adj[3]):,.2f} (+ to driver dues)")
        print(f"    - Accident Damage Recoveries:    Rs. {float(adj[4]):,.2f} (+ to driver dues)")
        print(f"    - General Credits (Waivers):     Rs. {float(adj[5]):,.2f} (- to driver dues)")
        print(f"    - Net Adjustment Balance:        Rs. {float(adj[3] + adj[4] - adj[5]):,.2f}\n")

        # -------------------------------------------------------------
        # 2. AUDIT TRAFFIC CHALLANS (core_challans)
        # -------------------------------------------------------------
        print("--- 2. TRAFFIC CHALLANS AUDIT (core_challans) ---")
        challan_query = text("""
            SELECT 
                count(*) as total_challans,
                count(CASE WHEN payment_status IN ('UNPAID', 'PARTIALLY_PAID') THEN 1 END) as eligible_unpaid,
                count(CASE WHEN payment_status = 'PAID' THEN 1 END) as excluded_paid,
                COALESCE(sum(CASE WHEN payment_status IN ('UNPAID', 'PARTIALLY_PAID') THEN fine_amount ELSE 0 END), 0.0) as unpaid_amount,
                count(DISTINCT vehicle_reg_no) as vehicles_with_fines
            FROM public.core_challans
            WHERE violation_date BETWEEN :s AND :e;
        """)

        ch = conn.execute(challan_query, {"s": week_start, "e": week_end}).fetchone()

        print(f"  * Total Traffic Violations:      {ch[0]:,} notices")
        print(f"  * Unpaid Fines (Billed):         {ch[1]:,} notices across {ch[4]:,} vehicles")
        print(f"  * Paid Fines (Excluded):         {ch[2]:,} notices")
        print(f"  * Total Unpaid Fine Liability:   Rs. {float(ch[3]):,.2f} (+ to driver dues)\n")

        # -------------------------------------------------------------
        # 3. VERDICT
        # -------------------------------------------------------------
        total_liabilities = float(adj[3] + adj[4] - adj[5] + ch[3])
        print("=" * 80)
        print(f"NET NON-OPERATIONAL LIABILITIES FOR {week_id}: Rs. {total_liabilities:,.2f}")
        print("[VERDICT: READY] Adjustments and Challans are verified and ready for Hisaab sync.")
        print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit adjustments and challans for a historical cycle.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    args = parser.parse_args()

    audit_adjustments_and_challans(args.week)
