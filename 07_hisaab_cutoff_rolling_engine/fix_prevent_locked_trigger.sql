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
BEGIN
    -- Check master week lock status
    SELECT COALESCE(is_locked, FALSE) INTO v_is_locked
    FROM public.hisaab_settlement_weeks
    WHERE week_id = OLD.week_id;

    -- Case 1: DELETE operation
    IF TG_OP = 'DELETE' THEN
        IF v_is_locked = TRUE OR OLD.settlement_status = 'LOCKED' THEN
            RAISE EXCEPTION 'Settlement week % is locked. Records cannot be deleted.', OLD.week_id;
        END IF;
        RETURN OLD;
    END IF;

    -- Case 2: UPDATE operation
    IF TG_OP = 'UPDATE' THEN
        -- Allow administrative lock transition: when master is being locked, allow setting settlement_status = 'LOCKED'
        IF NEW.settlement_status = 'LOCKED' AND OLD.settlement_status <> 'LOCKED' THEN
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

-- Attach trigger to hisaab_partner_weekly if not exists
DROP TRIGGER IF EXISTS trg_prevent_locked_partner_weekly ON public.hisaab_partner_weekly;
CREATE TRIGGER trg_prevent_locked_partner_weekly
BEFORE UPDATE OR DELETE ON public.hisaab_partner_weekly
FOR EACH ROW EXECUTE FUNCTION public.fn_prevent_locked_partner_weekly_update();


-- 2. Trigger function for hisaab_vehicle_weekly (Tier 2 vehicle records lock protection)
CREATE OR REPLACE FUNCTION public.fn_prevent_locked_vehicle_weekly_update()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_is_locked BOOLEAN;
BEGIN
    SELECT COALESCE(is_locked, FALSE) INTO v_is_locked
    FROM public.hisaab_settlement_weeks
    WHERE week_id = OLD.week_id;

    -- Case 1: DELETE operation
    IF TG_OP = 'DELETE' THEN
        IF v_is_locked = TRUE OR OLD.settlement_status = 'LOCKED' THEN
            RAISE EXCEPTION 'Settlement week % is locked. Vehicle weekly records cannot be deleted.', OLD.week_id;
        END IF;
        RETURN OLD;
    END IF;

    -- Case 2: UPDATE operation
    IF TG_OP = 'UPDATE' THEN
        -- Allow setting settlement_status = 'LOCKED' during week lock
        IF NEW.settlement_status = 'LOCKED' AND OLD.settlement_status <> 'LOCKED' THEN
            RETURN NEW;
        END IF;

        -- Allow setting settlement_status to 'OPEN' or other during week unlock
        IF v_is_locked = FALSE AND OLD.settlement_status = 'LOCKED' THEN
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
BEFORE UPDATE OR DELETE ON public.hisaab_vehicle_weekly
FOR EACH ROW EXECUTE FUNCTION public.fn_prevent_locked_vehicle_weekly_update();


-- 3. Trigger function for hisaab_adjustments_ledger
-- Ensures that updates to existing adjustments in locked weeks do not accidentally re-route them
CREATE OR REPLACE FUNCTION public.fn_trg_auto_route_prior_period_adjustment()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_is_locked BOOLEAN;
    v_next_week VARCHAR;
BEGIN
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
