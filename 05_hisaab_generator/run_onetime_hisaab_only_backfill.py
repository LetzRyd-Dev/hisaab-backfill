"""
One-Time Hisaab-Tables-Only Backfill Generator
==============================================
Creates historical weekly Hisaab entries ONLY in:
  1. public.hisaab_vehicle_weekly
  2. public.hisaab_partner_weekly

STRICT GUARANTEES:
- ZERO writes to any core_*, sheet_*, july_*, daily_rent_log, or source tables.
- For dates missing in daily_rent_log (e.g. WK01-WK25, WK28-WK32, Aug 10), reconstructs
  daily vehicle custody and 5-tier rental waterfall 100% in-memory via read-only CTEs.
- Missing platform feeds (Uber/Ola/GPS) default cleanly to 0 / 0.00.
- Pulls real weekly Uber Vehicle Incentives from uber_vehicle_incentives_raw when core_uber_weekly is empty.
- Suppresses false GPS dead-mile penalties on dates where Uber daily trip distance is not yet loaded.
- Hard-blocks protected weeks (CY26WK26, CY26WK27, CY26WK36-CY26WK41) and verifies zero roll-forward impact.
- Defaults to DRY-RUN (runs inside a transaction and rolls back 100% cleanly unless --commit is passed).
"""
import argparse
import sys
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import engine
from safety_guard import PROTECTED_PRODUCTION_WEEKS, SafetyViolationError

DEFAULT_BACKFILL_WEEKS = [f"CY26WK{i:02d}" for i in range(1, 26)] + [f"CY26WK{i:02d}" for i in range(28, 36)]


def assert_week_not_protected(week_id: str):
    if week_id.strip().upper() in PROTECTED_PRODUCTION_WEEKS:
        raise SafetyViolationError(
            f"[BLOCKED] Week {week_id} is in PROTECTED_PRODUCTION_WEEKS ({sorted(PROTECTED_PRODUCTION_WEEKS)})."
        )


def verify_roll_forward_disabled(conn):
    val = conn.execute(text(
        "SELECT config_value FROM public.hisaab_system_config WHERE config_key ILIKE '%roll_forward%'"
    )).scalar()
    if val is not None and str(val).strip().lower() == "true":
        raise SafetyViolationError(f"Roll-forward is enabled ({val})! Must be false.")


def snapshot_protected_weeks(conn) -> dict:
    snap = {}
    for w in sorted(PROTECTED_PRODUCTION_WEEKS):
        v = conn.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(current_week_os), 0) FROM public.hisaab_vehicle_weekly WHERE week_id = :w"
        ), {"w": w}).fetchone()
        p = conn.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(total_outstanding), 0) FROM public.hisaab_partner_weekly WHERE week_id = :w"
        ), {"w": w}).fetchone()
        snap[w] = (v[0], float(v[1]), p[0], float(p[1]))
    return snap


def assert_protected_weeks_unchanged(conn, pre_snap: dict):
    post_snap = snapshot_protected_weeks(conn)
    if post_snap != pre_snap:
        raise SafetyViolationError(f"Protected weeks snapshot changed! Pre={pre_snap} Post={post_snap}")

