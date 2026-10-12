-- schema_cutoff_baseline.sql
-- ============================================================================
-- Complete schema for Cutoff Baseline and Cumulative Rolling Ledger
-- ============================================================================

-- 1. Table: System Configuration (Controls whether roll-forward is active and cutoff week)
CREATE TABLE IF NOT EXISTS public.hisaab_system_config (
    config_key VARCHAR(100) PRIMARY KEY,
    config_value VARCHAR(255) NOT NULL,
    description TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed configuration keys (defaulted to disabled until cutoff is declared)
INSERT INTO public.hisaab_system_config (config_key, config_value, description)
VALUES 
    ('enable_roll_forward', 'false', 'Flag to enable cumulative week-over-week outstanding roll-forward'),
    ('cutoff_week_id', 'CY26WK41', 'Starting week_id from which cutoff balance is effective'),
    ('cutoff_date', '2026-10-12', 'The exact date when the final balance snapshot was taken')
ON CONFLICT (config_key) DO UPDATE SET 
    config_value = EXCLUDED.config_value,
    updated_at = CURRENT_TIMESTAMP;


-- 2. Table: Cutoff Baseline Opening Balances
-- Holds the master snapshot of verified balances as of the cutoff date.
CREATE TABLE IF NOT EXISTS public.hisaab_partner_opening_balances (
    id SERIAL PRIMARY KEY,
    partner_id VARCHAR(100) NOT NULL,
    driver_code VARCHAR(100) NULL,
    phone VARCHAR(20) NULL,
    effective_week_id VARCHAR(50) NOT NULL,
    cutoff_date DATE NOT NULL,
    opening_balance_due NUMERIC(12,2) NOT NULL DEFAULT 0.00, -- Amount owed as of cutoff
    security_deposit_pending NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    verified_by VARCHAR(100) DEFAULT 'Admin Cutoff Upload',
    remarks TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_partner_effective_week UNIQUE (partner_id, effective_week_id)
);

CREATE INDEX IF NOT EXISTS idx_cutoff_partner_week 
ON public.hisaab_partner_opening_balances(partner_id, effective_week_id);


-- 3. Table: Audit Log of Balance Adjustments / Payments Applied to Cutoff
CREATE TABLE IF NOT EXISTS public.hisaab_cutoff_audit_log (
    log_id SERIAL PRIMARY KEY,
    partner_id VARCHAR(100) NOT NULL,
    week_id VARCHAR(50) NOT NULL,
    action_type VARCHAR(50) NOT NULL, -- 'CUTOFF_INITIALIZED', 'ROLL_FORWARD_APPLIED', 'PAYMENT_CREDITED'
    previous_os NUMERIC(12,2) NOT NULL,
    new_weekly_due NUMERIC(12,2) NOT NULL,
    amount_paid NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    resulting_os NUMERIC(12,2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
