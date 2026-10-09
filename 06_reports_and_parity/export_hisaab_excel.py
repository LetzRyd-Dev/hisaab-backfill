"""
Export Hisaab Excel Statement (Module 06)
-----------------------------------------
Exports a multi-tab financial Excel workbook for any finalized settlement cycle
mirroring the operational templates used by LetzRyd finance and operations.

Sheets Generated:
1. Executive Summary: High-level KPI scorecard and city metrics.
2. Vehicle Weekly Detail: Full vehicle-level ledger (Tier 2).
3. Partner Consolidated: Fleet rollup by operator/partner (Tier 3).
4. Bank Payouts Clearing: Actionable treasury disbursement list.

Usage:
    python export_hisaab_excel.py --week CY26WK28
"""

import argparse
import sys
from pathlib import Path
import pandas as pd
from sqlalchemy import text

# Import database configuration
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import HisaabSafetyGuard


def export_excel(week_id: str, output_dir: str = None):
    week_id = week_id.upper()
    guard = HisaabSafetyGuard()

    print("=" * 80)
    print(f"HISAAB EXCEL STATEMENT EXPORTER: {week_id}")
    print("=" * 80)

    out_path = Path(output_dir) if output_dir else Path(__file__).resolve().parent.parent / "reports"
    out_path.mkdir(parents=True, exist_ok=True)
    file_name = out_path / f"{week_id}_HISAAB_STATEMENT.xlsx"

    with engine.connect() as conn:
        week_start, week_end, is_locked = guard.get_week_dates(conn, week_id)

        # 1. Fetch Vehicle Weekly Detail
        print("  * Extracting Tier 2 (Vehicle Weekly Detail)...")
        veh_df = pd.read_sql(text("""
            SELECT 
                vehicle_number,
                partner_id,
                driver_name,
                driver_phone,
                city,
                onroad_days,
                net_weekly_lease_rental AS lease_rent,
                uber_total_earnings,
                uber_cash_collection,
                ola_net_revenue,
                ola_cash_collection,
                total_platform_earnings,
                total_cash_collected,
                challan_amount,
                adjustment_amount,
                dead_mile_charges,
                tds_amount,
                current_week_os,
                to_collect,
                to_payout,
                settlement_status
            FROM public.hisaab_vehicle_weekly
            WHERE week_id = :w
            ORDER BY city, partner_id, vehicle_number;
        """), conn, params={"w": week_id})

        if veh_df.empty:
            print(f"[ERROR] Zero records found in hisaab_vehicle_weekly for {week_id}!")
            sys.exit(1)

        # 2. Fetch Partner Consolidated Detail
        print("  * Extracting Tier 3 (Partner Consolidated Statement)...")
        part_df = pd.read_sql(text("""
            SELECT 
                partner_id,
                partner_name,
                partner_phone,
                partner_type,
                city,
                allotted_cars_count AS cars_count,
                total_onroad_days,
                total_net_rent_billed,
                total_platform_earnings,
                total_cash_collected,
                current_week_os,
                previous_outstanding,
                total_outstanding,
                net_bank_payout,
                net_amount_to_collect,
                payout_account_number,
                payout_ifsc,
                settlement_status
            FROM public.hisaab_partner_weekly
            WHERE week_id = :w
            ORDER BY current_week_os DESC;
        """), conn, params={"w": week_id})

        # 3. Create Bank Payouts Clearing Sheet
        print("  * Preparing Treasury Disbursement Schedule...")
        payout_df = part_df[part_df['net_bank_payout'] > 0][
            ['partner_id', 'partner_name', 'partner_phone', 'city', 'cars_count', 
             'net_bank_payout', 'payout_account_number', 'payout_ifsc']
        ].sort_values(by='net_bank_payout', ascending=False)

        # 4. Create Executive Summary Sheet
        print("  * Compiling Executive KPI Scorecard...")
        summary_data = [
            {"KPI": "Settlement Week ID", "Value": week_id},
            {"KPI": "Cycle Start Date", "Value": str(week_start)},
            {"KPI": "Cycle End Date", "Value": str(week_end)},
            {"KPI": "Lock Immutability Status", "Value": "LOCKED" if is_locked else "OPEN"},
            {"KPI": "Total Fleet Settled", "Value": f"{len(veh_df):,} vehicles"},
            {"KPI": "Total Partners / Operators", "Value": f"{len(part_df):,} partners"},
            {"KPI": "Total Lease Rent Billed", "Value": f"Rs. {veh_df['lease_rent'].sum():,.2f}"},
            {"KPI": "Total Gross Platform Revenue", "Value": f"Rs. {veh_df['total_platform_earnings'].sum():,.2f}"},
            {"KPI": "Total Passenger Cash Collected", "Value": f"Rs. {veh_df['total_cash_collected'].sum():,.2f}"},
            {"KPI": "Total Traffic Challans", "Value": f"Rs. {veh_df['challan_amount'].sum():,.2f}"},
            {"KPI": "Total Adjustments (Signed)", "Value": f"Rs. {veh_df['adjustment_amount'].sum():,.2f}"},
            {"KPI": "Total GPS Dead Mile Penalty", "Value": f"Rs. {veh_df['dead_mile_charges'].sum():,.2f}"},
            {"KPI": "Total Statutory TDS (194C)", "Value": f"Rs. {veh_df['tds_amount'].sum():,.2f}"},
            {"KPI": "Net Vehicle Current O/S", "Value": f"Rs. {veh_df['current_week_os'].sum():,.2f}"},
            {"KPI": "Net Partner Current O/S", "Value": f"Rs. {part_df['current_week_os'].sum():,.2f}"},
            {"KPI": "Multi-Grain Parity Variance", "Value": f"Rs. {abs(veh_df['current_week_os'].sum() - part_df['current_week_os'].sum()):.2f}"},
            {"KPI": "Total Treasury Bank Payouts", "Value": f"Rs. {part_df['net_bank_payout'].sum():,.2f}"},
            {"KPI": "Total Driver Dues to Collect", "Value": f"Rs. {part_df['net_amount_to_collect'].sum():,.2f}"},
        ]
        sum_df = pd.DataFrame(summary_data)

        # 5. Write to Excel
        print(f"  * Writing Excel workbook to {file_name}...")
        with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
            sum_df.to_excel(writer, sheet_name="Executive Summary", index=False)
            part_df.to_excel(writer, sheet_name="Partner Consolidated", index=False)
            veh_df.to_excel(writer, sheet_name="Vehicle Weekly Detail", index=False)
            payout_df.to_excel(writer, sheet_name="Bank Payouts Clearing", index=False)

        print(f"\n[SUCCESS] Statement generated: {file_name}")
        print(f"  * Total Sheets: 4")
        print(f"  * Vehicle Rows: {len(veh_df):,}")
        print(f"  * Partner Rows: {len(part_df):,}")
        print(f"  * Payout Rows:  {len(payout_df):,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Hisaab statement to Excel.")
    parser.add_argument("--week", type=str, required=True, help="Settlement week ID (e.g. CY26WK28)")
    parser.add_argument("--output-dir", type=str, help="Custom output directory path")
    args = parser.parse_args()

    export_excel(args.week, output_dir=args.output_dir)
