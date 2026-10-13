-- sp_route_late_aggregator_sync.sql
-- ============================================================================
-- AUTOMATIC LATE OLA / UBER DATA ROUTER (METHOD 1: OPEN WEEK AUTO-FLOW)
-- LetzRyd Hisaab Engine
-- ============================================================================
--
-- PURPOSE:
-- When Ola, Uber, Rapido, challans, or other operational data arrives after 
-- Monday 12:00 PM Noon when a settlement week is already locked:
-- 1. The locked week remains 100% IMMUTABLE and untouched.
-- 2. The late delta (or adjustment) is routed directly into the NEXT OPEN WEEK
--    as a prior-period adjustment amount.
-- 3. In the new week's hisaab, the amount is added/subtracted directly
--    in `prior_period_adjustments` and reflects in `total_outstanding`.
-- ============================================================================

CREATE OR REPLACE PROCEDURE public.sp_route_late_aggregator_sync(
    IN p_partner_id VARCHAR,
    IN p_original_week_id VARCHAR,
    IN p_aggregator VARCHAR,              -- 'UBER', 'OLA', 'RAPIDO'
    IN p_net_earnings_delta NUMERIC,       -- Additional platform earnings owed to driver (+)
    IN p_cash_collected_delta NUMERIC,     -- Additional cash collected by driver (+)
    IN p_remarks TEXT DEFAULT 'Late aggregator sync reconciliation',
    IN p_vehicle_number TEXT DEFAULT NULL
)
LANGUAGE plpgsql
AS $procedure$
DECLARE
    v_is_locked BOOLEAN;
    v_week_end DATE;
    v_target_week VARCHAR;
    v_net_delta NUMERIC;
    v_polarity VARCHAR(10);
    v_vehicle_number TEXT;
BEGIN
    -- Check if original week exists and get its status and week_end date
    SELECT is_locked, week_end INTO v_is_locked, v_week_end
    FROM public.hisaab_settlement_weeks
    WHERE week_id = p_original_week_id;

    IF v_is_locked IS NULL THEN
        RAISE EXCEPTION 'Settlement week % does not exist in hisaab_settlement_weeks.', p_original_week_id;
    END IF;

    -- Resolve vehicle number if not explicitly passed
    IF p_vehicle_number IS NOT NULL THEN
        v_vehicle_number := p_vehicle_number;
    ELSE
        SELECT vehicle_number INTO v_vehicle_number
        FROM public.hisaab_vehicle_weekly
        WHERE week_id = p_original_week_id AND partner_id = p_partner_id
        LIMIT 1;

        IF v_vehicle_number IS NULL THEN
            SELECT vehicle_number INTO v_vehicle_number
            FROM public.app_driver_allocations
            WHERE driver_id = p_partner_id AND status = 'ACTIVE'
            LIMIT 1;
        END IF;

        IF v_vehicle_number IS NULL THEN
            v_vehicle_number := 'N/A';
        END IF;
    END IF;

    -- If week is locked, route to next active open week
    IF v_is_locked = TRUE THEN
        SELECT week_id INTO v_target_week
        FROM public.hisaab_settlement_weeks
        WHERE is_locked = FALSE
          AND week_start > (SELECT week_start FROM public.hisaab_settlement_weeks WHERE week_id = p_original_week_id)
        ORDER BY week_start ASC
        LIMIT 1;

        IF v_target_week IS NULL THEN
            -- If no future open week exists yet, fallback to current calendar week
            v_target_week := 'CY' || to_char(CURRENT_DATE, 'IY') || 'WK' || to_char(CURRENT_DATE, 'IW');
        END IF;
    ELSE
        -- Original week is still open; can be credited directly
        v_target_week := p_original_week_id;
    END IF;

    -- Net Delta = Earnings Delta - Cash Collected Delta
    -- If Earnings > Cash, company owes driver -> CREDIT
    -- If Cash > Earnings, driver collected extra cash -> DEBIT
    v_net_delta := COALESCE(p_net_earnings_delta, 0.00) - COALESCE(p_cash_collected_delta, 0.00);

    IF v_net_delta = 0.00 THEN
        RAISE NOTICE 'Net aggregator delta is 0.00. No adjustment needed.';
        RETURN;
    END IF;

    IF v_net_delta > 0.00 THEN
        -- Net credit to driver (reduces driver debt / increases payout)
        v_polarity := 'CREDIT';
    ELSE
        -- Net debit to driver (increases driver debt)
        v_polarity := 'DEBIT';
        v_net_delta := ABS(v_net_delta);
    END IF;

    -- Insert into adjustments ledger (triggers will automatically reinforce routing if week is locked)
    INSERT INTO public.hisaab_adjustments_ledger (
        partner_id,
        settlement_week_id,
        vehicle_number,
        incident_date,
        incident_week_id,
        adjustment_category,
        polarity,
        amount,
        is_prior_period,
        approval_status,
        remarks,
        created_at,
        updated_at
    ) VALUES (
        p_partner_id,
        v_target_week,
        v_vehicle_number,
        COALESCE(v_week_end, CURRENT_DATE),
        p_original_week_id,
        UPPER(p_aggregator) || '_LATE_SYNC',
        v_polarity,
        v_net_delta,
        (v_target_week <> p_original_week_id),
        'Approved',
        COALESCE(p_remarks, '') || ' [Late ' || p_aggregator || ' sync for locked week ' || p_original_week_id || ' applied to ' || v_target_week || ']',
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    );

    RAISE NOTICE 'Late % data for partner % (origin week %) successfully routed to % as % of INR %',
        p_aggregator, p_partner_id, p_original_week_id, v_target_week, v_polarity, v_net_delta;

END;
$procedure$;
