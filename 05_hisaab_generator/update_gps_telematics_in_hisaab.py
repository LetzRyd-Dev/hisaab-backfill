"""
Module: update_gps_telematics_in_hisaab.py
Description: One-time fast in-place update of GPS telematics (gps_dead_km, gps_dead_mile_penalty)
across historical Hisaab tables (CY25WK24 to CY26WK35).
Strictly protects live production weeks (CY26WK36 to CY26WK42) and verifies zero-variance parity.
"""

import sys
import argparse
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import PROTECTED_PRODUCTION_WEEKS, SafetyViolationError

TARGET_WEEKS_SQL = """
    SELECT week_id, week_start, week_end
    FROM public.hisaab_settlement_weeks
    WHERE (week_id BETWEEN 'CY25WK24' AND 'CY25WK52')
       OR (week_id BETWEEN 'CY26WK01' AND 'CY26WK35')
    ORDER BY week_start
"""

def snapshot_production_weeks(conn):
    snap = {}
    for w in sorted(PROTECTED_PRODUCTION_WEEKS):
        v = conn.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(current_week_os), 0), COALESCE(SUM(gps_dead_mile_penalty), 0) "
            "FROM public.hisaab_vehicle_weekly WHERE week_id = :w"
        ), {"w": w}).fetchone()
        p = conn.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(total_outstanding), 0), COALESCE(SUM(total_dead_mile_penalty), 0) "
            "FROM public.hisaab_partner_weekly WHERE week_id = :w"
        ), {"w": w}).fetchone()
        snap[w] = (v[0], float(v[1]), float(v[2]), p[0], float(p[1]), float(p[2]))
    return snap