VEHICLE_WEEKLY_HISAAB_ONLY_SQL = """
WITH week_bounds AS (
    SELECT week_id, settlement_year, settlement_week, week_start, week_end
    FROM public.hisaab_settlement_weeks
    WHERE week_id = :week_id
),
uber_wk_cnt AS (
    SELECT COUNT(*) AS cnt FROM public.core_uber_weekly WHERE week_id = :week_id
),
ola_wk_cnt AS (
    SELECT COUNT(*) AS cnt FROM public.core_ola_weekly WHERE week_id = :week_id
),
rent_log_day_cnt AS (
    SELECT COUNT(DISTINCT drl.log_date) AS day_cnt
    FROM public.daily_rent_log drl
    CROSS JOIN week_bounds wb
    WHERE drl.log_date BETWEEN wb.week_start AND wb.week_end
),
missing_rent_dates AS (
    SELECT gs::date AS target_date
    FROM week_bounds wb
    CROSS JOIN rent_log_day_cnt rldc
    CROSS JOIN LATERAL generate_series(wb.week_start, wb.week_end, '1 day'::interval) gs
    WHERE rldc.day_cnt < 6
),
active_alloc AS (
    SELECT DISTINCT ON (md.target_date, a.vehicle_number)
        md.target_date, a.id, a.vehicle_number, a.partner_id, a.driver_name, a.driver_phone,
        a.hub_name, a.car_model, a.city, a.allocation_date, a.partner_type
    FROM missing_rent_dates md
    JOIN public.core_vehicle_allocation a
      ON a.is_deleted = FALSE AND a.allocation_date <= md.target_date
    ORDER BY md.target_date, a.vehicle_number, a.allocation_date DESC, a.id DESC
),
first_drop_after_alloc AS (
    SELECT DISTINCT ON (aa.target_date, d.vehicle_number)
        aa.target_date, d.id, d.vehicle_number, d.return_date, d.return_type, d.driver_id, d.driver_name
    FROM active_alloc aa
    JOIN public.core_dropoffs d
      ON d.is_deleted = FALSE
     AND d.vehicle_number = aa.vehicle_number
     AND d.return_date >= aa.allocation_date
    ORDER BY aa.target_date, d.vehicle_number, d.return_date ASC, d.id ASC
),
first_next_alloc AS (
    SELECT DISTINCT ON (aa.target_date, a2.vehicle_number)
        aa.target_date, a2.vehicle_number, a2.allocation_date
    FROM active_alloc aa
    JOIN public.core_vehicle_allocation a2
      ON a2.is_deleted = FALSE
     AND a2.vehicle_number = aa.vehicle_number
     AND (a2.allocation_date > aa.allocation_date OR (a2.allocation_date = aa.allocation_date AND a2.id > aa.id))
    ORDER BY aa.target_date, a2.vehicle_number, a2.allocation_date ASC, a2.id ASC
),
alloc_today_latest AS (
    SELECT DISTINCT ON (md.target_date, a.vehicle_number)
        md.target_date, a.id, a.vehicle_number, a.partner_id, a.driver_name, a.driver_phone,
        a.hub_name, a.car_model, a.city, a.allocation_date, a.partner_type
    FROM missing_rent_dates md
    JOIN public.core_vehicle_allocation a
      ON a.is_deleted = FALSE AND a.allocation_date = md.target_date
    ORDER BY md.target_date, a.vehicle_number, a.id DESC
),
drop_today_latest AS (
    SELECT DISTINCT ON (md.target_date, d.vehicle_number)
        md.target_date, d.id, d.vehicle_number, d.return_type, d.return_date, d.driver_id, d.driver_name
    FROM missing_rent_dates md
    JOIN public.core_dropoffs d
      ON d.is_deleted = FALSE AND d.return_date = md.target_date
    ORDER BY md.target_date, d.vehicle_number, d.id DESC
),
active_maint_latest AS (
    SELECT DISTINCT ON (md.target_date, m.vehicle_number)
        md.target_date, m.id, m.vehicle_number, m.workshop_name, m.start_date, m.end_date
    FROM missing_rent_dates md
    JOIN public.core_maintenance m
      ON m.is_deleted = FALSE
     AND m.start_date <= md.target_date
     AND (
         m.end_date >= md.target_date
         OR (
             m.end_date IS NULL
             AND NOT EXISTS (
                 SELECT 1 FROM public.core_vehicle_allocation a
                 WHERE a.vehicle_number = m.vehicle_number
                   AND a.is_deleted = FALSE
                   AND a.allocation_date > m.start_date
                   AND a.allocation_date <= md.target_date
             )
         )
     )
    ORDER BY md.target_date, m.vehicle_number, m.start_date DESC, m.id DESC
),
svs_today_latest AS (
    SELECT DISTINCT ON (md.target_date, s.vehicle_number)
        md.target_date, s.vehicle_number, s.final_status, s.cohort, s.partner_id,
        s.partner_name, s.new_partner_name, s.vehicle_model, s.city
    FROM missing_rent_dates md
    JOIN public.sheet_vehicle_status s
      ON s.status_date = md.target_date
    ORDER BY md.target_date, s.vehicle_number, s.id DESC
),
virtual_daily_status AS (
    SELECT
        vo.registration_no AS vehicle_number,
        md.target_date AS status_date,
        COALESCE(svs.city, at.city, aa.city, vo.city, 'UNKNOWN') AS city,
        COALESCE(svs.vehicle_model, at.car_model, aa.car_model, vo.model, 'UNKNOWN') AS car_model,
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
            WHEN am.id IS NOT NULL THEN 'Maintenance'
            WHEN aa.id IS NOT NULL AND (
                (fda.id IS NULL AND fna.allocation_date IS NULL) OR
                (fda.id IS NOT NULL AND (fna.allocation_date IS NULL OR fda.return_date <= fna.allocation_date) AND fda.return_date > md.target_date) OR
                (fna.allocation_date IS NOT NULL AND fna.allocation_date > md.target_date)
            ) THEN 'Active'
            ELSE 'RFD'
        END AS final_status,
        CASE
            WHEN (
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
                    WHEN am.id IS NOT NULL THEN 'Maintenance'
                    WHEN aa.id IS NOT NULL AND (
                        (fda.id IS NULL AND fna.allocation_date IS NULL) OR
                        (fda.id IS NOT NULL AND (fna.allocation_date IS NULL OR fda.return_date <= fna.allocation_date) AND fda.return_date > md.target_date) OR
                        (fna.allocation_date IS NOT NULL AND fna.allocation_date > md.target_date)
                    ) THEN 'Active'
                    ELSE 'RFD'
                END
            ) IN ('RFD', 'Maintenance', 'Drop Off', 'New Deployment') THEN NULL
            ELSE COALESCE(
                (regexp_match(TRIM(svs.new_partner_name), '(LETZ[A-Z0-9]+)'))[1],
                CASE WHEN aa.partner_type = 'Operator' THEN aa.partner_id END,
                CASE WHEN at.partner_type = 'Operator' THEN at.partner_id END,
                svs.partner_id,
                at.partner_id,
                aa.partner_id
            )
        END AS partner_id,
        FALSE AS billable_rent_day,
        aa.id AS allocation_id,
        aa.allocation_date AS allocation_date,
        fda.return_date AS dropoff_date
    FROM missing_rent_dates md
    CROSS JOIN public.core_vehicle_onboarding vo
    LEFT JOIN active_alloc aa ON aa.target_date = md.target_date AND aa.vehicle_number = vo.registration_no
    LEFT JOIN first_drop_after_alloc fda ON fda.target_date = md.target_date AND fda.vehicle_number = vo.registration_no
    LEFT JOIN first_next_alloc fna ON fna.target_date = md.target_date AND fna.vehicle_number = vo.registration_no
    LEFT JOIN alloc_today_latest at ON at.target_date = md.target_date AND at.vehicle_number = vo.registration_no
    LEFT JOIN drop_today_latest dt ON dt.target_date = md.target_date AND dt.vehicle_number = vo.registration_no
    LEFT JOIN active_maint_latest am ON am.target_date = md.target_date AND am.vehicle_number = vo.registration_no
    LEFT JOIN svs_today_latest svs ON svs.target_date = md.target_date AND svs.vehicle_number = vo.registration_no
    WHERE vo.is_deleted = FALSE
      AND (aa.id IS NOT NULL OR at.id IS NOT NULL OR svs.vehicle_number IS NOT NULL)
),
virtual_raw_status AS (
    SELECT
        s.status_date AS log_date,
        UPPER(REPLACE(REPLACE(s.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
        COALESCE(NULLIF(TRIM(s.partner_id), ''), 'SYSTEM_ONBOARDED') AS partner_id,
        CASE
            WHEN s.city ILIKE 'blr%' OR s.city ILIKE 'bengalur%' THEN 'Bangalore'
            WHEN s.city ILIKE 'hyd%' THEN 'Hyderabad'
            WHEN s.city ILIKE 'mum%' OR s.city ILIKE 'bombay%' THEN 'Mumbai'
            ELSE COALESCE(s.city, 'Hyderabad')
        END AS city,
        COALESCE(s.car_model, 'Unknown') AS vehicle_model,
        COALESCE(s.final_status, 'Active') AS attendance_status,
        s.billable_rent_day,
        wb.week_id,
        wb.week_start,
        wb.week_end,
        COALESCE(po.onboarding_type,
            CASE WHEN s.partner_id ILIKE '%OP%' OR s.partner_id ILIKE '%FLEET%' OR s.partner_id ILIKE '%IP%' THEN 'Operator' ELSE 'Individual' END
        ) AS customer_type,
        COALESCE(cp_plan.plan_id, p_enrolled.plan_id) AS enrolled_plan_id
    FROM virtual_daily_status s
    CROSS JOIN week_bounds wb
    LEFT JOIN public.core_partner_onboarding po ON po.partner_id = s.partner_id
    LEFT JOIN LATERAL (
        SELECT plan_id FROM public.rental_custom_partner_plans
        WHERE partner_id = s.partner_id AND plan_id IS NOT NULL AND is_active = TRUE
        ORDER BY custom_plan_id DESC LIMIT 1
    ) cp_plan ON TRUE
    LEFT JOIN public.core_rental_plans p_enrolled
        ON p_enrolled.plan_code = CASE
            WHEN po.driver_plan ILIKE '%Uber Reducing%' AND (s.city ILIKE 'blr%' OR s.city ILIKE 'bengalur%') THEN 'BLR_UBER_TBS'
            WHEN po.driver_plan IN ('D2R', 'Drive to Rent') AND (s.city ILIKE 'blr%' OR s.city ILIKE 'bengalur%') THEN 'BLR_MASTER_IND'
            WHEN po.driver_plan ILIKE 'Rapido%' AND (s.city ILIKE 'blr%' OR s.city ILIKE 'bengalur%') THEN 'BLR_ALL_PLATFORM'
            WHEN (po.driver_plan ILIKE '%EBS%' OR po.driver_plan = 'LIP') AND (s.city ILIKE 'hyd%') THEN 'HYD_UBER_EBS'
            WHEN s.partner_id = 'LETZBLRIP7034607989' THEN 'BLR_OP_HAMZA'
            WHEN s.partner_id = 'LETZBLRIP7026684292' THEN 'BLR_OP_SUBHAN'
            WHEN s.partner_id IN ('LETZBLRIP9656907001', 'LETZBLR9656907001') THEN 'BLR_OP_RISHAD_EBS'
            WHEN s.partner_id = 'LETZBLRIP7356813050' THEN 'BLR_OP_RISHAN_SARBAS'
            WHEN s.partner_id = 'LETZBLRIP8075280208' THEN 'BLR_OP_RAMEES'
            ELSE po.driver_plan
        END
),
virtual_daily_trips AS (
    SELECT
        u.log_date,
        u.vehicle_number,
        COALESCE(SUM(CASE WHEN ub.operational_date = u.log_date THEN ub.completed_trips ELSE 0 END), 0) +
        COALESCE(SUM(CASE WHEN ol.service_date = u.log_date THEN ol.completed_trips ELSE 0 END), 0) AS day_trips,
        COALESCE(ub_w.completed_trips, SUM(ub.completed_trips), 0) + COALESCE(ol_w.completed_trips, SUM(ol.completed_trips), 0) AS week_trips,
        COALESCE(ol_w.completed_trips, SUM(ol.completed_trips), 0) AS week_ola_trips
    FROM (SELECT DISTINCT log_date, vehicle_number, week_start, week_end, week_id FROM virtual_raw_status) u
    LEFT JOIN public.core_uber_weekly ub_w
        ON ub_w.week_id = u.week_id AND UPPER(REPLACE(ub_w.vehicle_number, ' ', '')) = u.vehicle_number
    LEFT JOIN public.core_uber_daily ub
        ON UPPER(REPLACE(ub.vehicle_number, ' ', '')) = u.vehicle_number
        AND ub.operational_date BETWEEN u.week_start AND u.week_end
    LEFT JOIN public.core_ola_weekly ol_w
        ON ol_w.week_id = u.week_id AND UPPER(REPLACE(ol_w.vehicle_number, ' ', '')) = u.vehicle_number
    LEFT JOIN public.core_ola_daily ol
        ON UPPER(REPLACE(ol.vehicle_number, ' ', '')) = u.vehicle_number
        AND ol.service_date BETWEEN u.week_start AND u.week_end
    GROUP BY u.log_date, u.vehicle_number, ub_w.completed_trips, ol_w.completed_trips
),
virtual_status_with_billability AS (
    SELECT
        rs.log_date,
        rs.week_id,
        rs.vehicle_number,
        rs.partner_id,
        rs.city,
        rs.vehicle_model,
        rs.customer_type,
        rs.enrolled_plan_id,
        CASE
            WHEN COALESCE(dt.day_trips, 0) > 0 AND rs.attendance_status IN ('Drop Off', 'Drop-off', 'RFD', 'Unassigned', 'Maintenance', 'Breakdown', 'Accident') THEN 'Active'
            ELSE rs.attendance_status
        END AS attendance_status,
        CASE
            WHEN rs.partner_id IS NULL OR rs.partner_id = '' OR rs.partner_id = 'SYSTEM_ONBOARDED' THEN FALSE
            WHEN COALESCE(dt.day_trips, 0) > 0 THEN TRUE
            WHEN rs.attendance_status IN ('Drop Off', 'Drop-off', 'RFD', 'Unassigned') THEN FALSE
            WHEN rs.attendance_status IN ('Maintenance', 'Breakdown', 'Accident') AND NOT rs.billable_rent_day THEN FALSE
            ELSE TRUE
        END AS is_billable_day,
        COALESCE(dt.week_trips, 0)::INT AS weekly_completed_trips,
        COALESCE(dt.week_ola_trips, 0)::INT AS weekly_ola_trips,
        COALESCE(rs.enrolled_plan_id,
            CASE
                WHEN rs.city = 'Hyderabad' THEN 6
                WHEN rs.city = 'Mumbai' THEN 10
                WHEN rs.city = 'Bangalore' THEN
                    CASE WHEN rs.customer_type = 'Operator' THEN 20 ELSE 1 END
                ELSE 9
            END
        ) AS default_plan_id
    FROM virtual_raw_status rs
    LEFT JOIN virtual_daily_trips dt ON dt.log_date = rs.log_date AND dt.vehicle_number = rs.vehicle_number
),
virtual_rent_rows AS (
    SELECT
        swb.log_date,
        swb.vehicle_number,
        swb.partner_id,
        swb.city,
        swb.vehicle_model,
        swb.attendance_status,
        swb.is_billable_day,
        CASE
            WHEN NOT swb.is_billable_day THEN 0.00
            WHEN ex.override_daily_rent IS NOT NULL THEN ex.override_daily_rent
            WHEN swb.city = 'Mumbai' AND (swb.vehicle_model ILIKE '%dzire%' OR swb.vehicle_model ILIKE '%tour s%') THEN 1100.00
            WHEN cp.custom_daily_rent IS NOT NULL THEN cp.custom_daily_rent
            WHEN slab.base_daily_rent IS NOT NULL THEN slab.base_daily_rent
            WHEN mb.default_base_rent IS NOT NULL THEN mb.default_base_rent
            WHEN p.default_daily_rent IS NOT NULL THEN p.default_daily_rent
            ELSE 0.00
        END AS applied_daily_rent,
        CASE
            WHEN NOT swb.is_billable_day THEN 0.00
            WHEN ex.override_fee IS NOT NULL THEN ex.override_fee
            WHEN fee.is_waiver = TRUE THEN 0.00
            WHEN fee.fee_amount IS NOT NULL THEN fee.fee_amount
            WHEN cp.custom_daily_fee IS NOT NULL AND cp.custom_daily_rent IS NOT NULL THEN cp.custom_daily_fee
            WHEN swb.city = 'Mumbai' THEN 30.00
            WHEN slab.default_daily_fee IS NOT NULL THEN slab.default_daily_fee
            WHEN mb.default_daily_indemnity IS NOT NULL THEN mb.default_daily_indemnity
            WHEN p.default_daily_fee IS NOT NULL THEN p.default_daily_fee
            ELSE 30.00
        END AS applied_daily_indemnity,
        CASE
            WHEN NOT swb.is_billable_day THEN NULL
            WHEN ex.override_daily_rent IS NOT NULL THEN NULL
            WHEN cp.custom_daily_rent IS NOT NULL THEN COALESCE(cp.plan_id, swb.default_plan_id)
            WHEN swb.city = 'Mumbai' AND (swb.vehicle_model ILIKE '%dzire%' OR swb.vehicle_model ILIKE '%tour s%') THEN 11
            WHEN slab.base_daily_rent IS NOT NULL THEN slab.plan_id
            WHEN mb.default_base_rent IS NOT NULL THEN NULL
            ELSE swb.default_plan_id
        END AS matched_plan_id,
        0::bigint AS id
    FROM virtual_status_with_billability swb
    LEFT JOIN LATERAL (
        SELECT exception_id, override_daily_rent, override_fee
        FROM public.rental_exceptions
        WHERE status IN ('APPROVED', 'PENDING_CONFIRMATION')
          AND GREATEST(swb.log_date, '2026-01-01'::date) BETWEEN valid_from AND valid_to
          AND (
              (partner_id = swb.partner_id AND vehicle_number = swb.vehicle_number)
              OR (vehicle_number = swb.vehicle_number AND partner_id IS NULL)
              OR (partner_id = swb.partner_id AND vehicle_number IS NULL)
          )
        ORDER BY
            CASE WHEN partner_id IS NOT NULL AND vehicle_number IS NOT NULL THEN 1
                 WHEN vehicle_number IS NOT NULL THEN 2
                 ELSE 3 END
        LIMIT 1
    ) ex ON TRUE
    LEFT JOIN LATERAL (
        SELECT custom_plan_id, custom_daily_rent, custom_daily_fee, plan_id
        FROM public.rental_custom_partner_plans
        WHERE partner_id = swb.partner_id
          AND is_active = TRUE
          AND GREATEST(swb.log_date, '2026-01-01'::date) BETWEEN valid_from AND valid_to
          AND (vehicle_model = 'ALL'
               OR vehicle_model IS NULL
               OR REPLACE(REPLACE(LOWER(swb.vehicle_model), '-', ''), ' ', '') LIKE '%' || REPLACE(REPLACE(LOWER(vehicle_model), '-', ''), ' ', '') || '%'
               OR REPLACE(REPLACE(LOWER(vehicle_model), '-', ''), ' ', '') LIKE '%' || REPLACE(REPLACE(LOWER(swb.vehicle_model), '-', ''), ' ', '') || '%')
          AND (vehicle_number = swb.vehicle_number OR vehicle_number IS NULL)
        ORDER BY
            CASE WHEN vehicle_number IS NOT NULL THEN 1 ELSE 2 END,
            CASE WHEN vehicle_model IS NOT NULL AND vehicle_model <> 'ALL' THEN 1 ELSE 2 END
        LIMIT 1
    ) cp ON TRUE
    LEFT JOIN LATERAL (
        SELECT s.slab_id, s.plan_id, s.base_daily_rent, s.default_daily_fee
        FROM public.rental_rate_slabs s
        JOIN public.core_rental_plans p ON p.plan_id = s.plan_id
        WHERE s.city = swb.city
          AND (
              (s.partner_id = swb.partner_id AND (swb.enrolled_plan_id IS NULL OR s.plan_id = swb.enrolled_plan_id))
              OR (s.partner_id = 'ALL' AND s.plan_id = swb.default_plan_id)
          )
          AND (s.customer_type = 'ALL' OR s.customer_type = swb.customer_type)
          AND (s.vehicle_model = 'ALL'
               OR REPLACE(REPLACE(LOWER(swb.vehicle_model), '-', ''), ' ', '') LIKE '%' || REPLACE(REPLACE(LOWER(s.vehicle_model), '-', ''), ' ', '') || '%'
               OR REPLACE(REPLACE(LOWER(s.vehicle_model), '-', ''), ' ', '') LIKE '%' || REPLACE(REPLACE(LOWER(swb.vehicle_model), '-', ''), ' ', '') || '%')
          AND (
              (s.condition_rule = 'OLA_GE_1' AND swb.weekly_ola_trips >= 1)
              OR (s.condition_rule = 'OLA_GE_1_UBER_ZERO' AND swb.weekly_ola_trips >= 1 AND (swb.weekly_completed_trips - swb.weekly_ola_trips) = 0)
              OR (s.condition_rule = 'OLA_ZERO' AND swb.weekly_ola_trips = 0)
              OR (s.condition_rule = 'NONE')
          )
          AND swb.weekly_completed_trips >= s.trip_min
          AND (s.trip_max IS NULL OR swb.weekly_completed_trips <= s.trip_max)
          AND GREATEST(swb.log_date, '2026-01-01'::date) BETWEEN s.valid_from AND s.valid_to
        ORDER BY CASE WHEN s.partner_id <> 'ALL' THEN 1 ELSE 2 END, s.slab_id ASC
        LIMIT 1
    ) slab ON TRUE
    LEFT JOIN LATERAL (
        SELECT baseline_id, default_base_rent, default_daily_indemnity, vehicle_model
        FROM public.rental_model_baselines
        WHERE city = swb.city
          AND is_active = TRUE
          AND (
              vehicle_model = 'ALL'
              OR REPLACE(REPLACE(LOWER(swb.vehicle_model), '-', ''), ' ', '') LIKE '%' || REPLACE(REPLACE(LOWER(vehicle_model), '-', ''), ' ', '') || '%'
              OR REPLACE(REPLACE(LOWER(vehicle_model), '-', ''), ' ', '') LIKE '%' || REPLACE(REPLACE(LOWER(swb.vehicle_model), '-', ''), ' ', '') || '%'
          )
        ORDER BY CASE WHEN vehicle_model <> 'ALL' THEN 1 ELSE 2 END
        LIMIT 1
    ) mb ON TRUE
    LEFT JOIN public.core_rental_plans p ON p.plan_id = swb.default_plan_id
    LEFT JOIN LATERAL (
        SELECT fee_amount, is_waiver
        FROM public.rental_fee_rules
        WHERE city = swb.city
          AND GREATEST(swb.log_date, '2026-01-01'::date) BETWEEN valid_from AND valid_to
          AND (
              (partner_id = swb.partner_id AND vehicle_model = swb.vehicle_model)
              OR (partner_id = swb.partner_id AND vehicle_model = 'ALL')
              OR (partner_id = 'ALL' AND vehicle_model = swb.vehicle_model)
              OR (partner_id = 'ALL' AND vehicle_model = 'ALL')
          )
        ORDER BY
            CASE WHEN partner_id <> 'ALL' AND vehicle_model <> 'ALL' THEN 1
                 WHEN partner_id <> 'ALL' THEN 2
                 WHEN vehicle_model <> 'ALL' THEN 3
                 ELSE 4 END
        LIMIT 1
    ) fee ON TRUE
),
unified_daily_rent_log AS (
    SELECT
        d.id,
        d.log_date,
        d.vehicle_number,
        d.partner_id,
        d.city,
        d.vehicle_model,
        d.attendance_status,
        d.is_billable_day,
        d.applied_daily_rent,
        d.applied_daily_indemnity,
        d.net_daily_rent,
        d.matched_plan_id
    FROM public.daily_rent_log d
    CROSS JOIN week_bounds wb
    CROSS JOIN rent_log_day_cnt rldc
    WHERE d.log_date BETWEEN wb.week_start AND wb.week_end
      AND rldc.day_cnt >= 6

    UNION ALL

    SELECT
        v.id,
        v.log_date,
        v.vehicle_number,
        v.partner_id,
        v.city,
        v.vehicle_model,
        v.attendance_status,
        v.is_billable_day,
        v.applied_daily_rent,
        v.applied_daily_indemnity,
        (v.applied_daily_rent + v.applied_daily_indemnity) AS net_daily_rent,
        v.matched_plan_id
    FROM virtual_rent_rows v
),
partner_car_counts AS (
    SELECT
        d.partner_id,
        COUNT(DISTINCT d.vehicle_number) AS car_count
    FROM unified_daily_rent_log d
    GROUP BY d.partner_id
),
rent_agg AS (
    SELECT
        d.vehicle_number,
        COALESCE(NULLIF(TRIM(d.partner_id), ''), 'SYSTEM_ONBOARDED') AS partner_id,
        RIGHT(REGEXP_REPLACE(COALESCE(NULLIF(TRIM(d.partner_id), ''), ''), '[^0-9]', '', 'g'), 10) AS partner_phone,
        (ARRAY_AGG(d.city ORDER BY d.log_date DESC))[1] AS city,
        (ARRAY_AGG(d.vehicle_model ORDER BY d.log_date DESC))[1] AS vehicle_model,
        (ARRAY_AGG(p.plan_name ORDER BY d.log_date DESC))[1] AS rental_plan,
        COUNT(d.log_date) AS allotted_days,
        COUNT(CASE WHEN d.attendance_status IN ('Active', 'Allocation', 'Same Day D&A') AND d.is_billable_day = TRUE THEN 1 END) AS onroad_days,
        ROUND(AVG(d.applied_daily_rent), 2) AS daily_rent_applied,
        SUM(d.applied_daily_rent) AS weekly_lease_rental,
        SUM(d.applied_daily_indemnity) AS weekly_indemnity_fees,
        SUM(d.net_daily_rent) AS net_weekly_lease_rental
    FROM unified_daily_rent_log d
    LEFT JOIN public.core_rental_plans p ON d.matched_plan_id = p.plan_id
    WHERE NOT (
        COALESCE(NULLIF(TRIM(d.partner_id), ''), 'SYSTEM_ONBOARDED') = 'SYSTEM_ONBOARDED'
        AND EXISTS (
            SELECT 1 FROM unified_daily_rent_log d2
            WHERE d2.vehicle_number = d.vehicle_number
              AND COALESCE(NULLIF(TRIM(d2.partner_id), ''), 'SYSTEM_ONBOARDED') <> 'SYSTEM_ONBOARDED'
        )
    )
    GROUP BY d.vehicle_number, COALESCE(NULLIF(TRIM(d.partner_id), ''), 'SYSTEM_ONBOARDED'), RIGHT(REGEXP_REPLACE(COALESCE(NULLIF(TRIM(d.partner_id), ''), ''), '[^0-9]', '', 'g'), 10)
),
rent_ranked AS (
    SELECT
        ra.*,
        ROW_NUMBER() OVER(
            PARTITION BY UPPER(REPLACE(REPLACE(ra.vehicle_number, ' ', ''), '-', ''))
            ORDER BY ra.allotted_days DESC, ra.net_weekly_lease_rental DESC, ra.partner_id DESC
        ) AS partner_rank,
        ROW_NUMBER() OVER(
            PARTITION BY ra.partner_id
            ORDER BY ra.allotted_days DESC, ra.net_weekly_lease_rental DESC, ra.vehicle_number DESC
        ) AS partner_veh_rank
    FROM rent_agg ra
),
raw_uber_inc_fallback AS (
    SELECT
        UPPER(REPLACE(REPLACE(u.number_plate, ' ', ''), '-', '')) AS vehicle_number,
        MAX(COALESCE(u.total_payout, 0.00)) AS uber_incentive
    FROM public.uber_vehicle_incentives_raw u
    CROSS JOIN week_bounds wb
    WHERE u.number_plate IS NOT NULL AND TRIM(u.number_plate) <> '' AND u.number_plate <> 'nan'
      AND u.start_date::date BETWEEN wb.week_start AND wb.week_end
    GROUP BY UPPER(REPLACE(REPLACE(u.number_plate, ' ', ''), '-', ''))
),
uber_agg AS (
    SELECT
        UPPER(REPLACE(REPLACE(u.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
        COALESCE(SUM(u.completed_trips), 0) AS uber_trips,
        COALESCE(SUM(ABS(u.uber_total_earnings)), 0.00) AS uber_total_earnings,
        COALESCE(SUM(ABS(u.uber_cash_collection)), 0.00) AS uber_cash_collection,
        COALESCE(SUM(ABS(u.uber_toll)), 0.00) AS uber_toll,
        COALESCE(SUM(ABS(u.uber_driver_sub_charge)), 0.00) AS uber_driver_sub_charge,
        COALESCE(SUM(ABS(u.uber_vehicle_incentive)), 0.00) AS uber_incentive,
        COALESCE(SUM(
            ABS(u.uber_total_earnings) + ABS(u.uber_vehicle_incentive) + ABS(u.uber_toll)
            - ABS(u.uber_cash_collection) - ABS(u.uber_driver_sub_charge)
        ), 0.00) AS uber_week_os
    FROM public.core_uber_weekly u
    CROSS JOIN uber_wk_cnt uwc
    WHERE u.week_id = :week_id AND uwc.cnt > 0
    GROUP BY UPPER(REPLACE(REPLACE(u.vehicle_number, ' ', ''), '-', ''))

    UNION ALL

    SELECT
        COALESCE(d.vehicle_number, inc.vehicle_number) AS vehicle_number,
        COALESCE(d.uber_trips, 0) AS uber_trips,
        COALESCE(d.uber_total_earnings, 0.00) AS uber_total_earnings,
        COALESCE(d.uber_cash_collection, 0.00) AS uber_cash_collection,
        COALESCE(d.uber_toll, 0.00) AS uber_toll,
        COALESCE(d.uber_driver_sub_charge, 0.00) AS uber_driver_sub_charge,
        COALESCE(inc.uber_incentive, 0.00) AS uber_incentive,
        COALESCE(d.uber_week_os_no_inc, 0.00) + COALESCE(inc.uber_incentive, 0.00) AS uber_week_os
    FROM (
        SELECT
            UPPER(REPLACE(REPLACE(u.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
            COALESCE(SUM(u.completed_trips), 0) AS uber_trips,
            COALESCE(SUM(ABS(u.net_fare_earnings)), 0.00) AS uber_total_earnings,
            COALESCE(SUM(ABS(u.cash_collected)), 0.00) AS uber_cash_collection,
            COALESCE(SUM(ABS(u.tolls_refunded)), 0.00) AS uber_toll,
            COALESCE(SUM(ABS(u.driver_subscription_charge)), 0.00) AS uber_driver_sub_charge,
            COALESCE(SUM(
                ABS(u.net_fare_earnings) + ABS(u.tolls_refunded)
                - ABS(u.cash_collected) - ABS(u.driver_subscription_charge)
            ), 0.00) AS uber_week_os_no_inc
        FROM public.core_uber_daily u
        CROSS JOIN week_bounds wb
        WHERE u.operational_date BETWEEN wb.week_start AND wb.week_end
        GROUP BY UPPER(REPLACE(REPLACE(u.vehicle_number, ' ', ''), '-', ''))
    ) d
    FULL OUTER JOIN raw_uber_inc_fallback inc ON d.vehicle_number = inc.vehicle_number
    CROSS JOIN uber_wk_cnt uwc
    WHERE uwc.cnt = 0
),
ola_agg AS (
    SELECT
        UPPER(REPLACE(REPLACE(o.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
        COALESCE(SUM(o.completed_trips), 0) AS ola_trips,
        COALESCE(SUM(ABS(o.ola_net_revenue)), 0.00) AS ola_net_revenue,
        COALESCE(SUM(ABS(o.ola_cash_collected)), 0.00) AS ola_cash_collection,
        COALESCE(SUM(ABS(o.ola_toll)), 0.00) AS ola_toll,
        0.00 AS ola_gst,
        COALESCE(SUM(ABS(o.ola_online_payment_deductions)), 0.00) AS ola_online_payment,
        COALESCE(SUM(ABS(o.ola_portal_incentive)), 0.00) AS ola_incentive,
        COALESCE(SUM(
            ABS(o.ola_net_revenue) + ABS(o.ola_portal_incentive) + ABS(o.ola_toll)
            - ABS(o.ola_cash_collected)
        ), 0.00) AS ola_week_os
    FROM public.core_ola_weekly o
    CROSS JOIN ola_wk_cnt owc
    WHERE o.week_id = :week_id AND owc.cnt > 0
    GROUP BY UPPER(REPLACE(REPLACE(o.vehicle_number, ' ', ''), '-', ''))

    UNION ALL

    SELECT
        UPPER(REPLACE(REPLACE(o.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
        COALESCE(SUM(o.completed_trips), 0) AS ola_trips,
        COALESCE(SUM(ABS(o.operator_bill)), 0.00) AS ola_net_revenue,
        COALESCE(SUM(ABS(o.cash_collected)), 0.00) AS ola_cash_collection,
        COALESCE(SUM(ABS(o.toll_and_parking)), 0.00) AS ola_toll,
        0.00 AS ola_gst,
        COALESCE(SUM(ABS(o.online_payouts)), 0.00) AS ola_online_payment,
        COALESCE(SUM(ABS(o.portal_incentive)), 0.00) AS ola_incentive,
        COALESCE(SUM(
            ABS(o.operator_bill) + ABS(o.portal_incentive) + ABS(o.toll_and_parking)
            - ABS(o.cash_collected)
        ), 0.00) AS ola_week_os
    FROM public.core_ola_daily o
    CROSS JOIN week_bounds wb
    CROSS JOIN ola_wk_cnt owc
    WHERE o.service_date BETWEEN wb.week_start AND wb.week_end
      AND owc.cnt = 0
    GROUP BY UPPER(REPLACE(REPLACE(o.vehicle_number, ' ', ''), '-', ''))
),
daily_driver_custody AS (
    SELECT
        d.log_date,
        d.vehicle_number,
        d.partner_id,
        d.city,
        d.is_billable_day,
        CASE
            WHEN COALESCE(pcc.car_count, 1) > 1 THEN 'Operator'
            WHEN d.partner_id ILIKE '%OP%' OR d.partner_id ILIKE '%IP%' THEN 'Operator'
            WHEN po.onboarding_type = 'Operator' THEN 'Operator'
            WHEN d.partner_id = 'SYSTEM_ONBOARDED' THEN 'Operator'
            ELSE 'Individual'
        END AS partner_type,
        ROW_NUMBER() OVER (
            PARTITION BY d.log_date, d.vehicle_number
            ORDER BY
                CASE WHEN COALESCE(NULLIF(TRIM(d.partner_id), ''), 'SYSTEM_ONBOARDED') <> 'SYSTEM_ONBOARDED' THEN 0 ELSE 1 END,
                d.net_daily_rent DESC,
                d.id DESC
        ) AS custody_rank
    FROM unified_daily_rent_log d
    LEFT JOIN partner_car_counts pcc ON pcc.partner_id = d.partner_id
    LEFT JOIN public.core_partner_onboarding po ON po.partner_id = d.partner_id
),
dates_with_uber_feed AS (
    SELECT DISTINCT u.operational_date
    FROM public.core_uber_daily u
    CROSS JOIN week_bounds wb
    WHERE u.operational_date BETWEEN wb.week_start AND wb.week_end
),
daily_gps AS (
    SELECT
        g.record_date,
        UPPER(REPLACE(REPLACE(public.fn_clean_gps_vehicle_number(g.vehicle_number), ' ', ''), '-', '')) AS clean_veh,
        SUM(COALESCE(g.distance_km, 0.00)) AS gps_dist
    FROM public.core_gps g
    CROSS JOIN week_bounds wb
    -- Only evaluate GPS distance on dates where Uber daily trip distance feed is loaded
    JOIN dates_with_uber_feed duf ON duf.operational_date = g.record_date
    WHERE g.record_date BETWEEN wb.week_start AND wb.week_end
    GROUP BY g.record_date, UPPER(REPLACE(REPLACE(public.fn_clean_gps_vehicle_number(g.vehicle_number), ' ', ''), '-', ''))
),
daily_uber AS (
    SELECT
        u.operational_date,
        UPPER(REPLACE(REPLACE(u.vehicle_number, ' ', ''), '-', '')) AS clean_veh,
        SUM(COALESCE(u.total_trip_distance_km, 0.00)) AS uber_dist,
        SUM(COALESCE(u.completed_trips, 0)) AS uber_trips,
        SUM(ABS(u.net_fare_earnings)) AS uber_earnings,
        SUM(ABS(u.cash_collected)) AS uber_cash,
        SUM(ABS(u.tolls_refunded)) AS uber_toll,
        SUM(ABS(u.driver_subscription_charge)) AS uber_sub
    FROM public.core_uber_daily u
    CROSS JOIN week_bounds wb
    WHERE u.operational_date BETWEEN wb.week_start AND wb.week_end
    GROUP BY u.operational_date, UPPER(REPLACE(REPLACE(u.vehicle_number, ' ', ''), '-', ''))
),
daily_ola AS (
    SELECT
        o.service_date,
        UPPER(REPLACE(REPLACE(o.vehicle_number, ' ', ''), '-', '')) AS clean_veh,
        SUM(COALESCE(o.total_kms, 0.00)) AS ola_dist,
        SUM(COALESCE(o.completed_trips, 0)) AS ola_trips,
        SUM(ABS(o.operator_bill)) AS ola_revenue,
        SUM(ABS(o.cash_collected)) AS ola_cash,
        SUM(ABS(o.toll_and_parking)) AS ola_toll,
        SUM(ABS(o.online_payouts)) AS ola_payout,
        SUM(ABS(o.portal_incentive)) AS ola_inc
    FROM public.core_ola_daily o
    CROSS JOIN week_bounds wb
    WHERE o.service_date BETWEEN wb.week_start AND wb.week_end
    GROUP BY o.service_date, UPPER(REPLACE(REPLACE(o.vehicle_number, ' ', ''), '-', ''))
),
daily_rapido AS (
    SELECT
        r.operational_date,
        UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) AS clean_veh,
        SUM(COALESCE(r.total_trip_distance_km, 0.00)) AS rapido_dist,
        SUM(COALESCE(r.completed_trips, 0)) AS rapido_trips
    FROM public.core_rapido_daily r
    CROSS JOIN week_bounds wb
    WHERE r.operational_date BETWEEN wb.week_start AND wb.week_end
    GROUP BY r.operational_date, UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', ''))
),
uber_daily_partner_agg AS (
    SELECT
        dc.vehicle_number,
        dc.partner_id,
        COALESCE(SUM(u.uber_trips), 0) AS uber_trips,
        COALESCE(SUM(u.uber_earnings), 0.00) AS uber_total_earnings,
        COALESCE(SUM(u.uber_cash), 0.00) AS uber_cash_collection,
        COALESCE(SUM(u.uber_toll), 0.00) AS uber_toll,
        COALESCE(SUM(u.uber_sub), 0.00) AS uber_driver_sub_charge,
        COALESCE(SUM(u.uber_earnings + u.uber_toll - u.uber_cash - u.uber_sub), 0.00) AS uber_week_os
    FROM daily_driver_custody dc
    JOIN daily_uber u ON u.operational_date = dc.log_date AND u.clean_veh = dc.vehicle_number
    WHERE dc.custody_rank = 1
    GROUP BY dc.vehicle_number, dc.partner_id
),
ola_daily_partner_agg AS (
    SELECT
        dc.vehicle_number,
        dc.partner_id,
        COALESCE(SUM(o.ola_trips), 0) AS ola_trips,
        COALESCE(SUM(o.ola_revenue), 0.00) AS ola_net_revenue,
        COALESCE(SUM(o.ola_cash), 0.00) AS ola_cash_collection,
        COALESCE(SUM(o.ola_toll), 0.00) AS ola_toll,
        0.00 AS ola_gst,
        COALESCE(SUM(o.ola_payout), 0.00) AS ola_online_payment,
        COALESCE(SUM(o.ola_inc), 0.00) AS ola_incentive,
        COALESCE(SUM(o.ola_revenue + o.ola_inc + o.ola_toll - o.ola_cash), 0.00) AS ola_week_os
    FROM daily_driver_custody dc
    JOIN daily_ola o ON o.service_date = dc.log_date AND o.clean_veh = dc.vehicle_number
    WHERE dc.custody_rank = 1
    GROUP BY dc.vehicle_number, dc.partner_id
),
uber_vehicles_with_daily AS (
    SELECT DISTINCT vehicle_number FROM uber_daily_partner_agg
),
ola_vehicles_with_daily AS (
    SELECT DISTINCT vehicle_number FROM ola_daily_partner_agg
),
gps_weekly_partner_agg AS (
    SELECT
        dc.vehicle_number,
        dc.partner_id,
        (ARRAY_AGG(dc.partner_type))[1] AS partner_type,
        (ARRAY_AGG(dc.city))[1] AS city,
        COALESCE(SUM(g.gps_dist), 0.00) AS total_gps_km,
        GREATEST(0.00, COALESCE(SUM(g.gps_dist), 0.00) - COALESCE(SUM(
            CASE WHEN g.gps_dist IS NOT NULL THEN
                (COALESCE(u.uber_dist, 0.00) + COALESCE(o.ola_dist, 0.00) + COALESCE(r.rapido_dist, 0.00))
                + ((COALESCE(u.uber_trips, 0) + COALESCE(o.ola_trips, 0) + COALESCE(r.rapido_trips, 0)) * 3.00)
                + (CASE WHEN dc.is_billable_day THEN 30.00 ELSE 0.00 END)
            ELSE 0.00 END
        ), 0.00)) AS gps_dead_km,
        CASE
            WHEN (ARRAY_AGG(dc.partner_type))[1] = 'Individual'
                 AND UPPER(COALESCE((ARRAY_AGG(dc.city))[1], '')) IN ('BLR', 'BENGALURU', 'BANGALORE')
            THEN ROUND(GREATEST(0.00, COALESCE(SUM(g.gps_dist), 0.00) - COALESCE(SUM(
                CASE WHEN g.gps_dist IS NOT NULL THEN
                    (COALESCE(u.uber_dist, 0.00) + COALESCE(o.ola_dist, 0.00) + COALESCE(r.rapido_dist, 0.00))
                    + ((COALESCE(u.uber_trips, 0) + COALESCE(o.ola_trips, 0) + COALESCE(r.rapido_trips, 0)) * 3.00)
                    + (CASE WHEN dc.is_billable_day THEN 30.00 ELSE 0.00 END)
                ELSE 0.00 END
            ), 0.00)) * 3.00, 2)
            ELSE 0.00
        END AS gps_dead_mile_penalty
    FROM daily_driver_custody dc
    LEFT JOIN daily_gps g ON g.record_date = dc.log_date AND g.clean_veh = dc.vehicle_number
    LEFT JOIN daily_uber u ON u.operational_date = dc.log_date AND u.clean_veh = dc.vehicle_number
    LEFT JOIN daily_ola o ON o.service_date = dc.log_date AND o.clean_veh = dc.vehicle_number
    LEFT JOIN daily_rapido r ON r.operational_date = dc.log_date AND r.clean_veh = dc.vehicle_number
    WHERE dc.custody_rank = 1
    GROUP BY dc.vehicle_number, dc.partner_id
),
adj_exact AS (
    SELECT
        UPPER(REPLACE(REPLACE(c.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
        c.partner_id,
        COALESCE(SUM(CASE WHEN c.financial_direction = 'CREDIT' THEN -c.amount ELSE c.amount END), 0.00) AS net_adj_signed
    FROM public.core_adjustments c
    CROSS JOIN week_bounds wb
    WHERE c.is_deleted = FALSE
      AND c.approval_status = 'Approved'
      AND c.adjustment_date BETWEEN wb.week_start AND wb.week_end
      AND c.vehicle_number IS NOT NULL AND TRIM(c.vehicle_number) <> ''
    GROUP BY UPPER(REPLACE(REPLACE(c.vehicle_number, ' ', ''), '-', '')), c.partner_id
),
adj_phone AS (
    SELECT
        UPPER(REPLACE(REPLACE(c.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
        RIGHT(REGEXP_REPLACE(c.partner_id, '[^0-9]', '', 'g'), 10) AS partner_phone,
        COALESCE(SUM(CASE WHEN c.financial_direction = 'CREDIT' THEN -c.amount ELSE c.amount END), 0.00) AS net_adj_signed
    FROM public.core_adjustments c
    CROSS JOIN week_bounds wb
    WHERE c.is_deleted = FALSE
      AND c.approval_status = 'Approved'
      AND c.adjustment_date BETWEEN wb.week_start AND wb.week_end
      AND c.vehicle_number IS NOT NULL AND TRIM(c.vehicle_number) <> ''
      AND LENGTH(REGEXP_REPLACE(c.partner_id, '[^0-9]', '', 'g')) >= 10
    GROUP BY UPPER(REPLACE(REPLACE(c.vehicle_number, ' ', ''), '-', '')), RIGHT(REGEXP_REPLACE(c.partner_id, '[^0-9]', '', 'g'), 10)
),
adj_veh_total AS (
    SELECT
        UPPER(REPLACE(REPLACE(c.vehicle_number, ' ', ''), '-', '')) AS vehicle_number,
        COALESCE(SUM(CASE WHEN c.financial_direction = 'CREDIT' THEN -c.amount ELSE c.amount END), 0.00) AS net_adj_signed
    FROM public.core_adjustments c
    CROSS JOIN week_bounds wb
    WHERE c.is_deleted = FALSE
      AND c.approval_status = 'Approved'
      AND c.adjustment_date BETWEEN wb.week_start AND wb.week_end
      AND c.vehicle_number IS NOT NULL AND TRIM(c.vehicle_number) <> ''
    GROUP BY UPPER(REPLACE(REPLACE(c.vehicle_number, ' ', ''), '-', ''))
),
adj_unmapped_partner AS (
    SELECT
        c.partner_id,
        COALESCE(SUM(CASE WHEN c.financial_direction = 'CREDIT' THEN -c.amount ELSE c.amount END), 0.00) AS net_unmapped_adj
    FROM public.core_adjustments c
    CROSS JOIN week_bounds wb
    WHERE c.is_deleted = FALSE
      AND c.approval_status = 'Approved'
      AND c.adjustment_date BETWEEN wb.week_start AND wb.week_end
      AND (
          c.vehicle_number IS NULL
          OR TRIM(c.vehicle_number) = ''
          OR UPPER(REPLACE(REPLACE(c.vehicle_number, ' ', ''), '-', '')) NOT IN (
              SELECT UPPER(REPLACE(REPLACE(vehicle_number, ' ', ''), '-', '')) FROM rent_agg
          )
      )
    GROUP BY c.partner_id
),
adj_unmapped_phone AS (
    SELECT
        RIGHT(REGEXP_REPLACE(c.partner_id, '[^0-9]', '', 'g'), 10) AS partner_phone,
        COALESCE(SUM(CASE WHEN c.financial_direction = 'CREDIT' THEN -c.amount ELSE c.amount END), 0.00) AS net_unmapped_adj
    FROM public.core_adjustments c
    CROSS JOIN week_bounds wb
    WHERE c.is_deleted = FALSE
      AND c.approval_status = 'Approved'
      AND c.adjustment_date BETWEEN wb.week_start AND wb.week_end
      AND LENGTH(REGEXP_REPLACE(c.partner_id, '[^0-9]', '', 'g')) >= 10
      AND (
          c.vehicle_number IS NULL
          OR TRIM(c.vehicle_number) = ''
          OR UPPER(REPLACE(REPLACE(c.vehicle_number, ' ', ''), '-', '')) NOT IN (
              SELECT UPPER(REPLACE(REPLACE(vehicle_number, ' ', ''), '-', '')) FROM rent_agg
          )
      )
    GROUP BY RIGHT(REGEXP_REPLACE(c.partner_id, '[^0-9]', '', 'g'), 10)
),
partner_matched_adj AS (
    SELECT
        r.vehicle_number,
        r.partner_id,
        r.partner_phone,
        r.partner_rank,
        r.partner_veh_rank,
        COALESCE(ae.net_adj_signed, ap.net_adj_signed, 0.00) AS direct_matched_adj
    FROM rent_ranked r
    LEFT JOIN adj_exact ae ON UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) = ae.vehicle_number AND r.partner_id = ae.partner_id
    LEFT JOIN adj_phone ap ON UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) = ap.vehicle_number AND r.partner_phone = ap.partner_phone AND ae.vehicle_number IS NULL
),
veh_matched_sums AS (
    SELECT vehicle_number, SUM(direct_matched_adj) AS total_direct_matched
    FROM partner_matched_adj
    GROUP BY vehicle_number
),
final_adj_calc AS (
    SELECT
        pma.vehicle_number,
        pma.partner_id,
        pma.partner_rank,
        pma.partner_veh_rank,
        (CASE
            WHEN pma.partner_rank = 1 THEN pma.direct_matched_adj + (COALESCE(avt.net_adj_signed, 0.00) - vms.total_direct_matched)
            ELSE pma.direct_matched_adj
        END)
        +
        (CASE
            WHEN pma.partner_veh_rank = 1 THEN COALESCE(aup.net_unmapped_adj, auph.net_unmapped_adj, 0.00)
            ELSE 0.00
        END) AS final_adj_signed
    FROM partner_matched_adj pma
    JOIN veh_matched_sums vms ON pma.vehicle_number = vms.vehicle_number
    LEFT JOIN adj_veh_total avt ON UPPER(REPLACE(REPLACE(pma.vehicle_number, ' ', ''), '-', '')) = avt.vehicle_number
    LEFT JOIN adj_unmapped_partner aup ON pma.partner_id = aup.partner_id
    LEFT JOIN adj_unmapped_phone auph ON pma.partner_phone = auph.partner_phone AND aup.partner_id IS NULL
),
challan_direct_custody AS (
    SELECT
        c.clean_veh AS vehicle_number,
        COALESCE(dc.partner_id, 'SYSTEM_ONBOARDED') AS partner_id,
        SUM(c.challan_amount) AS direct_challan_amount
    FROM (
        SELECT
            UPPER(REPLACE(REPLACE(c.vehicle_reg_no, ' ', ''), '-', '')) AS clean_veh,
            c.violation_date,
            COALESCE(NULLIF(c.net_pending_amount, 0.00), c.total_fine_amount) AS challan_amount
        FROM public.core_challans c
        CROSS JOIN week_bounds wb
        WHERE c.violation_date BETWEEN wb.week_start AND wb.week_end
          AND c.is_deleted = FALSE
          AND c.payment_status IN ('UNPAID', 'PARTIALLY_PAID')
          AND c.liability_type IN ('TRAFFIC_FINE', 'STICKER_FINE')
    ) c
    LEFT JOIN daily_driver_custody dc
      ON c.violation_date = dc.log_date
     AND c.clean_veh = dc.vehicle_number
     AND dc.custody_rank = 1
    GROUP BY c.clean_veh, COALESCE(dc.partner_id, 'SYSTEM_ONBOARDED')
),
challan_veh_total AS (
    SELECT
        UPPER(REPLACE(REPLACE(c.vehicle_reg_no, ' ', ''), '-', '')) AS vehicle_number,
        SUM(COALESCE(NULLIF(c.net_pending_amount, 0.00), c.total_fine_amount)) AS total_veh_challan
    FROM public.core_challans c
    CROSS JOIN week_bounds wb
    WHERE c.violation_date BETWEEN wb.week_start AND wb.week_end
      AND c.is_deleted = FALSE
      AND c.payment_status IN ('UNPAID', 'PARTIALLY_PAID')
      AND c.liability_type IN ('TRAFFIC_FINE', 'STICKER_FINE')
    GROUP BY UPPER(REPLACE(REPLACE(c.vehicle_reg_no, ' ', ''), '-', ''))
),
partner_matched_challans AS (
    SELECT
        r.vehicle_number,
        r.partner_id,
        r.partner_rank,
        COALESCE(cdc.direct_challan_amount, 0.00) AS direct_challan
    FROM rent_ranked r
    LEFT JOIN challan_direct_custody cdc
      ON UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) = cdc.vehicle_number
     AND r.partner_id = cdc.partner_id
),
veh_matched_challan_sums AS (
    SELECT vehicle_number, SUM(direct_challan) AS total_direct_matched_challan
    FROM partner_matched_challans
    GROUP BY vehicle_number
),
final_challan_calc AS (
    SELECT
        pmc.vehicle_number,
        pmc.partner_id,
        pmc.partner_rank,
        CASE
            WHEN pmc.partner_rank = 1 THEN pmc.direct_challan + (COALESCE(cvt.total_veh_challan, 0.00) - vmcs.total_direct_matched_challan)
            ELSE pmc.direct_challan
        END AS final_challan_amount
    FROM partner_matched_challans pmc
    JOIN veh_matched_challan_sums vmcs ON pmc.vehicle_number = vmcs.vehicle_number
    LEFT JOIN challan_veh_total cvt ON UPPER(REPLACE(REPLACE(pmc.vehicle_number, ' ', ''), '-', '')) = cvt.vehicle_number
),
existing_hisaab AS (
    SELECT
        vehicle_number,
        partner_id,
        COALESCE(accident_deduction, 0.00) AS accident_deduction
    FROM public.hisaab_vehicle_weekly
    WHERE week_id = :week_id
),
partner_names AS (
    SELECT partner_id, driver_name FROM (
        SELECT partner_id, driver_name, ROW_NUMBER() OVER(PARTITION BY partner_id ORDER BY created_at DESC NULLS LAST) rn
        FROM public.core_partner_onboarding
    ) sub WHERE rn = 1
),
custom_names AS (
    SELECT partner_id, partner_name FROM (
        SELECT partner_id, partner_name, ROW_NUMBER() OVER(PARTITION BY partner_id ORDER BY is_active DESC, custom_plan_id DESC) rn
        FROM public.rental_custom_partner_plans
        WHERE partner_name IS NOT NULL
    ) sub WHERE rn = 1
)
INSERT INTO public.hisaab_vehicle_weekly (
    week_id, week_start, week_end, vehicle_number, partner_id, partner_name,
    city, vehicle_model, rental_plan, allotted_days, onroad_days,
    daily_rent_applied, weekly_lease_rental, weekly_indemnity_fees, net_weekly_lease_rental,
    uber_trips, uber_total_earnings, uber_cash_collection, uber_toll, uber_driver_sub_charge, uber_incentive, uber_week_os,
    ola_trips, ola_net_revenue, ola_cash_collection, ola_toll, ola_gst, ola_online_payment, ola_incentive, ola_week_os,
    adjustment_amount, challan_amount, tds_amount, gps_dead_km, gps_dead_mile_penalty, current_week_os, net_to_collect_from_driver, net_payout_to_driver,
    settlement_status, created_at, updated_at
)
SELECT
    wb.week_id,
    wb.week_start,
    wb.week_end,
    r.vehicle_number,
    r.partner_id,
    COALESCE(pn.driver_name, cn.partner_name, r.partner_id) AS partner_name,
    r.city,
    r.vehicle_model,
    r.rental_plan,
    r.allotted_days,
    r.onroad_days,
    CASE WHEN r.onroad_days > 0 THEN ROUND(r.weekly_lease_rental / r.onroad_days, 2) ELSE 0.00 END AS daily_rent_applied,
    r.weekly_lease_rental,
    r.weekly_indemnity_fees,
    r.net_weekly_lease_rental,
    COALESCE(udpa.uber_trips, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_trips ELSE 0 END, 0),
    COALESCE(udpa.uber_total_earnings, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_total_earnings ELSE 0.00 END, 0.00),
    COALESCE(udpa.uber_cash_collection, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_cash_collection ELSE 0.00 END, 0.00),
    COALESCE(udpa.uber_toll, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_toll ELSE 0.00 END, 0.00),
    COALESCE(udpa.uber_driver_sub_charge, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_driver_sub_charge ELSE 0.00 END, 0.00),
    CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END AS uber_incentive,
    COALESCE(
        udpa.uber_week_os + (CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END),
        CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_week_os ELSE 0.00 END,
        0.00
    ) AS uber_week_os,
    COALESCE(odpa.ola_trips, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_trips ELSE 0 END, 0),
    COALESCE(odpa.ola_net_revenue, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_net_revenue ELSE 0.00 END, 0.00),
    COALESCE(odpa.ola_cash_collection, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_cash_collection ELSE 0.00 END, 0.00),
    COALESCE(odpa.ola_toll, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_toll ELSE 0.00 END, 0.00),
    0.00,
    COALESCE(odpa.ola_online_payment, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_online_payment ELSE 0.00 END, 0.00),
    COALESCE(odpa.ola_incentive, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_incentive ELSE 0.00 END, 0.00),
    COALESCE(odpa.ola_week_os, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_week_os ELSE 0.00 END, 0.00),
    COALESCE(fac.final_adj_signed, 0.00) AS adjustment_amount,
    COALESCE(fcc.final_challan_amount, 0.00) AS challan_amount,
    CASE
        WHEN COALESCE(gdpa.partner_type, 'Individual') = 'Operator'
             AND UPPER(COALESCE(r.city, '')) IN ('BLR', 'BANGALORE', 'BENGALURU', 'HYD', 'HYDERABAD', 'MUM', 'MUMBAI')
        THEN 0.00
        ELSE ROUND(GREATEST(0.00, (
            (
                COALESCE(udpa.uber_total_earnings, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_total_earnings ELSE 0.00 END, 0.00)
                + COALESCE(udpa.uber_toll, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_toll ELSE 0.00 END, 0.00)
                + CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END
                + COALESCE(odpa.ola_net_revenue, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_net_revenue ELSE 0.00 END, 0.00)
                + COALESCE(odpa.ola_toll, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_toll ELSE 0.00 END, 0.00)
                + COALESCE(odpa.ola_incentive, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_incentive ELSE 0.00 END, 0.00)
            )
            - r.net_weekly_lease_rental
        ) * 0.01), 2)
    END AS tds_amount,
    COALESCE(gdpa.gps_dead_km, 0.00) AS gps_dead_km,
    COALESCE(gdpa.gps_dead_mile_penalty, 0.00) AS gps_dead_mile_penalty,
    (
        r.net_weekly_lease_rental
        - (
            COALESCE(
                udpa.uber_week_os + (CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END),
                CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_week_os ELSE 0.00 END,
                0.00
            )
            + COALESCE(odpa.ola_week_os, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_week_os ELSE 0.00 END, 0.00)
        )
        + COALESCE(fcc.final_challan_amount, 0.00)
        + COALESCE(eh.accident_deduction, 0.00)
        + COALESCE(gdpa.gps_dead_mile_penalty, 0.00)
        + CASE
            WHEN COALESCE(gdpa.partner_type, 'Individual') = 'Operator'
                 AND UPPER(COALESCE(r.city, '')) IN ('BLR', 'BANGALORE', 'BENGALURU', 'HYD', 'HYDERABAD', 'MUM', 'MUMBAI')
            THEN 0.00
            ELSE ROUND(GREATEST(0.00, (
                (
                    COALESCE(udpa.uber_total_earnings, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_total_earnings ELSE 0.00 END, 0.00)
                    + COALESCE(udpa.uber_toll, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_toll ELSE 0.00 END, 0.00)
                    + CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END
                    + COALESCE(odpa.ola_net_revenue, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_net_revenue ELSE 0.00 END, 0.00)
                    + COALESCE(odpa.ola_toll, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_toll ELSE 0.00 END, 0.00)
                    + COALESCE(odpa.ola_incentive, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_incentive ELSE 0.00 END, 0.00)
                )
                - r.net_weekly_lease_rental
            ) * 0.01), 2)
          END
        + COALESCE(fac.final_adj_signed, 0.00)
    ) AS current_week_os,
    GREATEST(0.00, (
        r.net_weekly_lease_rental
        - (
            COALESCE(
                udpa.uber_week_os + (CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END),
                CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_week_os ELSE 0.00 END,
                0.00
            )
            + COALESCE(odpa.ola_week_os, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_week_os ELSE 0.00 END, 0.00)
        )
        + COALESCE(fcc.final_challan_amount, 0.00)
        + COALESCE(eh.accident_deduction, 0.00)
        + COALESCE(gdpa.gps_dead_mile_penalty, 0.00)
        + CASE
            WHEN COALESCE(gdpa.partner_type, 'Individual') = 'Operator'
                 AND UPPER(COALESCE(r.city, '')) IN ('BLR', 'BANGALORE', 'BENGALURU', 'HYD', 'HYDERABAD', 'MUM', 'MUMBAI')
            THEN 0.00
            ELSE ROUND(GREATEST(0.00, (
                (
                    COALESCE(udpa.uber_total_earnings, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_total_earnings ELSE 0.00 END, 0.00)
                    + COALESCE(udpa.uber_toll, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_toll ELSE 0.00 END, 0.00)
                    + CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END
                    + COALESCE(odpa.ola_net_revenue, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_net_revenue ELSE 0.00 END, 0.00)
                    + COALESCE(odpa.ola_toll, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_toll ELSE 0.00 END, 0.00)
                    + COALESCE(odpa.ola_incentive, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_incentive ELSE 0.00 END, 0.00)
                )
                - r.net_weekly_lease_rental
            ) * 0.01), 2)
          END
        + COALESCE(fac.final_adj_signed, 0.00)
    )) AS net_to_collect_from_driver,
    GREATEST(0.00, -(
        r.net_weekly_lease_rental
        - (
            COALESCE(
                udpa.uber_week_os + (CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END),
                CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_week_os ELSE 0.00 END,
                0.00
            )
            + COALESCE(odpa.ola_week_os, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_week_os ELSE 0.00 END, 0.00)
        )
        + COALESCE(fcc.final_challan_amount, 0.00)
        + COALESCE(eh.accident_deduction, 0.00)
        + COALESCE(gdpa.gps_dead_mile_penalty, 0.00)
        + CASE
            WHEN COALESCE(gdpa.partner_type, 'Individual') = 'Operator'
                 AND UPPER(COALESCE(r.city, '')) IN ('BLR', 'BANGALORE', 'BENGALURU', 'HYD', 'HYDERABAD', 'MUM', 'MUMBAI')
            THEN 0.00
            ELSE ROUND(GREATEST(0.00, (
                (
                    COALESCE(udpa.uber_total_earnings, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_total_earnings ELSE 0.00 END, 0.00)
                    + COALESCE(udpa.uber_toll, CASE WHEN r.partner_rank = 1 AND uvwd.vehicle_number IS NULL THEN u.uber_toll ELSE 0.00 END, 0.00)
                    + CASE WHEN r.partner_rank = 1 THEN COALESCE(u.uber_incentive, 0.00) ELSE 0.00 END
                    + COALESCE(odpa.ola_net_revenue, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_net_revenue ELSE 0.00 END, 0.00)
                    + COALESCE(odpa.ola_toll, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_toll ELSE 0.00 END, 0.00)
                    + COALESCE(odpa.ola_incentive, CASE WHEN r.partner_rank = 1 AND ovwd.vehicle_number IS NULL THEN o.ola_incentive ELSE 0.00 END, 0.00)
                )
                - r.net_weekly_lease_rental
            ) * 0.01), 2)
          END
        + COALESCE(fac.final_adj_signed, 0.00)
    )) AS net_payout_to_driver,
    'CALCULATED',
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
FROM rent_ranked r
CROSS JOIN week_bounds wb
LEFT JOIN partner_names pn ON r.partner_id = pn.partner_id
LEFT JOIN custom_names cn ON r.partner_id = cn.partner_id
LEFT JOIN uber_daily_partner_agg udpa ON r.vehicle_number = udpa.vehicle_number AND r.partner_id = udpa.partner_id
LEFT JOIN ola_daily_partner_agg odpa ON r.vehicle_number = odpa.vehicle_number AND r.partner_id = odpa.partner_id
LEFT JOIN uber_agg u ON r.partner_rank = 1 AND UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) = u.vehicle_number
LEFT JOIN ola_agg o ON r.partner_rank = 1 AND UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) = o.vehicle_number
LEFT JOIN uber_vehicles_with_daily uvwd ON UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) = uvwd.vehicle_number
LEFT JOIN ola_vehicles_with_daily ovwd ON UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) = ovwd.vehicle_number
LEFT JOIN final_adj_calc fac ON r.vehicle_number = fac.vehicle_number AND r.partner_id = fac.partner_id
LEFT JOIN final_challan_calc fcc ON UPPER(REPLACE(REPLACE(r.vehicle_number, ' ', ''), '-', '')) = fcc.vehicle_number AND r.partner_id = fcc.partner_id
LEFT JOIN existing_hisaab eh ON r.vehicle_number = eh.vehicle_number AND r.partner_id = eh.partner_id
LEFT JOIN gps_weekly_partner_agg gdpa ON r.vehicle_number = gdpa.vehicle_number AND r.partner_id = gdpa.partner_id
ON CONFLICT (week_id, vehicle_number, partner_id) DO UPDATE SET
    partner_name = EXCLUDED.partner_name,
    city = EXCLUDED.city,
    vehicle_model = EXCLUDED.vehicle_model,
    rental_plan = EXCLUDED.rental_plan,
    allotted_days = EXCLUDED.allotted_days,
    onroad_days = EXCLUDED.onroad_days,
    daily_rent_applied = EXCLUDED.daily_rent_applied,
    weekly_lease_rental = EXCLUDED.weekly_lease_rental,
    weekly_indemnity_fees = EXCLUDED.weekly_indemnity_fees,
    net_weekly_lease_rental = EXCLUDED.net_weekly_lease_rental,
    uber_trips = EXCLUDED.uber_trips,
    uber_total_earnings = EXCLUDED.uber_total_earnings,
    uber_cash_collection = EXCLUDED.uber_cash_collection,
    uber_toll = EXCLUDED.uber_toll,
    uber_driver_sub_charge = EXCLUDED.uber_driver_sub_charge,
    uber_incentive = EXCLUDED.uber_incentive,
    uber_week_os = EXCLUDED.uber_week_os,
    ola_trips = EXCLUDED.ola_trips,
    ola_net_revenue = EXCLUDED.ola_net_revenue,
    ola_cash_collection = EXCLUDED.ola_cash_collection,
    ola_toll = EXCLUDED.ola_toll,
    ola_gst = EXCLUDED.ola_gst,
    ola_online_payment = EXCLUDED.ola_online_payment,
    ola_incentive = EXCLUDED.ola_incentive,
    ola_week_os = EXCLUDED.ola_week_os,
    adjustment_amount = EXCLUDED.adjustment_amount,
    challan_amount = EXCLUDED.challan_amount,
    tds_amount = EXCLUDED.tds_amount,
    gps_dead_km = EXCLUDED.gps_dead_km,
    gps_dead_mile_penalty = EXCLUDED.gps_dead_mile_penalty,
    current_week_os = EXCLUDED.current_week_os,
    net_to_collect_from_driver = EXCLUDED.net_to_collect_from_driver,
    net_payout_to_driver = EXCLUDED.net_payout_to_driver,
    settlement_status = 'CALCULATED',
    updated_at = CURRENT_TIMESTAMP
WHERE hisaab_vehicle_weekly.settlement_status <> 'LOCKED';
"""

