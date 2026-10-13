-- sp_admin_settlement_week_lock_controls.sql
-- ============================================================================
-- ADMINISTRATIVE LOCK & UNLOCK CONTROLS FOR SETTLEMENT WEEKS
-- LetzRyd Hisaab Engine
-- ============================================================================
--
-- PURPOSE:
-- Allows Finance & Engineering administrators to safely and temporarily
-- unlock a settlement week for a specified duration (e.g., 1 hour, 2 hours, 24 hours),
-- perform necessary audit corrections or re-syncs, and either allow it to auto-relock
-- when the window expires or immediately re-lock it on demand.
--
-- AUDIT GUARANTEE:
-- Every unlock and lock action is recorded in:
-- 1. public.hisaab_settlement_weeks.notes
-- 2. public.hisaab_cutoff_audit_log
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. PROCEDURE: sp_admin_unlock_settlement_week
-- ----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE public.sp_admin_unlock_settlement_week(
    IN p_week_id VARCHAR,
    IN p_unlock_duration_hours INT DEFAULT 2,
    IN p_admin_user VARCHAR DEFAULT 'finance_admin',
    IN p_reason TEXT DEFAULT 'Temporary administrative maintenance'
)
LANGUAGE plpgsql
AS $procedure$
DECLARE
    v_exists BOOLEAN;
    v_new_cutoff TIMESTAMPTZ;
BEGIN
    -- Verify target week exists
    SELECT EXISTS (
        SELECT 1 FROM public.hisaab_settlement_weeks WHERE week_id = p_week_id
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'Settlement week % does not exist in public.hisaab_settlement_weeks.', p_week_id;
    END IF;

    IF p_unlock_duration_hours <= 0 THEN
        RAISE EXCEPTION 'Unlock duration must be greater than 0 hours. Provided: %', p_unlock_duration_hours;
    END IF;

    -- Compute new expiration timestamp
    v_new_cutoff := CURRENT_TIMESTAMP + (p_unlock_duration_hours || ' hours')::INTERVAL;

    -- 1. Unlock settlement week and extend cutoff threshold
    UPDATE public.hisaab_settlement_weeks
    SET is_locked = FALSE,
        locked_at = NULL,
        locked_by = NULL,
        lock_cutoff_at = v_new_cutoff,
        notes = COALESCE(notes || '; ', '') || '[UNLOCKED for ' || p_unlock_duration_hours || 'h by ' || p_admin_user || ' until ' || to_char(v_new_cutoff AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:MI:SS IST') || ' - Reason: ' || p_reason || ']',
        updated_at = CURRENT_TIMESTAMP
    WHERE week_id = p_week_id;

    -- 2. Transition associated partner weekly records back to OPEN for updates
    UPDATE public.hisaab_partner_weekly
    SET settlement_status = 'OPEN',
        updated_at = CURRENT_TIMESTAMP
    WHERE week_id = p_week_id AND settlement_status = 'LOCKED';

    -- 3. Log event in audit log
    INSERT INTO public.hisaab_cutoff_audit_log (
        partner_id,
        week_id,
        action_type,
        previous_os,
        new_weekly_due,
        amount_paid,
        resulting_os,
        created_at
    ) VALUES (
        'ADMIN:' || p_admin_user,
        p_week_id,
        'WEEK_TEMPORARILY_UNLOCKED',
        0.00,
        p_unlock_duration_hours,
        0.00,
        0.00,
        CURRENT_TIMESTAMP
    );

    RAISE NOTICE 'Settlement week % successfully UNLOCKED for % hours until % (IST). Reason: %',
        p_week_id, p_unlock_duration_hours, to_char(v_new_cutoff AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:MI:SS IST'), p_reason;

END;
$procedure$;


-- ----------------------------------------------------------------------------
-- 2. PROCEDURE: sp_admin_lock_settlement_week
-- ----------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE public.sp_admin_lock_settlement_week(
    IN p_week_id VARCHAR,
    IN p_admin_user VARCHAR DEFAULT 'finance_admin',
    IN p_reason TEXT DEFAULT 'Administrative freeze'
)
LANGUAGE plpgsql
AS $procedure$
DECLARE
    v_exists BOOLEAN;
BEGIN
    -- Verify target week exists
    SELECT EXISTS (
        SELECT 1 FROM public.hisaab_settlement_weeks WHERE week_id = p_week_id
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'Settlement week % does not exist in public.hisaab_settlement_weeks.', p_week_id;
    END IF;

    -- 1. Freeze settlement week immediately
    UPDATE public.hisaab_settlement_weeks
    SET is_locked = TRUE,
        locked_at = CURRENT_TIMESTAMP,
        locked_by = p_admin_user,
        lock_cutoff_at = CURRENT_TIMESTAMP,
        notes = COALESCE(notes || '; ', '') || '[MANUALLY LOCKED by ' || p_admin_user || ' - Reason: ' || p_reason || ']',
        updated_at = CURRENT_TIMESTAMP
    WHERE week_id = p_week_id;

    -- 2. Lock associated partner weekly records
    UPDATE public.hisaab_partner_weekly
    SET settlement_status = 'LOCKED',
        updated_at = CURRENT_TIMESTAMP
    WHERE week_id = p_week_id AND settlement_status <> 'LOCKED';

    -- 3. Log event in audit log
    INSERT INTO public.hisaab_cutoff_audit_log (
        partner_id,
        week_id,
        action_type,
        previous_os,
        new_weekly_due,
        amount_paid,
        resulting_os,
        created_at
    ) VALUES (
        'ADMIN:' || p_admin_user,
        p_week_id,
        'WEEK_MANUALLY_LOCKED',
        0.00,
        0.00,
        0.00,
        0.00,
        CURRENT_TIMESTAMP
    );

    RAISE NOTICE 'Settlement week % successfully LOCKED by %. Reason: %',
        p_week_id, p_admin_user, p_reason;

END;
$procedure$;
