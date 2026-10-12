-- sp_sync_hisaab_cumulative_cutoff.sql
-- ============================================================================
-- Complete Stored Procedure for Cutoff-Based Cumulative Rolling Ledger
-- ============================================================================

CREATE OR REPLACE PROCEDURE public.sp_sync_hisaab_cumulative_cutoff(
    IN p_week_id VARCHAR,
    IN p_partner VARCHAR DEFAULT NULL
)
LANGUAGE plpgsql
AS $procedure$
DECLARE
    v_week_start DATE;
    v_week_end DATE;
    v_year INT;
    v_week_num INT;
    v_is_locked BOOLEAN;
    v_prev_week_id VARCHAR;
    v_prev_week_end DATE;
    v_enable_roll_forward BOOLEAN := FALSE;
    v_cutoff_week_id VARCHAR;
BEGIN
    IF p_week_id IS NULL THEN
        RAISE EXCEPTION 'p_week_id is required.';
    END IF;

    -- Ensure week metadata exists
    PERFORM public.fn_ensure_hisaab_week(p_week_id, NULL);

    SELECT is_locked, week_start, week_end, settlement_year, settlement_week
    INTO v_is_locked, v_week_start, v_week_end, v_year, v_week_num
    FROM public.hisaab_settlement_weeks
    WHERE week_id = p_week_id;

    -- Never mutate weeks that are already closed and locked
    IF v_is_locked = TRUE THEN
        RAISE NOTICE 'Week % is locked. Skipping sync.', p_week_id;
        RETURN;
    END IF;

    -- Read configuration flags
    SELECT (COALESCE(config_value, 'false') = 'true') INTO v_enable_roll_forward
    FROM public.hisaab_system_config
    WHERE config_key = 'enable_roll_forward';

    SELECT config_value INTO v_cutoff_week_id
    FROM public.hisaab_system_config
    WHERE config_key = 'cutoff_week_id';

    -- Compute previous calendar week identifier
    v_prev_week_end := v_week_start - INTERVAL '1 day';
    v_prev_week_id := 'CY' || to_char(v_prev_week_end, 'IY') || 'WK' || to_char(v_prev_week_end, 'IW');

    -- 1. Aggregate current week vehicles for each partner
    WITH veh_summary AS (
        SELECT 
            v.partner_id,
            MAX(v.partner_name) AS partner_name,
            MAX(v.partner_type) AS partner_type,
            MAX(v.city) AS city,
            COUNT(DISTINCT v.vehicle_number)::INT AS allotted_cars_count,
            SUM(v.onroad_days)::INT AS total_onroad_days,
            SUM(v.uber_trips + v.ola_trips + v.rapido_trips)::INT AS total_trips,
            SUM(v.net_weekly_lease_rental) AS total_net_rent_billed,
            SUM(v.uber_total_earnings + v.ola_net_revenue + v.rapido_net_revenue) AS total_platform_earnings,
            SUM(ABS(COALESCE(v.uber_cash_collection, 0.00)) + ABS(COALESCE(v.ola_cash_collection, 0.00)) + ABS(COALESCE(v.rapido_cash_collected, 0.00))) AS total_cash_collected,
            SUM(v.weekly_platform_incentive) AS total_platform_incentives,
            SUM(v.vehicle_adjustments) AS total_adjustments,
            SUM(v.challan_amount) AS total_challans,
            SUM(v.accident_penalties) AS total_accidents,
            SUM(v.tds_amount) AS total_tds,
            SUM(v.current_week_os) AS current_week_os
        FROM public.hisaab_vehicle_weekly v
        WHERE v.week_id = p_week_id
          AND (p_partner IS NULL OR v.partner_id = p_partner)
        GROUP BY v.partner_id
    ),
    
    -- 2. Pull approved adjustments logged for this settlement week
    prior_adj AS (
        SELECT 
            partner_id,
            COALESCE(SUM(CASE WHEN polarity = 'DEBIT' THEN amount ELSE -amount END), 0.00) AS prior_period_adjustments
        FROM public.hisaab_adjustments_ledger
        WHERE settlement_week_id = p_week_id 
          AND is_prior_period = TRUE
          AND approval_status = 'Approved'
          AND (p_partner IS NULL OR partner_id = p_partner)
        GROUP BY partner_id
    ),

    -- 3. DETERMINE PREVIOUS OUTSTANDING BALANCE:
    --    Case A: If this week is the cutoff week, use the verified snapshot from hisaab_partner_opening_balances.
    --    Case B: If this week is AFTER the cutoff and roll-forward is enabled, carry forward
    --            the previous week's unsettled total_outstanding.
    --    Case C: Otherwise default to 0.00.
    prev_dues AS (
        SELECT 
            ap.partner_id,
            CASE 
                -- Priority 1: Direct cutoff snapshot override for this week
                WHEN op.opening_balance_due IS NOT NULL THEN op.opening_balance_due
                
                -- Priority 2: Post-cutoff cumulative roll-forward from previous week
                WHEN v_enable_roll_forward = TRUE 
                     AND pw.total_outstanding IS NOT NULL 
                     AND p_week_id >= v_cutoff_week_id THEN 
                     GREATEST(0.00, pw.total_outstanding - COALESCE(pw.amount_paid_during_week, 0.00))
                
                ELSE 0.00 
            END AS previous_outstanding
        FROM (
            SELECT partner_id FROM veh_summary
            UNION
            SELECT partner_id FROM prior_adj
            UNION
            SELECT partner_id FROM public.hisaab_partner_opening_balances WHERE effective_week_id = p_week_id
            UNION
            SELECT partner_id FROM public.hisaab_partner_weekly 
            WHERE v_enable_roll_forward = TRUE 
              AND week_id = v_prev_week_id 
              AND total_outstanding > 0
        ) ap
        LEFT JOIN public.hisaab_partner_opening_balances op 
            ON op.partner_id = ap.partner_id AND op.effective_week_id = p_week_id
        LEFT JOIN public.hisaab_partner_weekly pw 
            ON pw.week_id = v_prev_week_id AND pw.partner_id = ap.partner_id
        WHERE (p_partner IS NULL OR ap.partner_id = p_partner)
    ),

    -- 4. Preserve payments already made during this week
    existing_payments AS (
        SELECT 
            partner_id,
            COALESCE(amount_paid_during_week, 0.00) AS amount_paid_during_week
        FROM public.hisaab_partner_weekly
        WHERE week_id = p_week_id
          AND (p_partner IS NULL OR partner_id = p_partner)
    ),

    all_partners AS (
        SELECT partner_id FROM prev_dues
        WHERE previous_outstanding <> 0.00 
           OR partner_id IN (SELECT partner_id FROM veh_summary)
           OR partner_id IN (SELECT partner_id FROM prior_adj)
    )

    -- 5. UPSERT into hisaab_partner_weekly
    INSERT INTO public.hisaab_partner_weekly (
        settlement_year,
        settlement_week,
        week_id,
        week_start,
        week_end,
        partner_id,
        partner_name,
        partner_type,
        city,
        allotted_cars_count,
        total_onroad_days,
        total_trips,
        total_net_rent_billed,
        total_platform_earnings,
        total_cash_collected,
        total_platform_incentives,
        total_adjustments,
        total_challans,
        total_accidents,
        total_tds,
        current_week_os,
        previous_outstanding,
        amount_paid_during_week,
        prior_period_adjustments,
        security_deposit_target,
        security_deposit_paid,
        deposit_deduction_current_week,
        pending_deposit,
        total_outstanding,
        net_bank_payout,
        net_amount_to_collect,
        settlement_status,
        payout_account_number,
        payout_ifsc,
        updated_at
    )
    SELECT 
        v_year,
        v_week_num,
        p_week_id,
        v_week_start,
        v_week_end,
        ap.partner_id,
        COALESCE(v.partner_name, dr.driver_name, ap.partner_id) AS partner_name,
        COALESCE(v.partner_type, CASE WHEN ap.partner_id ILIKE '%IP%' OR ap.partner_id ILIKE '%OP%' THEN 'Operator' ELSE 'Individual' END) AS partner_type,
        COALESCE(v.city, 'HYD') AS city,
        COALESCE(v.allotted_cars_count, 0) AS allotted_cars_count,
        COALESCE(v.total_onroad_days, 0) AS total_onroad_days,
        COALESCE(v.total_trips, 0) AS total_trips,
        COALESCE(v.total_net_rent_billed, 0.00) AS total_net_rent_billed,
        COALESCE(v.total_platform_earnings, 0.00) AS total_platform_earnings,
        COALESCE(v.total_cash_collected, 0.00) AS total_cash_collected,
        COALESCE(v.total_platform_incentives, 0.00) AS total_platform_incentives,
        COALESCE(v.total_adjustments, 0.00) AS total_adjustments,
        COALESCE(v.total_challans, 0.00) AS total_challans,
        COALESCE(v.total_accidents, 0.00) AS total_accidents,
        COALESCE(v.total_tds, 0.00) AS total_tds,
        COALESCE(v.current_week_os, 0.00) AS current_week_os,
        COALESCE(pd.previous_outstanding, 0.00) AS previous_outstanding,
        COALESCE(ep.amount_paid_during_week, 0.00) AS amount_paid_during_week,
        COALESCE(pa.prior_period_adjustments, 0.00) AS prior_period_adjustments,
        COALESCE(dr.security_deposit, 5000.00) AS security_deposit_target,
        COALESCE(dr.security_deposit, 5000.00) AS security_deposit_paid,
        0.00 AS deposit_deduction_current_week,
        0.00 AS pending_deposit,
        
        -- Cumulative Total Outstanding:
        -- = Current Week Net Due + Carried Forward Previous Outstanding + Prior Adjustments - Current Payments
        (COALESCE(v.current_week_os, 0.00) + COALESCE(pd.previous_outstanding, 0.00) + COALESCE(pa.prior_period_adjustments, 0.00) - COALESCE(ep.amount_paid_during_week, 0.00)) AS total_outstanding,
        
        -- Payout (if negative / LetzRyd owes driver)
        ABS(LEAST(0, (COALESCE(v.current_week_os, 0.00) + COALESCE(pd.previous_outstanding, 0.00) + COALESCE(pa.prior_period_adjustments, 0.00) - COALESCE(ep.amount_paid_during_week, 0.00)))) AS net_bank_payout,
        
        -- To Collect (if positive / driver owes LetzRyd)
        GREATEST(0, (COALESCE(v.current_week_os, 0.00) + COALESCE(pd.previous_outstanding, 0.00) + COALESCE(pa.prior_period_adjustments, 0.00) - COALESCE(ep.amount_paid_during_week, 0.00))) AS net_amount_to_collect,
        
        'DRAFT' AS settlement_status,
        dr.account_number AS payout_account_number,
        dr.ifsc_code AS payout_ifsc,
        CURRENT_TIMESTAMP AS updated_at
    FROM all_partners ap
    LEFT JOIN veh_summary v ON ap.partner_id = v.partner_id
    LEFT JOIN prior_adj pa ON ap.partner_id = pa.partner_id
    LEFT JOIN prev_dues pd ON ap.partner_id = pd.partner_id
    LEFT JOIN existing_payments ep ON ap.partner_id = ep.partner_id
    LEFT JOIN LATERAL (
        SELECT account_number, ifsc_code, driver_name, security_deposit FROM public.core_partner_onboarding 
        WHERE partner_id = ap.partner_id LIMIT 1
    ) dr ON TRUE
    ON CONFLICT (week_id, partner_id) DO UPDATE SET
        partner_name = COALESCE(EXCLUDED.partner_name, public.hisaab_partner_weekly.partner_name),
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
        current_week_os = EXCLUDED.current_week_os,
        previous_outstanding = EXCLUDED.previous_outstanding,
        prior_period_adjustments = EXCLUDED.prior_period_adjustments,
        amount_paid_during_week = COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00),
        security_deposit_target = EXCLUDED.security_deposit_target,
        security_deposit_paid = EXCLUDED.security_deposit_paid,
        
        -- Recalculate with preserved payments
        total_outstanding = (EXCLUDED.current_week_os + EXCLUDED.previous_outstanding + EXCLUDED.prior_period_adjustments - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00)),
        net_bank_payout = ABS(LEAST(0, (EXCLUDED.current_week_os + EXCLUDED.previous_outstanding + EXCLUDED.prior_period_adjustments - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00)))),
        net_amount_to_collect = GREATEST(0, (EXCLUDED.current_week_os + EXCLUDED.previous_outstanding + EXCLUDED.prior_period_adjustments - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00))),
        payout_account_number = COALESCE(EXCLUDED.payout_account_number, public.hisaab_partner_weekly.payout_account_number),
        payout_ifsc = COALESCE(EXCLUDED.payout_ifsc, public.hisaab_partner_weekly.payout_ifsc),
        updated_at = CURRENT_TIMESTAMP
    WHERE public.hisaab_partner_weekly.settlement_status IN ('DRAFT', 'OPEN');

END;
$procedure$;
