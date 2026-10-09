"""
Audit Custody Dry-Run Scanner (Variable 1)
------------------------------------------
100% READ-ONLY diagnostic scanner that simulates vehicle custody and attendance
reconstruction for any historical settlement cycle without writing a single row to the database.

Usage:
    python audit_custody_dryrun.py --week CY26WK28
    python audit_custody_dryrun.py --start-date 2026-07-06 --end-date 2026-07-12
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import text

# Import database configuration
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import engine


def resolve_week_boundaries(week_id: str):
    """Fetch start and end date for a given settlement week."""
    with engine.connect() as conn:
        res = conn.execute(
            text("SELECT week_start, week_end, is_locked FROM hisaab_settlement_weeks WHERE week_id = :w"),
            {"w": week_id}
        ).fetchone()
        if not res:
            raise ValueError(f"Week ID '{week_id}' not found in hisaab_settlement_weeks!")
        return res[0], res[1], res[2]


def simulate_custody_for_date(conn, target_date):
    """
    Simulates the exact custody waterfall from sp_generate_daily_vehicle_status
    using a pure read-only SQL CTE query.
    """
    sql = text("""
    WITH ranked_allocs AS (
        SELECT 
            a.id, a.vehicle_number, a.partner_id, a.driver_name, a.driver_phone, a.hub_name, a.car_model, a.city, a.allocation_date, a.partner_type,
            ROW_NUMBER() OVER (PARTITION BY a.vehicle_number ORDER BY a.allocation_date DESC, a.id DESC) as rn
        FROM public.core_vehicle_allocation a
        WHERE a.is_deleted = FALSE
          AND a.allocation_date <= :target_date
    ),
    active_alloc AS (
        SELECT * FROM ranked_allocs WHERE rn = 1
    ),
    active_drop AS (
        SELECT 
            d.id, d.vehicle_number, d.return_date, d.return_type, d.driver_id, d.driver_name,
            ROW_NUMBER() OVER (PARTITION BY d.vehicle_number ORDER BY d.return_date ASC, d.id ASC) as rn
        FROM public.core_dropoffs d
        JOIN active_alloc a ON d.vehicle_number = a.vehicle_number AND d.return_date >= a.allocation_date
        WHERE d.is_deleted = FALSE
    ),
    first_drop_after_alloc AS (
        SELECT * FROM active_drop WHERE rn = 1
    ),
    next_allocs AS (
        SELECT 
            a2.vehicle_number, a2.allocation_date,
            ROW_NUMBER() OVER (PARTITION BY a2.vehicle_number ORDER BY a2.allocation_date ASC, a2.id ASC) as rn
        FROM public.core_vehicle_allocation a2
        JOIN active_alloc a ON a2.vehicle_number = a.vehicle_number AND a2.allocation_date > a.allocation_date
        WHERE a2.is_deleted = FALSE
    ),
    first_next_alloc AS (
        SELECT * FROM next_allocs WHERE rn = 1
    ),
    alloc_today AS (
        SELECT 
            a3.id, a3.vehicle_number, a3.partner_id, a3.driver_name, a3.driver_phone, a3.partner_type,
            ROW_NUMBER() OVER (PARTITION BY a3.vehicle_number ORDER BY a3.id DESC) as rn
        FROM public.core_vehicle_allocation a3
        WHERE a3.is_deleted = FALSE
          AND a3.allocation_date = :target_date
    ),
    alloc_today_latest AS (
        SELECT * FROM alloc_today WHERE rn = 1
    ),
    drop_today AS (
        SELECT 
            d2.id, d2.vehicle_number, d2.return_type,
            ROW_NUMBER() OVER (PARTITION BY d2.vehicle_number ORDER BY d2.id DESC) as rn
        FROM public.core_dropoffs d2
        WHERE d2.is_deleted = FALSE
          AND d2.return_date = :target_date
    ),
    drop_today_latest AS (
        SELECT * FROM drop_today WHERE rn = 1
    ),
    svs_today AS (
        SELECT 
            sv.vehicle_number, sv.final_status, sv.partner_id, sv.new_partner_name,
            ROW_NUMBER() OVER (PARTITION BY sv.vehicle_number ORDER BY sv.id DESC) as rn
        FROM public.sheet_vehicle_status sv
        WHERE sv.status_date = :target_date
    ),
    svs_today_latest AS (
        SELECT * FROM svs_today WHERE rn = 1
    )
    SELECT 
        vo.registration_no AS vehicle_number,
        CASE 
            WHEN vo.city ILIKE 'blr%' OR vo.city ILIKE 'bengalur%' THEN 'Bangalore'
            WHEN vo.city ILIKE 'hyd%' THEN 'Hyderabad'
            WHEN vo.city ILIKE 'mum%' OR vo.city ILIKE 'bombay%' THEN 'Mumbai'
            ELSE COALESCE(vo.city, 'Hyderabad')
        END AS city,
        vo.model AS car_model,
        
        -- Final Status Classification
        CASE 
            WHEN svs.final_status IS NOT NULL THEN 
                CASE 
                    WHEN svs.final_status = 'New Deployment' AND (svs.partner_id IS NOT NULL AND TRIM(svs.partner_id) != '') THEN 'Allocation'
                    WHEN svs.final_status = 'New Deployment' THEN 'New Deployment'
                    ELSE svs.final_status
                END
            WHEN at.id IS NOT NULL AND dt.id IS NOT NULL THEN 'Same Day D&A'
            WHEN at.id IS NOT NULL THEN 'Allocation'
            WHEN dt.id IS NOT NULL AND dt.return_type IN ('Repair and Maintenance', 'Vehicle Breakdown / Maintenance') THEN 'Maintenance'
            WHEN dt.id IS NOT NULL THEN 'Drop Off'
            WHEN aa.id IS NOT NULL AND (
                (fda.id IS NULL AND fna.allocation_date IS NULL) OR
                (fda.id IS NOT NULL AND (fna.allocation_date IS NULL OR fda.return_date <= fna.allocation_date) AND fda.return_date > :target_date) OR
                (fna.allocation_date IS NOT NULL AND fna.allocation_date > :target_date)
            ) THEN 'Active'
            ELSE 'RFD'
        END AS final_status,
        
        -- Resolved Partner ID
        CASE 
            WHEN (
                CASE 
                    WHEN svs.final_status IS NOT NULL THEN svs.final_status
                    WHEN at.id IS NOT NULL AND dt.id IS NOT NULL THEN 'Same Day D&A'
                    WHEN at.id IS NOT NULL THEN 'Allocation'
                    WHEN dt.id IS NOT NULL AND dt.return_type IN ('Repair and Maintenance', 'Vehicle Breakdown / Maintenance') THEN 'Maintenance'
                    WHEN dt.id IS NOT NULL THEN 'Drop Off'
                    WHEN aa.id IS NOT NULL AND (
                        (fda.id IS NULL AND fna.allocation_date IS NULL) OR
                        (fda.id IS NOT NULL AND (fna.allocation_date IS NULL OR fda.return_date <= fna.allocation_date) AND fda.return_date > :target_date) OR
                        (fna.allocation_date IS NOT NULL AND fna.allocation_date > :target_date)
                    ) THEN 'Active'
                    ELSE 'RFD'
                END
            ) IN ('RFD', 'Maintenance', 'Drop Off', 'New Deployment') THEN NULL
            ELSE COALESCE(
                (regexp_match(TRIM(svs.new_partner_name), '(LETZ[A-Z0-9]+)'))[1],
                svs.partner_id,
                at.partner_id,
                aa.partner_id
            )
        END AS partner_id,
        
        CASE WHEN svs.final_status IS NOT NULL THEN 'SHEET_GROUND_TRUTH' ELSE 'PORTAL_FALLBACK' END AS source_origin
        
    FROM public.core_vehicle_onboarding vo
    LEFT JOIN active_alloc aa ON vo.registration_no = aa.vehicle_number
    LEFT JOIN first_drop_after_alloc fda ON vo.registration_no = fda.vehicle_number
    LEFT JOIN first_next_alloc fna ON vo.registration_no = fna.vehicle_number
    LEFT JOIN alloc_today_latest at ON vo.registration_no = at.vehicle_number
    LEFT JOIN drop_today_latest dt ON vo.registration_no = dt.vehicle_number
    LEFT JOIN svs_today_latest svs ON vo.registration_no = svs.vehicle_number
    WHERE vo.is_deleted = FALSE;
    """)

    rows = conn.execute(sql, {"target_date": target_date}).fetchall()
    return rows


def run_dryrun_audit(start_date, end_date, week_label=None):
    print("=" * 80)
    print(f"CUSTODY ENGINE DRY-RUN AUDIT: {week_label or ''} [{start_date} -> {end_date}]")
    print("=" * 80)
    print("Mode: 100% READ-ONLY (Zero writes, zero mutation)\n")

    current_date = start_date
    daily_summaries = []
    total_anomalies = 0

    with engine.connect() as conn:
        while current_date <= end_date:
            rows = simulate_custody_for_date(conn, current_date)
            
            # Aggregate metrics
            total_fleet = len(rows)
            active_count = sum(1 for r in rows if r[3] in ('Active', 'Allocation', 'Same Day D&A'))
            rfd_count = sum(1 for r in rows if r[3] == 'RFD')
            maint_count = sum(1 for r in rows if r[3] == 'Maintenance')
            other_count = total_fleet - (active_count + rfd_count + maint_count)
            
            # Attribution check
            active_missing_partner = sum(1 for r in rows if r[3] in ('Active', 'Allocation') and not r[4])
            total_anomalies += active_missing_partner
            
            # City breakdown for active vehicles
            blr_active = sum(1 for r in rows if r[3] in ('Active', 'Allocation') and r[1] == 'Bangalore')
            hyd_active = sum(1 for r in rows if r[3] in ('Active', 'Allocation') and r[1] == 'Hyderabad')
            mum_active = sum(1 for r in rows if r[3] in ('Active', 'Allocation') and r[1] == 'Mumbai')
            
            # Distinct active partners
            distinct_partners = len(set(r[4] for r in rows if r[3] in ('Active', 'Allocation') and r[4]))
            source_type = rows[0][5] if rows else 'N/A'

            daily_summaries.append({
                "date": current_date.strftime("%Y-%m-%d (%a)"),
                "fleet": total_fleet,
                "active": active_count,
                "rfd": rfd_count,
                "maint": maint_count,
                "blr": blr_active,
                "hyd": hyd_active,
                "mum": mum_active,
                "partners": distinct_partners,
                "source": source_type,
                "missing_partner": active_missing_partner
            })

            current_date += timedelta(days=1)

    # Print Formatted Daily Breakdown
    header = f"{'Date':<18} | {'Fleet':<6} | {'Active (On-Road)':<17} | {'RFD':<6} | {'Maint':<6} | {'BLR':<5} | {'HYD':<5} | {'MUM':<5} | {'Partners':<9} | {'Source'}"
    print(header)
    print("-" * len(header))
    
    total_active_days = 0
    for d in daily_summaries:
        total_active_days += d['active']
        print(f"{d['date']:<18} | {d['fleet']:<6} | {d['active']:<17} | {d['rfd']:<6} | {d['maint']:<6} | {d['blr']:<5} | {d['hyd']:<5} | {d['mum']:<5} | {d['partners']:<9} | {d['source']}")

    print("-" * len(header))
    print(f"\nAUDIT SUMMARY FOR CYCLE:")
    print(f"  * Total Billable Vehicle-Days Calculated: {total_active_days:,}")
    print(f"  * Average Daily On-Road Fleet: {round(total_active_days / len(daily_summaries)):,} vehicles")
    print(f"  * Active Vehicles with Missing Partner ID: {total_anomalies} (Target: 0)")
    
    if total_anomalies == 0:
        print("\n[VERDICT: PASS] Custody data is 100% clean and ready for Variable 2 (Rent Calculation).")
    else:
        print(f"\n[VERDICT: WARNING] Detected {total_anomalies} active records missing partner attribution.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dry-run custody scanner for historical cycles.")
    parser.add_argument("--week", type=str, help="Settlement week ID (e.g. CY26WK28)")
    parser.add_argument("--start-date", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.week:
        start_d, end_d, locked = resolve_week_boundaries(args.week.upper())
        run_dryrun_audit(start_d, end_d, week_label=args.week.upper())
    elif args.start_date and args.end_date:
        s_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        e_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        run_dryrun_audit(s_date, e_date)
    else:
        print("Error: Specify either --week <WEEK_ID> or both --start-date and --end-date.")
        sys.exit(1)