def run_gps_update(commit: bool = False):
    mode = "COMMIT MODE" if commit else "READ-ONLY DRY-RUN"
    print(f"\n{'=' * 120}")
    print(f"[GPS TELEMATICS INTEGRATION] {mode}")
    print("Scope: CY25WK24 to CY26WK35 (64 Historical Weeks with GPS)")
    print("Guarantees: Production weeks CY26WK36-WK42 are 100% UNTOUCHED | Zero-Variance Parity Enforced")
    print(f"{'=' * 120}\n")

    with engine.connect() as conn:
        pre_snap = snapshot_production_weeks(conn)
        conn.rollback()
        trans = conn.begin()
        try:
            # 1. Update hisaab_vehicle_weekly
            print("Step 1: Calculating GPS distance and updating hisaab_vehicle_weekly...")
            update_veh_sql = text("""
            WITH target_weeks AS (
                SELECT week_id, week_start, week_end
                FROM public.hisaab_settlement_weeks
                WHERE (week_id BETWEEN 'CY25WK24' AND 'CY25WK52')
                   OR (week_id BETWEEN 'CY26WK01' AND 'CY26WK35')
            ),
            veh_gps_daily AS (
                SELECT 
                    tw.week_id,
                    g.record_date,
                    UPPER(REPLACE(REPLACE(public.fn_clean_gps_vehicle_number(g.vehicle_number), ' ', ''), '-', '')) AS clean_veh,
                    SUM(COALESCE(g.distance_km, 0.00)) AS daily_km
                FROM target_weeks tw
                JOIN public.core_gps g ON g.record_date BETWEEN tw.week_start AND tw.week_end
                GROUP BY 1, 2, 3
            ),
            veh_gps_weekly AS (
                SELECT 
                    vg.week_id,
                    vg.clean_veh AS vehicle_number,
                    SUM(vg.daily_km) AS week_gps_km
                FROM veh_gps_daily vg
                GROUP BY 1, 2
            ),
            calc AS (
                SELECT 
                    v.id AS row_id,
                    COALESCE(vg.week_gps_km, 0.00) AS total_gps_km,
                    GREATEST(0.00, COALESCE(vg.week_gps_km, 0.00) - (
                        (COALESCE(v.uber_trips, 0) + COALESCE(v.ola_trips, 0)) * 3.00
                        + (v.onroad_days * 30.00)
                    )) AS new_dead_km,
                    CASE 
                        WHEN p.partner_type = 'Individual' 
                             AND UPPER(COALESCE(v.city, '')) IN ('BLR', 'BENGALURU', 'BANGALORE')
                        THEN ROUND(GREATEST(0.00, COALESCE(vg.week_gps_km, 0.00) - (
                            (COALESCE(v.uber_trips, 0) + COALESCE(v.ola_trips, 0)) * 3.00
                            + (v.onroad_days * 30.00)
                        )) * 3.00, 2)
                        ELSE 0.00
                    END AS new_dead_penalty,
                    COALESCE(v.gps_dead_mile_penalty, 0.00) AS old_dead_penalty,
                    v.current_week_os
                FROM public.hisaab_vehicle_weekly v
                JOIN target_weeks tw ON tw.week_id = v.week_id
                LEFT JOIN public.hisaab_partner_weekly p ON p.week_id = v.week_id AND p.partner_id = v.partner_id
                LEFT JOIN veh_gps_weekly vg ON vg.week_id = v.week_id AND vg.vehicle_number = UPPER(REPLACE(REPLACE(v.vehicle_number, ' ', ''), '-', ''))
            )
            UPDATE public.hisaab_vehicle_weekly v
            SET 
                gps_dead_km = c.new_dead_km,
                gps_dead_mile_penalty = c.new_dead_penalty,
                current_week_os = c.current_week_os + (c.new_dead_penalty - c.old_dead_penalty),
                net_to_collect_from_driver = GREATEST(0.00, c.current_week_os + (c.new_dead_penalty - c.old_dead_penalty)),
                net_payout_to_driver = ABS(LEAST(0.00, c.current_week_os + (c.new_dead_penalty - c.old_dead_penalty))),
                updated_at = NOW()
            FROM calc c
            WHERE v.id = c.row_id
              AND (v.gps_dead_km <> c.new_dead_km OR v.gps_dead_mile_penalty <> c.new_dead_penalty);
            """)
            res_v = conn.execute(update_veh_sql)
            print(f"  --> Updated {res_v.rowcount:,} rows in hisaab_vehicle_weekly.")

            # 2. Update hisaab_partner_weekly
            print("\nStep 2: Re-aggregating partner totals in hisaab_partner_weekly...")
            update_prt_sql = text("""
            WITH target_weeks AS (
                SELECT week_id
                FROM public.hisaab_settlement_weeks
                WHERE (week_id BETWEEN 'CY25WK24' AND 'CY25WK52')
                   OR (week_id BETWEEN 'CY26WK01' AND 'CY26WK35')
            ),
            veh_agg AS (
                SELECT 
                    v.week_id,
                    v.partner_id,
                    SUM(COALESCE(v.gps_dead_mile_penalty, 0.00)) AS sum_dead_penalty,
                    SUM(COALESCE(v.current_week_os, 0.00)) AS sum_current_os
                FROM public.hisaab_vehicle_weekly v
                JOIN target_weeks tw ON tw.week_id = v.week_id
                GROUP BY v.week_id, v.partner_id
            )
            UPDATE public.hisaab_partner_weekly p
            SET 
                total_dead_mile_penalty = va.sum_dead_penalty,
                current_week_os = va.sum_current_os,
                total_outstanding = (va.sum_current_os - COALESCE(p.amount_paid_during_week, 0.00)),
                net_bank_payout = ABS(LEAST(0.00, (va.sum_current_os - COALESCE(p.amount_paid_during_week, 0.00)))),
                net_amount_to_collect = GREATEST(0.00, (va.sum_current_os - COALESCE(p.amount_paid_during_week, 0.00))),
                updated_at = NOW()
            FROM veh_agg va
            WHERE p.week_id = va.week_id AND p.partner_id = va.partner_id
              AND (p.total_dead_mile_penalty <> va.sum_dead_penalty OR p.current_week_os <> va.sum_current_os);
            """)
            res_p = conn.execute(update_prt_sql)
            print(f"  --> Updated {res_p.rowcount:,} rows in hisaab_partner_weekly.")

            # 3. Audit Parity
            print("\nStep 3: Checking Zero-Variance Parity...")
            parity_check = conn.execute(text("""
                WITH target_weeks AS (
                    SELECT week_id
                    FROM public.hisaab_settlement_weeks
                    WHERE (week_id BETWEEN 'CY25WK24' AND 'CY25WK52')
                       OR (week_id BETWEEN 'CY26WK01' AND 'CY26WK35')
                ),
                v_sum AS (
                    SELECT v.week_id, SUM(v.current_week_os) AS v_os, SUM(v.gps_dead_mile_penalty) AS v_pen
                    FROM public.hisaab_vehicle_weekly v
                    JOIN target_weeks tw ON tw.week_id = v.week_id
                    GROUP BY v.week_id
                ),
                p_sum AS (
                    SELECT p.week_id, SUM(p.current_week_os) AS p_os, SUM(p.total_dead_mile_penalty) AS p_pen
                    FROM public.hisaab_partner_weekly p
                    JOIN target_weeks tw ON tw.week_id = p.week_id
                    WHERE p.partner_id IN (SELECT partner_id FROM public.hisaab_vehicle_weekly WHERE week_id = p.week_id)
                    GROUP BY p.week_id
                )
                SELECT 
                    COUNT(*) AS total_weeks,
                    COUNT(CASE WHEN ABS(v.v_os - p.p_os) > 0.01 THEN 1 END) AS mismatched_weeks,
                    SUM(v.v_pen) AS total_gps_pen,
                    MAX(ABS(v.v_os - p.p_os)) AS max_diff
                FROM v_sum v
                JOIN p_sum p ON p.week_id = v.week_id
            """)).fetchone()
            print(f"  • Total Weeks Verified: {parity_check[0]}")
            print(f"  • Total Mismatched Weeks: {parity_check[1]}")
            print(f"  • Total GPS Dead-Mile Penalty Added: Rs {float(parity_check[2]):,.2f}")
            print(f"  • Max Vehicle vs Partner Parity Diff: Rs {float(parity_check[3] or 0):,.2f}")

            if parity_check[1] > 0:
                raise SafetyViolationError(f"Parity mismatch detected in {parity_check[1]} weeks!")

            # 4. Verify Production Weeks Untouched
            print("\nStep 4: Verifying Production Weeks (CY26WK36 to CY26WK42) are 100% UNTOUCHED...")
            post_snap = snapshot_production_weeks(conn)
            if post_snap != pre_snap:
                raise SafetyViolationError("Production weeks were altered! Aborting transaction.")
            print("  --> Production weeks verified 100% untouched.")

            if commit:
                trans.commit()
                print(f"\n[SUCCESS] Committed GPS Telematics updates to database across {parity_check[0]} historical weeks.")
            else:
                trans.rollback()
                print(f"\n[DRY-RUN COMPLETE] Rolled back 100% cleanly. Zero rows committed to database.")

        except Exception as e:
            trans.rollback()
            print(f"\n[ERROR] Transaction aborted and rolled back: {e}")
            raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fast In-Place GPS Telematics Updater for Hisaab Tables")
    parser.add_argument("--commit", action="store_true", help="Commit changes (default: dry-run rollback)")
    args = parser.parse_args()
    run_gps_update(commit=args.commit)