PARTNER_WEEKLY_HISAAB_ONLY_SQL = """
WITH week_bounds AS (
    SELECT week_id, settlement_year, settlement_week, week_start, week_end
    FROM public.hisaab_settlement_weeks
    WHERE week_id = :week_id
),
veh_summary AS (
    SELECT
        v.partner_id,
        (ARRAY_AGG(v.partner_name ORDER BY v.onroad_days DESC, v.net_weekly_lease_rental DESC))[1] AS partner_name,
        (ARRAY_AGG(v.city ORDER BY v.onroad_days DESC, v.net_weekly_lease_rental DESC))[1] AS city,
        CASE
            WHEN COUNT(DISTINCT v.vehicle_number) > 1 THEN 'Operator'
            WHEN v.partner_id ILIKE '%IP%' OR v.partner_id ILIKE '%OP%' THEN 'Operator'
            ELSE 'Individual'
        END AS partner_type,
        COUNT(DISTINCT v.vehicle_number) AS allotted_cars_count,
        SUM(v.onroad_days) AS total_onroad_days,
        SUM(COALESCE(v.uber_trips, 0) + COALESCE(v.ola_trips, 0)) AS total_trips,
        SUM(COALESCE(v.net_weekly_lease_rental, 0.00)) AS total_net_rent_billed,
        SUM(COALESCE(v.uber_total_earnings, 0.00) + COALESCE(v.ola_net_revenue, 0.00)) AS total_platform_earnings,
        SUM(COALESCE(v.uber_cash_collection, 0.00) + COALESCE(v.ola_cash_collection, 0.00)) AS total_cash_collected,
        SUM(COALESCE(v.uber_incentive, 0.00) + COALESCE(v.ola_incentive, 0.00)) AS total_platform_incentives,
        SUM(COALESCE(v.adjustment_amount, 0.00)) AS total_adjustments,
        SUM(COALESCE(v.challan_amount, 0.00)) AS total_challans,
        SUM(COALESCE(v.accident_deduction, 0.00)) AS total_accidents,
        SUM(COALESCE(v.tds_amount, 0.00)) AS total_tds,
        SUM(COALESCE(v.gps_dead_mile_penalty, 0.00)) AS total_dead_mile_penalty,
        SUM(COALESCE(v.current_week_os, 0.00)) AS current_week_os
    FROM public.hisaab_vehicle_weekly v
    WHERE v.week_id = :week_id
    GROUP BY v.partner_id
),
existing_payments AS (
    SELECT
        partner_id,
        COALESCE(amount_paid_during_week, 0.00) AS amount_paid_during_week
    FROM public.hisaab_partner_weekly
    WHERE week_id = :week_id
)
INSERT INTO public.hisaab_partner_weekly (
    settlement_year, settlement_week, week_id, week_start, week_end,
    partner_id, partner_name, partner_type, city,
    allotted_cars_count, total_onroad_days, total_trips,
    total_net_rent_billed, total_platform_earnings, total_cash_collected,
    total_platform_incentives, total_adjustments, total_challans,
    total_accidents, total_tds, total_dead_mile_penalty,
    current_week_os, previous_outstanding, amount_paid_during_week,
    prior_period_adjustments, security_deposit_target, security_deposit_paid,
    deposit_deduction_current_week, pending_deposit, total_outstanding,
    net_bank_payout, net_amount_to_collect, settlement_status,
    payout_account_number, payout_ifsc, updated_at
)
SELECT
    wb.settlement_year,
    wb.settlement_week,
    wb.week_id,
    wb.week_start,
    wb.week_end,
    v.partner_id,
    COALESCE(v.partner_name, dr.driver_name, v.partner_id) AS partner_name,
    v.partner_type,
    COALESCE(v.city, 'HYD') AS city,
    v.allotted_cars_count,
    v.total_onroad_days,
    v.total_trips,
    v.total_net_rent_billed,
    v.total_platform_earnings,
    v.total_cash_collected,
    v.total_platform_incentives,
    v.total_adjustments,
    v.total_challans,
    v.total_accidents,
    v.total_tds,
    v.total_dead_mile_penalty,
    v.current_week_os,
    0.00 AS previous_outstanding,
    COALESCE(ep.amount_paid_during_week, 0.00) AS amount_paid_during_week,
    0.00 AS prior_period_adjustments,
    COALESCE(dr.security_deposit, 5000.00) AS security_deposit_target,
    COALESCE(dr.security_deposit, 5000.00) AS security_deposit_paid,
    0.00 AS deposit_deduction_current_week,
    0.00 AS pending_deposit,
    (v.current_week_os - COALESCE(ep.amount_paid_during_week, 0.00)) AS total_outstanding,
    ABS(LEAST(0, (v.current_week_os - COALESCE(ep.amount_paid_during_week, 0.00)))) AS net_bank_payout,
    GREATEST(0, (v.current_week_os - COALESCE(ep.amount_paid_during_week, 0.00))) AS net_amount_to_collect,
    'DRAFT' AS settlement_status,
    dr.account_number AS payout_account_number,
    dr.ifsc_code AS payout_ifsc,
    CURRENT_TIMESTAMP AS updated_at
FROM veh_summary v
CROSS JOIN week_bounds wb
LEFT JOIN existing_payments ep ON v.partner_id = ep.partner_id
LEFT JOIN LATERAL (
    SELECT account_number, ifsc_code, driver_name, security_deposit
    FROM public.core_partner_onboarding
    WHERE partner_id = v.partner_id
    LIMIT 1
) dr ON TRUE
ON CONFLICT (week_id, partner_id) DO UPDATE SET
    partner_name = COALESCE(EXCLUDED.partner_name, public.hisaab_partner_weekly.partner_name),
    partner_type = EXCLUDED.partner_type,
    city = EXCLUDED.city,
    allotted_cars_count = EXCLUDED.allotted_cars_count,
    total_onroad_days = EXCLUDED.total_onroad_days,
    total_trips = EXCLUDED.total_trips,
    total_net_rent_billed = EXCLUDED.total_net_rent_billed,
    total_platform_earnings = EXCLUDED.total_platform_earnings,
    total_cash_collected = EXCLUDED.total_cash_collected,
    total_platform_incentives = EXCLUDED.total_platform_incentives,
    total_adjustments = EXCLUDED.total_adjustments,
    total_challans = EXCLUDED.total_challans,
    total_accidents = EXCLUDED.total_accidents,
    total_tds = EXCLUDED.total_tds,
    total_dead_mile_penalty = EXCLUDED.total_dead_mile_penalty,
    current_week_os = EXCLUDED.current_week_os,
    previous_outstanding = 0.00,
    prior_period_adjustments = 0.00,
    total_outstanding = (EXCLUDED.current_week_os - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00)),
    net_bank_payout = ABS(LEAST(0, (EXCLUDED.current_week_os - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00)))),
    net_amount_to_collect = GREATEST(0, (EXCLUDED.current_week_os - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00))),
    payout_account_number = COALESCE(EXCLUDED.payout_account_number, public.hisaab_partner_weekly.payout_account_number),
    payout_ifsc = COALESCE(EXCLUDED.payout_ifsc, public.hisaab_partner_weekly.payout_ifsc),
    updated_at = CURRENT_TIMESTAMP;
"""


