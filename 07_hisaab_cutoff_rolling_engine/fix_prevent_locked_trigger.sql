-- ============================================================================
-- Fix for fn_prevent_locked_partner_weekly_update, 
-- fn_prevent_locked_vehicle_weekly_update, and fn_trg_auto_route_prior_period_adjustment
-- ============================================================================

-- 1. Trigger function for hisaab_partner_weekly
CREATE OR REPLACE FUNCTION public.fn_prevent_locked_partner_weekly_update()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_is_locked BOOLEAN;
    v_target_week VARCHAR;
BEGIN
    v_target_week := CASE WHEN TG_OP = 'INSERT' THEN NEW.week_id ELSE OLD.week_id END;

    -- Check master week lock status
    SELECT COALESCE(is_locked, FALSE) INTO v_is_locked
    FROM public.hisaab_settlement_weeks
    WHERE week_id = v_target_week;

    -- Case 1: INSERT operation (Block inserts into locked weeks)
    IF TG_OP = 'INSERT' THEN
        IF v_is_locked = TRUE THEN
            RAISE EXCEPTION 'Settlement week % is locked. New records cannot be inserted.', NEW.week_id;
        END IF;
        RETURN NEW;
    END IF;

    -- Case 2: DELETE operation
    IF TG_OP = 'DELETE' THEN
        IF v_is_locked = TRUE OR OLD.settlement_status = 'LOCKED' THEN
            RAISE EXCEPTION 'Settlement week % is locked. Records cannot be deleted.', OLD.week_id;
        END IF;
        RETURN OLD;
    END IF;

    -- Case 3: UPDATE operation
    IF TG_OP = 'UPDATE' THEN
        -- Allow administrative lock transition: when master is being locked, allow setting settlement_status = 'LOCKED'
        -- but forbid modifying financial columns during the status transition
        IF NEW.settlement_status = 'LOCKED' AND OLD.settlement_status <> 'LOCKED' THEN
            IF ROW(NEW.total_net_rent_billed, NEW.total_platform_earnings, NEW.total_cash_collected,
                   NEW.total_adjustments, NEW.current_week_os, NEW.previous_outstanding,
                   NEW.amount_paid_during_week, NEW.total_outstanding, NEW.net_bank_payout,
                   NEW.net_amount_to_collect)
               IS DISTINCT FROM
               ROW(OLD.total_net_rent_billed, OLD.total_platform_earnings, OLD.total_cash_collected,
                   OLD.total_adjustments, OLD.current_week_os, OLD.previous_outstanding,
                   OLD.amount_paid_during_week, OLD.total_outstanding, OLD.net_bank_payout,
                   OLD.net_amount_to_collect) THEN
                RAISE EXCEPTION 'Cannot modify financial data while transitioning settlement_status to LOCKED in week %.', OLD.week_id;
            END IF;
            RETURN NEW;
        END IF;

        -- Allow administrative unlock transition: when master is unlocked, allow transitioning settlement_status to 'OPEN'
        IF v_is_locked = FALSE AND OLD.settlement_status = 'LOCKED' AND NEW.settlement_status = 'OPEN' THEN
            RETURN NEW;
        END IF;

        -- Block any update if either the master week is locked or the record itself is locked
        IF v_is_locked = TRUE OR OLD.settlement_status = 'LOCKED' THEN
            RAISE EXCEPTION 'Settlement week % is locked. Data in this week is immutable.', OLD.week_id;
        END IF;

        RETURN NEW;
    END IF;

    RETURN NEW;
END;
$function$;

-- Attach trigger to hisaab_partner_weekly
DROP TRIGGER IF EXISTS trg_prevent_locked_partner_weekly ON public.hisaab_partner_weekly;
CREATE TRIGGER trg_prevent_locked_partner_weekly
BEFORE INSERT OR UPDATE OR DELETE ON public.hisaab_partner_weekly
FOR EACH ROW EXECUTE FUNCTION public.fn_prevent_locked_partner_weekly_update();


-- 2. Trigger function for hisaab_vehicle_weekly (Tier 2 vehicle records lock protection)
CREATE OR REPLACE FUNCTION public.fn_prevent_locked_vehicle_weekly_update()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_is_locked BOOLEAN;
    v_target_week VARCHAR;
