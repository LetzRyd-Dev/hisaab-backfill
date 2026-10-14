-- rollback_cutoff_baseline.sql
-- ============================================================================
-- EMERGENCY ROLLBACK SCRIPT FOR CUTOFF BASELINE (OCT 12, 2026)
-- LetzRyd Hisaab Engine
-- ============================================================================
-- 
-- PURPOSE:
-- Safely and completely reverses the cutoff baseline ingestion and reset
-- the database state back to pre-migration baseline in case of any discrepancy.
--
-- GUARANTEE:
-- Core production tables (app_hisaabs, app_drivers, app_driver_allocations, 
-- vehicles, app_payments) are 100% untouched and unaffected.
-- ============================================================================

BEGIN;

-- 1. Remove the ingested opening balances for week CY26WK41
DELETE FROM public.hisaab_partner_opening_balances 
WHERE effective_week_id = 'CY26WK41';

-- 2. Unlock settlement week first so trigger permits cleanup
UPDATE public.hisaab_settlement_weeks
SET is_locked = FALSE,
    locked_at = NULL,
    locked_by = NULL
WHERE week_id = 'CY26WK41';

-- 3. Clean up partner weekly ledger rows generated for CY26WK41
DELETE FROM public.hisaab_partner_weekly 
WHERE week_id = 'CY26WK41';

-- 4. Revert system config to standby
UPDATE public.hisaab_system_config
SET config_value = 'false'
WHERE config_key = 'enable_roll_forward';

-- 5. Record rollback in audit log
INSERT INTO public.hisaab_cutoff_audit_log (
    partner_id,
    week_id,
    action_type,
    previous_os,
    new_weekly_due,
    amount_paid,
    resulting_os
) VALUES (
    'SYSTEM',
    'CY26WK41',
    'ROLLBACK_EXECUTED',
    0.00,
    0.00,
    0.00,
    0.00
);

COMMIT;

-- Verification check:
SELECT count(*) AS opening_balances_remaining 
FROM public.hisaab_partner_opening_balances 
WHERE effective_week_id = 'CY26WK41';