def run_onetime_hisaab_only(weeks: list[str], commit: bool = False):
    for w in weeks:
        assert_week_not_protected(w)

    mode = "COMMIT MODE (HISAAB TABLES ONLY)" if commit else "READ-ONLY DRY-RUN (ROLLBACK MODE)"
    print(f"\n{'=' * 145}")
    print(f"[ONE-TIME HISAAB-ONLY BACKFILL] {mode} | Weeks: {weeks}")
    print(f"Guarantee: ZERO writes to core_*, sheet_*, july_*, daily_rent_log, or protected Hisaab weeks.")
    print(f"{'=' * 145}")

    with engine.connect() as conn:
        verify_roll_forward_disabled(conn)
        pre_snap = snapshot_protected_weeks(conn)
        conn.rollback()

        print(f"{'Week':10} | {'Period':23} | {'Vehs':>5} | {'Prtnr':>5} | {'Net Rent':>11} | {'Uber Earn':>11} | {'Uber Inc':>9} | {'Ola Rev':>10} | {'Adjustmts':>10} | {'Challans':>9} | {'GPS Pen':>9} | {'Current O/S':>12}")
        print("-" * 145)

        for w in weeks:
            trans = conn.begin()
            try:
                conn.execute(text(VEHICLE_WEEKLY_HISAAB_ONLY_SQL), {"week_id": w})
                conn.execute(text(PARTNER_WEEKLY_HISAAB_ONLY_SQL), {"week_id": w})

                r = conn.execute(text("""
                    SELECT
                        MIN(week_start)::text || ' to ' || MAX(week_end)::text AS period,
                        COUNT(*) AS veh_rows,
                        COALESCE(SUM(net_weekly_lease_rental), 0.00) AS rent,
                        COALESCE(SUM(uber_total_earnings), 0.00) AS uber,
                        COALESCE(SUM(uber_incentive), 0.00) AS u_inc,
                        COALESCE(SUM(ola_net_revenue), 0.00) AS ola,
                        COALESCE(SUM(adjustment_amount), 0.00) AS adj,
                        COALESCE(SUM(challan_amount), 0.00) AS chal,
                        COALESCE(SUM(gps_dead_mile_penalty), 0.00) AS gps_pen,
                        COALESCE(SUM(current_week_os), 0.00) AS os
                    FROM public.hisaab_vehicle_weekly
                    WHERE week_id = :w
                """), {"w": w}).fetchone()

                p_cnt = conn.execute(text("""
                    SELECT COUNT(*) FROM public.hisaab_partner_weekly WHERE week_id = :w
                """), {"w": w}).scalar()

                print(f"{w:10} | {str(r[0]):23} | {r[1]:5d} | {p_cnt:5d} | {float(r[2]):11.2f} | {float(r[3]):11.2f} | {float(r[4]):9.2f} | {float(r[5]):10.2f} | {float(r[6]):10.2f} | {float(r[7]):9.2f} | {float(r[8]):9.2f} | {float(r[9]):12.2f}")

                if commit:
                    assert_protected_weeks_unchanged(conn, pre_snap)
                    trans.commit()
                else:
                    trans.rollback()
            except Exception:
                trans.rollback()
                raise

        if commit:
            assert_protected_weeks_unchanged(conn, pre_snap)
            conn.rollback()
            print(f"\n[SUCCESS] Committed Hisaab-only backfill for {len(weeks)} week(s). All protected weeks verified 100% untouched.")
        else:
            print(f"\n[DRY-RUN COMPLETE] Rolled back 100% cleanly. ZERO rows written to database. Pass --commit when ready to persist.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One-Time Hisaab-Tables-Only Backfill Generator")
    parser.add_argument("--weeks", nargs="+", default=DEFAULT_BACKFILL_WEEKS, help="Target week IDs (e.g. CY26WK35 CY26WK34)")
    parser.add_argument("--commit", action="store_true", help="Commit to hisaab_vehicle_weekly and hisaab_partner_weekly (default: Rollback Dry-Run)")
    args = parser.parse_args()
    run_onetime_hisaab_only(args.weeks, commit=args.commit)