BEGIN
    v_target_week := CASE WHEN TG_OP = 'INSERT' THEN NEW.week_id ELSE OLD.week_id END;

    SELECT COALESCE(is_locked, FALSE) INTO v_is_locked
    FROM public.hisaab_settlement_weeks
    WHERE week_id = v_target_week;

    -- Case 1: INSERT operation
    IF TG_OP = 'INSERT' THEN
        IF v_is_locked = TRUE THEN
            RAISE EXCEPTION 'Settlement week % is locked. Vehicle records cannot be inserted.', NEW.week_id;
        END IF;
        RETURN NEW;
    END IF;

    -- Case 2: DELETE operation
    IF TG_OP = 'DELETE' THEN
        IF v_is_locked = TRUE OR OLD.settlement_status = 'LOCKED' THEN
            RAISE EXCEPTION 'Settlement week % is locked. Vehicle weekly records cannot be deleted.', OLD.week_id;
        END IF;
        RETURN OLD;
    END IF;

    -- Case 3: UPDATE operation
    IF TG_OP = 'UPDATE' THEN
        -- Allow setting settlement_status = 'LOCKED' during week lock without mutating financials
        IF NEW.settlement_status = 'LOCKED' AND OLD.settlement_status <> 'LOCKED' THEN
            RETURN NEW;
        END IF;

        -- Allow setting settlement_status to 'OPEN' during week unlock
        IF v_is_locked = FALSE AND OLD.settlement_status = 'LOCKED' AND NEW.settlement_status = 'OPEN' THEN
            RETURN NEW;
        END IF;

        -- Block modifications if week is locked or row is locked
        IF v_is_locked = TRUE OR OLD.settlement_status = 'LOCKED' THEN
            RAISE EXCEPTION 'Settlement week % is locked. Vehicle weekly data is immutable.', OLD.week_id;
        END IF;

        RETURN NEW;
    END IF;

    RETURN NEW;
END;
$function$;

-- Attach trigger to hisaab_vehicle_weekly
DROP TRIGGER IF EXISTS trg_prevent_locked_vehicle_weekly ON public.hisaab_vehicle_weekly;
CREATE TRIGGER trg_prevent_locked_vehicle_weekly
BEFORE INSERT OR UPDATE OR DELETE ON public.hisaab_vehicle_weekly
FOR EACH ROW EXECUTE FUNCTION public.fn_prevent_locked_vehicle_weekly_update();


-- 3. Statement-level triggers to prevent TRUNCATE on settlement tables
CREATE OR REPLACE FUNCTION public.fn_prevent_truncate_locked_hisaab()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'TRUNCATE operation is strictly forbidden on %. Use controlled administrative procedures.', TG_TABLE_NAME;
END;
$function$;

DROP TRIGGER IF EXISTS trg_prevent_truncate_partner_weekly ON public.hisaab_partner_weekly;
CREATE TRIGGER trg_prevent_truncate_partner_weekly
BEFORE TRUNCATE ON public.hisaab_partner_weekly
FOR EACH STATEMENT EXECUTE FUNCTION public.fn_prevent_truncate_locked_hisaab();

DROP TRIGGER IF EXISTS trg_prevent_truncate_vehicle_weekly ON public.hisaab_vehicle_weekly;
CREATE TRIGGER trg_prevent_truncate_vehicle_weekly
BEFORE TRUNCATE ON public.hisaab_vehicle_weekly
FOR EACH STATEMENT EXECUTE FUNCTION public.fn_prevent_truncate_locked_hisaab();


-- 4. Trigger function for hisaab_adjustments_ledger
CREATE OR REPLACE FUNCTION public.fn_trg_auto_route_prior_period_adjustment()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_is_locked BOOLEAN;
    v_next_week VARCHAR;
    v_next_start DATE;
BEGIN
    -- Validate settlement_week_id
    IF NEW.settlement_week_id IS NULL OR TRIM(NEW.settlement_week_id) = '' THEN
        RAISE EXCEPTION 'settlement_week_id is required and cannot be empty.';
    END IF;

    -- Only auto-route on INSERT, OR on UPDATE if settlement_week_id was specifically changed
    IF TG_OP = 'UPDATE' AND NEW.settlement_week_id = OLD.settlement_week_id THEN
        RETURN NEW;
    END IF;

    SELECT COALESCE(is_locked, FALSE) INTO v_is_locked
    FROM public.hisaab_settlement_weeks
    WHERE week_id = NEW.settlement_week_id;

    -- If target settlement week is locked, re-route to next active open week
    IF v_is_locked = TRUE THEN
        SELECT week_id INTO v_next_week
        FROM public.hisaab_settlement_weeks
        WHERE is_locked = FALSE
          AND week_start > (SELECT week_start FROM public.hisaab_settlement_weeks WHERE week_id = NEW.settlement_week_id)
        ORDER BY week_start ASC
        LIMIT 1;

        -- If no open future week exists, calculate and auto-provision it
        IF v_next_week IS NULL THEN
            SELECT (week_end + INTERVAL '1 day')::date INTO v_next_start
            FROM public.hisaab_settlement_weeks
            WHERE week_id = NEW.settlement_week_id;

            IF v_next_start IS NOT NULL THEN
                v_next_week := 'CY' || to_char(v_next_start, 'IY') || 'WK' || to_char(v_next_start, 'IW');
                PERFORM public.fn_ensure_hisaab_week(v_next_week, v_next_start);
            END IF;
        END IF;

        IF v_next_week IS NOT NULL THEN
            NEW.remarks := COALESCE(NEW.remarks, '') || ' [Auto-routed from locked week ' || NEW.settlement_week_id || ' to ' || v_next_week || ']';
            NEW.settlement_week_id := v_next_week;
            NEW.is_prior_period := TRUE;
        ELSE
            RAISE NOTICE 'Target week % is locked; adjustment marked as prior-period.', NEW.settlement_week_id;
            NEW.is_prior_period := TRUE;
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;
