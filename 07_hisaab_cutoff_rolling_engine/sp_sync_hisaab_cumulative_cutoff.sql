-- sp_sync_hisaab_cumulative_cutoff.sql
-- ============================================================================
-- CUTOFF-BASED CUMULATIVE ROLLING HISAAB SYNC PROCEDURE
-- LetzRyd Hisaab Engine
-- ============================================================================
--
-- PURPOSE:
-- Synchronizes and recalculates cumulative partner weekly settlement balances
-- (public.hisaab_partner_weekly) for a target settlement week.
--
-- ENFORCES:
-- 1. MONDAY 12:00 PM NOON CUTOFF IMMUTABILITY:
--    Once is_locked = TRUE or current timestamp crosses lock_cutoff_at
--    (Monday 12:00 PM Noon IST), the week is frozen and skips calculation.
-- 2. DAY-ZERO BASELINE INGESTION:
--    For cutoff week 'CY26WK41', opening_balance_due from
--    public.hisaab_partner_opening_balances overrides previous_outstanding.
-- 3. SIGN-PRESERVING MULTI-WEEK ROLL-FORWARD:
--    Carries forward net debt (>0) AND advance credit (<0) from pw.total_outstanding.
-- 4. PARTIAL, FULL & ADVANCE PAYMENTS:
--    Deducts payments made via UPI/Cashfree. Advance payments roll forward as credit.
-- 5. LATE PRIOR-PERIOD ADJUSTMENTS:
--    Aggregates approved prior-period debits and credits from
--    public.hisaab_adjustments_ledger directly into total_outstanding.
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
    v_lock_cutoff_at TIMESTAMPTZ;
    v_prev_week_id VARCHAR;
    v_prev_week_end DATE;
    v_enable_roll_forward BOOLEAN := FALSE;
    v_cutoff_week_id VARCHAR;
