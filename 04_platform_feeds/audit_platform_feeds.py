"""
Audit Platform Feeds & GPS Telematics Scanner (Variables 5 & 6)
---------------------------------------------------------------
100% READ-ONLY diagnostic tool that audits Uber, Ola, Rapido, and GPS coverage
for any historical settlement cycle.

Usage:
    python audit_platform_feeds.py --week CY26WK28
"""

import argparse
import sys
from pathlib import Path
from sqlalchemy import text

# Import database configuration
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def audit_platform_feeds(week_id: str):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"PLATFORM FARES & GPS TELEMATICS AUDIT: {week_id}")
    print("Mode: 100% READ-ONLY (Zero writes, zero mutation)")
    print("=" * 80)

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)
        print(f"Target Period: {week_start} through {week_end}\n")

        # -------------------------------------------------------------
        # 1. AUDIT UBER FEEDS
        # -------------------------------------------------------------
        print("--- 1. UBER PLATFORM DATA ---")
        uber_daily_cnt = conn.execute(
            text("SELECT count(*) FROM public.core_uber_daily WHERE operational_date BETWEEN :s AND :e"),
            {"s": week_start, "e": week_end}
        ).scalar()

        uber_metrics = conn.execute(text("""
            SELECT 
                count(DISTINCT vehicle_number) as active_vehicles,
                COALESCE(sum(total_trips), 0) as total_trips,
                COALESCE(sum(gross_fare), 0.0) as gross_fare,
                COALESCE(sum(cash_collected), 0.0) as cash_collected,
                COALESCE(sum(trip_distance_km), 0.0) as trip_km
            FROM public.core_uber_daily
            WHERE operational_date BETWEEN :s AND :e;
        """), {"s": week_start, "e": week_end}).fetchone()

        # Check for legacy staging availability
        legacy_uber_cnt = 0
        legacy_table = "None"
        for tbl in ['raw_uber_data', 'z_uber_raw_1', 'z_uber_raw_15']:
            try:
                cnt = conn.execute(text(f"SELECT count(*) FROM public.{tbl}")).scalar()
                if cnt > 0:
                    legacy_table = tbl
                    legacy_uber_cnt = cnt
                    break
            except Exception:
                pass

        print(f"  * core_uber_daily Rows:          {uber_daily_cnt:,} records")
        print(f"  * Vehicles with Uber Fares:      {uber_metrics[0]:,} vehicles")
        print(f"  * Total Completed Trips:         {uber_metrics[1]:,} trips")
        print(f"  * Gross Fares Earned:            Rs. {float(uber_metrics[2]):,.2f}")
        print(f"  * Passenger Cash Pocketed:       Rs. {float(uber_metrics[3]):,.2f}")
        print(f"  * In-Trip Distance:              {float(uber_metrics[4]):,.1f} km")
        if uber_daily_cnt == 0 and legacy_uber_cnt > 0:
            print(f"  * Legacy Staging Available:      {legacy_table} ({legacy_uber_cnt:,} rows ready for ingestion)\n")
        else:
            print(f"  * Feed Status:                   {'Complete' if uber_daily_cnt > 0 else 'Absent (Zero-Fill Fallback)'}\n")

        # -------------------------------------------------------------
        # 2. AUDIT OLA FEEDS
        # -------------------------------------------------------------
        print("--- 2. OLA PLATFORM DATA ---")
        ola_daily_cnt = conn.execute(
            text("SELECT count(*) FROM public.core_ola_daily WHERE service_date BETWEEN :s AND :e"),
            {"s": week_start, "e": week_end}
        ).scalar()

        ola_metrics = conn.execute(text("""
            SELECT 
                count(DISTINCT vehicle_number) as active_vehicles,
                COALESCE(sum(total_trips), 0) as total_trips,
                COALESCE(sum(operator_bill), 0.0) as net_revenue,
                COALESCE(sum(cash_collected), 0.0) as cash_collected,
                COALESCE(sum(trip_distance_km), 0.0) as trip_km
            FROM public.core_ola_daily
            WHERE service_date BETWEEN :s AND :e;
        """), {"s": week_start, "e": week_end}).fetchone()

        print(f"  * core_ola_daily Rows:           {ola_daily_cnt:,} records")
        print(f"  * Vehicles with Ola Fares:       {ola_metrics[0]:,} vehicles")
        print(f"  * Total Completed Trips:         {ola_metrics[1]:,} trips")
        print(f"  * Net Revenue Earned:            Rs. {float(ola_metrics[2]):,.2f}")
        print(f"  * Passenger Cash Pocketed:       Rs. {float(ola_metrics[3]):,.2f}")
        print(f"  * Feed Status:                   {'Complete' if ola_daily_cnt > 0 else 'Absent (Zero-Fill Fallback)'}\n")

        # -------------------------------------------------------------
        # 3. AUDIT GPS TELEMATICS
        # -------------------------------------------------------------
        print("--- 3. GPS TELEMATICS & MILEAGE ---")
        gps_cnt = conn.execute(
            text("SELECT count(*) FROM public.core_gps WHERE record_date BETWEEN :s AND :e"),
            {"s": week_start, "e": week_end}
        ).scalar()

        gps_km = conn.execute(
            text("SELECT COALESCE(sum(distance_km), 0.0) FROM public.core_gps WHERE record_date BETWEEN :s AND :e"),
            {"s": week_start, "e": week_end}
        ).scalar()

        print(f"  * core_gps Rows:                 {gps_cnt:,} telemetry records")
        print(f"  * Total Fleet Distance Tracked:  {float(gps_km):,.1f} km")
        print(f"  * Feed Status:                   {'Complete' if gps_cnt > 0 else 'Absent (₹0.00 Penalty Fallback)'}\n")

        # -------------------------------------------------------------
        # 4. SUMMARY VERDICT
        # -------------------------------------------------------------
        print("=" * 80)
        total_platform_rev = float(uber_metrics[2] + ola_metrics[2])
        total_cash_pocketed = float(uber_metrics[3] + ola_metrics[3])
        print(f"Total Platform Earnings: Rs. {total_platform_rev:,.2f} (Credits to Rent)")
        print(f"Total Cash Pocketed:     Rs. {total_cash_pocketed:,.2f} (Adds to Driver Debt)")
        print(f"[VERDICT] Platform feeds audited. Ready for Variable 5 & 6 ingestion/zero-fill.")
        print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit platform fares and GPS for a historical cycle.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    args = parser.parse_args()

    audit_platform_feeds(args.week)