BEGIN
    IF p_week_id IS NULL THEN
        RAISE EXCEPTION 'p_week_id is required.';
    END IF;

    -- Ensure week metadata exists in hisaab_settlement_weeks
    PERFORM public.fn_ensure_hisaab_week(p_week_id, NULL);

    SELECT is_locked, week_start, week_end, settlement_year, settlement_week, lock_cutoff_at
    INTO v_is_locked, v_week_start, v_week_end, v_year, v_week_num, v_lock_cutoff_at
    FROM public.hisaab_settlement_weeks
    WHERE week_id = p_week_id
    FOR UPDATE;

    -- If lock_cutoff_at is NULL, calculate Monday 12:00 PM (Noon) IST:
    -- Settlement week ends Sunday (v_week_end). Following Monday noon is (v_week_end + 1 day) at 12:00 PM IST.
    IF v_lock_cutoff_at IS NULL THEN
        v_lock_cutoff_at := ((v_week_end + INTERVAL '1 day')::date || ' 12:00:00+05:30')::timestamptz;
        UPDATE public.hisaab_settlement_weeks
        SET lock_cutoff_at = v_lock_cutoff_at
        WHERE week_id = p_week_id;
    END IF;

    -- =========================================================================
    -- LOCK ENFORCEMENT AT MONDAY 12:00 PM (NOON) IST (lock_cutoff_at)
    -- =========================================================================
    IF v_is_locked = TRUE OR CURRENT_TIMESTAMP >= v_lock_cutoff_at THEN
        IF v_is_locked = FALSE AND CURRENT_TIMESTAMP >= v_lock_cutoff_at THEN
            UPDATE public.hisaab_settlement_weeks
            SET is_locked = TRUE,
                locked_at = CURRENT_TIMESTAMP,
                locked_by = 'SYSTEM_MONDAY_12_NOON_LOCK',
                notes = COALESCE(notes || '; ', '') || 'Auto-locked by Monday 12:00 PM Noon IST cutoff threshold'
            WHERE week_id = p_week_id;

            UPDATE public.hisaab_partner_weekly
            SET settlement_status = 'LOCKED', updated_at = CURRENT_TIMESTAMP
            WHERE week_id = p_week_id AND settlement_status <> 'LOCKED';

            v_is_locked := TRUE;
        END IF;

        RAISE NOTICE 'Settlement week % is locked (Cutoff was: %). Week is completely immutable. Skipping sync.', 
            p_week_id, v_lock_cutoff_at;
        RETURN;
    END IF;

    -- Read configuration flags from hisaab_system_config
    SELECT (COALESCE(config_value, 'false') = 'true') INTO v_enable_roll_forward
    FROM public.hisaab_system_config
    WHERE config_key = 'enable_roll_forward';

    SELECT config_value INTO v_cutoff_week_id
    FROM public.hisaab_system_config
    WHERE config_key = 'cutoff_week_id';

    v_cutoff_week_id := COALESCE(v_cutoff_week_id, 'CY26WK41');

    -- Compute previous calendar week identifier (e.g. CY26WK40 for CY26WK41)
    v_prev_week_end := v_week_start - INTERVAL '1 day';
    v_prev_week_id := 'CY' || to_char(v_prev_week_end, 'IY') || 'WK' || to_char(v_prev_week_end, 'IW');

    -- 1. Aggregate current week vehicles for each partner
    WITH veh_summary AS (
        SELECT 
            v.partner_id,
            MAX(v.partner_name) AS partner_name,
            MAX(CASE WHEN v.partner_id ILIKE '%IP%' OR v.partner_id ILIKE '%OP%' THEN 'Operator' ELSE 'Individual' END) AS partner_type,
            MAX(v.city) AS city,
            COUNT(DISTINCT v.vehicle_number)::INT AS allotted_cars_count,
            SUM(v.onroad_days)::INT AS total_onroad_days,
            SUM(COALESCE(v.uber_trips, 0) + COALESCE(v.ola_trips, 0))::INT AS total_trips,
            SUM(COALESCE(v.net_weekly_lease_rental, 0.00)) AS total_net_rent_billed,
            SUM(COALESCE(v.uber_total_earnings, 0.00) + COALESCE(v.ola_net_revenue, 0.00)) AS total_platform_earnings,
            SUM(ABS(COALESCE(v.uber_cash_collection, 0.00)) + ABS(COALESCE(v.ola_cash_collection, 0.00))) AS total_cash_collected,
            SUM(COALESCE(v.uber_incentive, 0.00) + COALESCE(v.ola_incentive, 0.00)) AS total_platform_incentives,
            SUM(COALESCE(v.adjustment_amount, 0.00)) AS total_adjustments,
            SUM(COALESCE(v.challan_amount, 0.00)) AS total_challans,
            SUM(COALESCE(v.accident_deduction, 0.00)) AS total_accidents,
            SUM(COALESCE(v.tds_amount, 0.00)) AS total_tds,
            SUM(COALESCE(v.gps_dead_mile_penalty, 0.00)) AS total_dead_mile_penalty,
            SUM(COALESCE(v.current_week_os, 0.00)) AS current_week_os
        FROM public.hisaab_vehicle_weekly v
        WHERE v.week_id = p_week_id
          AND (p_partner IS NULL OR v.partner_id = p_partner)
        GROUP BY v.partner_id
    ),
    
    -- 2. Pull approved prior-period adjustments logged for this settlement week
    prior_adj AS (
        SELECT 
            partner_id,
            COALESCE(SUM(CASE 
                WHEN UPPER(TRIM(COALESCE(polarity, 'DEBIT'))) IN ('CREDIT', 'CR') THEN -ABS(amount) 
                WHEN UPPER(TRIM(COALESCE(polarity, 'DEBIT'))) IN ('DEBIT', 'DR') THEN ABS(amount) 
                ELSE 0.00 
            END), 0.00) AS prior_period_adjustments
        FROM public.hisaab_adjustments_ledger
        WHERE settlement_week_id = p_week_id 
          AND is_prior_period = TRUE
          AND UPPER(TRIM(COALESCE(approval_status, ''))) = 'APPROVED'
          AND (p_partner IS NULL OR partner_id = p_partner)
        GROUP BY partner_id
    ),

    -- 3. Pull approved current-period adjustments logged directly in ledger
    current_ledger_adj AS (
        SELECT 
            partner_id,
            COALESCE(SUM(CASE 
                WHEN UPPER(TRIM(COALESCE(polarity, 'DEBIT'))) IN ('CREDIT', 'CR') THEN -ABS(amount) 
                WHEN UPPER(TRIM(COALESCE(polarity, 'DEBIT'))) IN ('DEBIT', 'DR') THEN ABS(amount) 
                ELSE 0.00 
            END), 0.00) AS current_ledger_adjustments
        FROM public.hisaab_adjustments_ledger
        WHERE settlement_week_id = p_week_id 
          AND is_prior_period = FALSE
          AND UPPER(TRIM(COALESCE(approval_status, ''))) = 'APPROVED'
          AND (p_partner IS NULL OR partner_id = p_partner)
        GROUP BY partner_id
    ),

    -- 4. DETERMINE PREVIOUS OUTSTANDING BALANCE:
    --    Priority 1: opening_balance_due overrides previous_outstanding for week 'CY26WK41' (from hisaab_partner_opening_balances).
    --    Priority 2: Immediate week after cutoff (e.g. CY26WK42 where previous week was the cutoff week):
    --                rolls forward from pw.total_outstanding, or falls back to cutoff opening balance if driver was inactive in week 41.
    --    Priority 3: Subsequent rolling weeks (CY26WK43+ where previous week > cutoff week):
    --                rolls strictly from immediate previous week pw.total_outstanding (defaults to 0.00).
    prev_dues AS (
        SELECT 
            ap.partner_id,
            CASE 
                -- Priority 1: opening_balance_due overrides previous_outstanding for cutoff week 'CY26WK41'
                WHEN p_week_id = v_cutoff_week_id THEN COALESCE(op.opening_balance_due, 0.00)
                WHEN op.opening_balance_due IS NOT NULL THEN op.opening_balance_due
                
                -- Priority 2: Immediate week after cutoff (where previous week was the cutoff week)
                WHEN v_enable_roll_forward = TRUE 
                     AND v_prev_week_id = v_cutoff_week_id THEN 
                     CASE 
                         WHEN pw.bank_utr_reference IS NOT NULL AND pw.total_outstanding < 0.00 THEN 0.00
                         ELSE COALESCE(pw.total_outstanding, prev_op.opening_balance_due, 0.00)
                     END
                
                -- Priority 3: Subsequent rolling weeks (CY26WK43 and beyond)
                WHEN v_enable_roll_forward = TRUE 
                     AND v_prev_week_id > v_cutoff_week_id THEN 
                     CASE 
                         WHEN pw.bank_utr_reference IS NOT NULL AND pw.total_outstanding < 0.00 THEN 0.00
                         ELSE COALESCE(pw.total_outstanding, 0.00)
                     END
                
                ELSE 0.00 
            END AS previous_outstanding,

            -- Pending security deposit roll-forward:
            CASE 
                WHEN p_week_id = v_cutoff_week_id THEN COALESCE(op.security_deposit_pending, 0.00)
                WHEN op.security_deposit_pending IS NOT NULL THEN op.security_deposit_pending
                WHEN v_enable_roll_forward = TRUE AND v_prev_week_id = v_cutoff_week_id THEN 
                     COALESCE(pw.pending_deposit, prev_op.security_deposit_pending, 0.00)
                WHEN v_enable_roll_forward = TRUE AND v_prev_week_id > v_cutoff_week_id THEN 
                     COALESCE(pw.pending_deposit, 0.00)
                ELSE 0.00
            END AS pending_deposit,
            
            -- Resolved metadata from opening balances and prior week (for inactive partners)
            COALESCE(op.remarks::json->>'city', prev_op.remarks::json->>'city', pw.city) AS op_city,
            COALESCE(op.remarks::json->>'name', prev_op.remarks::json->>'name', pw.partner_name) AS op_name,
            COALESCE(op.remarks::json->>'type', prev_op.remarks::json->>'type', pw.partner_type) AS op_type,
            pw.payout_account_number AS prev_payout_account_number,
            pw.payout_ifsc AS prev_payout_ifsc,
            COALESCE(
                (op.remarks::json->>'deposit_paid')::numeric,
                (prev_op.remarks::json->>'deposit_paid')::numeric,
                pw.security_deposit_paid
            ) AS deposit_paid_baseline
        FROM (
            SELECT partner_id FROM veh_summary
            UNION
            SELECT partner_id FROM prior_adj
            UNION
            SELECT partner_id FROM current_ledger_adj
            UNION
            SELECT partner_id FROM public.hisaab_partner_opening_balances WHERE effective_week_id = p_week_id
            UNION
            SELECT partner_id FROM public.hisaab_partner_opening_balances WHERE effective_week_id = v_cutoff_week_id AND v_enable_roll_forward = TRUE AND v_prev_week_id = v_cutoff_week_id
            UNION
            SELECT partner_id FROM public.hisaab_partner_weekly 
            WHERE v_enable_roll_forward = TRUE 
              AND week_id = v_prev_week_id 
              AND (total_outstanding <> 0.00 OR pending_deposit > 0.00)
            UNION
            SELECT partner_id FROM public.hisaab_partner_weekly
            WHERE week_id = p_week_id
              AND amount_paid_during_week > 0
        ) ap
        LEFT JOIN public.hisaab_partner_opening_balances op 
            ON op.partner_id = ap.partner_id AND op.effective_week_id = p_week_id
        LEFT JOIN public.hisaab_partner_opening_balances prev_op 
            ON prev_op.partner_id = ap.partner_id AND prev_op.effective_week_id = v_cutoff_week_id
        LEFT JOIN public.hisaab_partner_weekly pw 
            ON pw.week_id = v_prev_week_id AND pw.partner_id = ap.partner_id
        WHERE (p_partner IS NULL OR ap.partner_id = p_partner)
    ),

    -- 5. Preserve payments already made during this week
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
           OR pending_deposit > 0.00
           OR partner_id IN (SELECT partner_id FROM veh_summary)
           OR partner_id IN (SELECT partner_id FROM prior_adj)
           OR partner_id IN (SELECT partner_id FROM current_ledger_adj)
           OR partner_id IN (SELECT partner_id FROM existing_payments WHERE amount_paid_during_week > 0)
           OR (p_week_id = v_cutoff_week_id AND partner_id IN (SELECT partner_id FROM public.hisaab_partner_opening_balances WHERE effective_week_id = v_cutoff_week_id))
           OR (v_prev_week_id = v_cutoff_week_id AND partner_id IN (SELECT partner_id FROM public.hisaab_partner_opening_balances WHERE effective_week_id = v_cutoff_week_id))
    )

    -- 6. UPSERT into hisaab_partner_weekly
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
        total_dead_mile_penalty,
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
        COALESCE(v.partner_name, dr.driver_name, pd.op_name, ap.partner_id) AS partner_name,
        COALESCE(v.partner_type, pd.op_type, CASE WHEN ap.partner_id ILIKE '%IP%' OR ap.partner_id ILIKE '%OP%' THEN 'Operator' ELSE 'Individual' END) AS partner_type,
        COALESCE(v.city, dr.city, pd.op_city, CASE WHEN ap.partner_id ILIKE '%BLR%' THEN 'BLR' WHEN ap.partner_id ILIKE '%MUM%' THEN 'MUM' ELSE 'HYD' END) AS city,
        COALESCE(v.allotted_cars_count, 0) AS allotted_cars_count,
        COALESCE(v.total_onroad_days, 0) AS total_onroad_days,
        COALESCE(v.total_trips, 0) AS total_trips,
        COALESCE(v.total_net_rent_billed, 0.00) AS total_net_rent_billed,
        COALESCE(v.total_platform_earnings, 0.00) AS total_platform_earnings,
        COALESCE(v.total_cash_collected, 0.00) AS total_cash_collected,
        COALESCE(v.total_platform_incentives, 0.00) AS total_platform_incentives,
        ROUND(COALESCE(v.total_adjustments, 0.00) + COALESCE(cla.current_ledger_adjustments, 0.00), 2) AS total_adjustments,
        COALESCE(v.total_challans, 0.00) AS total_challans,
        COALESCE(v.total_accidents, 0.00) AS total_accidents,
        COALESCE(v.total_tds, 0.00) AS total_tds,
        COALESCE(v.total_dead_mile_penalty, 0.00) AS total_dead_mile_penalty,
        ROUND(COALESCE(v.current_week_os, 0.00) + COALESCE(cla.current_ledger_adjustments, 0.00), 2) AS current_week_os,
        COALESCE(pd.previous_outstanding, 0.00) AS previous_outstanding,
        COALESCE(ep.amount_paid_during_week, 0.00) AS amount_paid_during_week,
        COALESCE(pa.prior_period_adjustments, 0.00) AS prior_period_adjustments,
        COALESCE(
            dr.security_deposit,
            CASE WHEN pd.deposit_paid_baseline IS NOT NULL THEN (pd.deposit_paid_baseline + COALESCE(pd.pending_deposit, 0.00)) ELSE NULL END,
            CASE WHEN ap.partner_id ILIKE '%IP%' OR ap.partner_id ILIKE '%OP%' THEN 25000.00 ELSE 5000.00 END
        ) AS security_deposit_target,
        COALESCE(
            pd.deposit_paid_baseline,
            GREATEST(0.00, COALESCE(dr.security_deposit, CASE WHEN ap.partner_id ILIKE '%IP%' OR ap.partner_id ILIKE '%OP%' THEN 25000.00 ELSE 5000.00 END) - COALESCE(pd.pending_deposit, 0.00))
        ) AS security_deposit_paid,
        0.00 AS deposit_deduction_current_week,
        COALESCE(pd.pending_deposit, 0.00) AS pending_deposit,
        
        -- Net Signed Balance: preserves advance payments (<0) and debts (>0)
        ROUND(
            (COALESCE(pd.previous_outstanding, 0.00) 
             + (COALESCE(v.current_week_os, 0.00) + COALESCE(cla.current_ledger_adjustments, 0.00)) 
             + COALESCE(pa.prior_period_adjustments, 0.00)) 
            - COALESCE(ep.amount_paid_during_week, 0.00), 
            2
        ) AS total_outstanding,
        
        -- Net Bank Payout (when earnings or advance payments exceed rent + debt)
        CASE 
            WHEN ROUND(((COALESCE(pd.previous_outstanding, 0.00) + (COALESCE(v.current_week_os, 0.00) + COALESCE(cla.current_ledger_adjustments, 0.00)) + COALESCE(pa.prior_period_adjustments, 0.00)) - COALESCE(ep.amount_paid_during_week, 0.00)), 2) < 0.00
            THEN ABS(ROUND(((COALESCE(pd.previous_outstanding, 0.00) + (COALESCE(v.current_week_os, 0.00) + COALESCE(cla.current_ledger_adjustments, 0.00)) + COALESCE(pa.prior_period_adjustments, 0.00)) - COALESCE(ep.amount_paid_during_week, 0.00)), 2))
            ELSE 0.00
        END AS net_bank_payout,
        
        -- Net Amount to Collect (when driver owes company)
        GREATEST(0.00, 
            ROUND(((COALESCE(pd.previous_outstanding, 0.00) + (COALESCE(v.current_week_os, 0.00) + COALESCE(cla.current_ledger_adjustments, 0.00)) + COALESCE(pa.prior_period_adjustments, 0.00)) - COALESCE(ep.amount_paid_during_week, 0.00)), 2)
        ) AS net_amount_to_collect,
        
        CASE 
            WHEN ROUND(((COALESCE(pd.previous_outstanding, 0.00) + (COALESCE(v.current_week_os, 0.00) + COALESCE(cla.current_ledger_adjustments, 0.00)) + COALESCE(pa.prior_period_adjustments, 0.00)) - COALESCE(ep.amount_paid_during_week, 0.00)), 2) <= 0.00
            THEN 'SETTLED'
            WHEN COALESCE(ep.amount_paid_during_week, 0.00) > 0.00
                 AND ROUND(((COALESCE(pd.previous_outstanding, 0.00) + (COALESCE(v.current_week_os, 0.00) + COALESCE(cla.current_ledger_adjustments, 0.00)) + COALESCE(pa.prior_period_adjustments, 0.00)) - COALESCE(ep.amount_paid_during_week, 0.00)), 2) > 0.00
            THEN 'PARTIAL'
            ELSE 'OPEN'
        END AS settlement_status,
        COALESCE(dr.account_number, pd.prev_payout_account_number) AS payout_account_number,
        COALESCE(dr.ifsc_code, pd.prev_payout_ifsc) AS payout_ifsc,
        CURRENT_TIMESTAMP AS updated_at
    FROM all_partners ap
    LEFT JOIN veh_summary v ON ap.partner_id = v.partner_id
    LEFT JOIN prior_adj pa ON ap.partner_id = pa.partner_id
    LEFT JOIN current_ledger_adj cla ON ap.partner_id = cla.partner_id
    LEFT JOIN prev_dues pd ON ap.partner_id = pd.partner_id
    LEFT JOIN existing_payments ep ON ap.partner_id = ep.partner_id
    LEFT JOIN LATERAL (
        SELECT account_number, ifsc_code, driver_name, security_deposit, city FROM public.core_partner_onboarding 
        WHERE partner_id = ap.partner_id LIMIT 1
    ) dr ON TRUE
    ON CONFLICT (week_id, partner_id) DO UPDATE SET
        partner_name = COALESCE(EXCLUDED.partner_name, public.hisaab_partner_weekly.partner_name),
        partner_type = COALESCE(EXCLUDED.partner_type, public.hisaab_partner_weekly.partner_type),
        city = COALESCE(EXCLUDED.city, public.hisaab_partner_weekly.city),
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
        previous_outstanding = EXCLUDED.previous_outstanding,
        prior_period_adjustments = EXCLUDED.prior_period_adjustments,
        amount_paid_during_week = COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00),
        security_deposit_target = EXCLUDED.security_deposit_target,
        security_deposit_paid = EXCLUDED.security_deposit_paid,
        pending_deposit = EXCLUDED.pending_deposit,
        
        -- Recalculate signed balance
        total_outstanding = ROUND((EXCLUDED.previous_outstanding + EXCLUDED.current_week_os + EXCLUDED.prior_period_adjustments) - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00), 2),
        net_bank_payout = CASE 
            WHEN (EXCLUDED.previous_outstanding + EXCLUDED.current_week_os + EXCLUDED.prior_period_adjustments - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00)) < 0.00
            THEN ABS(ROUND(EXCLUDED.previous_outstanding + EXCLUDED.current_week_os + EXCLUDED.prior_period_adjustments - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00), 2))
            ELSE 0.00 
        END,
        net_amount_to_collect = GREATEST(0.00, ROUND((EXCLUDED.previous_outstanding + EXCLUDED.current_week_os + EXCLUDED.prior_period_adjustments) - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00), 2)),
        settlement_status = CASE 
            WHEN ROUND((EXCLUDED.previous_outstanding + EXCLUDED.current_week_os + EXCLUDED.prior_period_adjustments) - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00), 2) <= 0.00
            THEN 'SETTLED'
            WHEN COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00) > 0.00
                 AND ROUND((EXCLUDED.previous_outstanding + EXCLUDED.current_week_os + EXCLUDED.prior_period_adjustments) - COALESCE(public.hisaab_partner_weekly.amount_paid_during_week, 0.00), 2) > 0.00
            THEN 'PARTIAL'
            ELSE 'OPEN'
        END,
        payout_account_number = COALESCE(EXCLUDED.payout_account_number, public.hisaab_partner_weekly.payout_account_number),
        payout_ifsc = COALESCE(EXCLUDED.payout_ifsc, public.hisaab_partner_weekly.payout_ifsc),
        updated_at = CURRENT_TIMESTAMP
    WHERE public.hisaab_partner_weekly.settlement_status <> 'LOCKED';

END;
$procedure$;
