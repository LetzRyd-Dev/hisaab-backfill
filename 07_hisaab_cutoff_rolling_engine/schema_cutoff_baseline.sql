-- schema_cutoff_baseline.sql
-- ============================================================================
-- Complete schema for Cutoff Baseline and Cumulative Rolling Ledger
-- LetzRyd Hisaab Engine - Monday 12:00 PM (Noon) IST Cutoff & Rolling Engine
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Table: System Configuration
-- Controls whether roll-forward is active, cutoff week, and lock cutoff settings.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.hisaab_system_config (
    config_key VARCHAR(100) PRIMARY KEY,
    config_value VARCHAR(255) NOT NULL,
    description TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed configuration keys (idempotent with ON CONFLICT DO UPDATE)
INSERT INTO public.hisaab_system_config (config_key, config_value, description)
VALUES 
    ('enable_roll_forward', 'true', 'Flag to enable cumulative week-over-week outstanding roll-forward'),
    ('cutoff_week_id', 'CY26WK41', 'Starting week_id from which cutoff balance is effective'),
    ('cutoff_date', '2026-10-12', 'The exact date when the final balance snapshot was taken'),
    ('lock_cutoff_time', '12:00:00+05:30', 'Monday 12:00 PM (Noon) IST weekly settlement lock threshold')
ON CONFLICT (config_key) DO UPDATE SET 
    config_value = EXCLUDED.config_value,
    description = EXCLUDED.description,
    updated_at = CURRENT_TIMESTAMP;


-- ----------------------------------------------------------------------------
-- 2. Table: Cutoff Baseline Opening Balances
-- Holds the master snapshot of verified balances as of the Monday 12 Noon cutoff.
-- Required columns:
--   partner_id (VARCHAR), effective_week_id (VARCHAR), 
--   opening_balance_due (NUMERIC(12,2)), 
--   cutoff_timestamp (TIMESTAMPTZ DEFAULT '2026-10-12 12:00:00+05:30'), 
--   remarks (TEXT), created_at (TIMESTAMPTZ).
-- UNIQUE constraint on (partner_id, effective_week_id) ensures idempotent ingestion.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.hisaab_partner_opening_balances (
    id SERIAL PRIMARY KEY,
    partner_id VARCHAR(100) NOT NULL,
    driver_code VARCHAR(100) NULL,
    phone VARCHAR(20) NULL,
    effective_week_id VARCHAR(50) NOT NULL,
    cutoff_date DATE NULL DEFAULT '2026-10-12',
    cutoff_timestamp TIMESTAMPTZ NOT NULL DEFAULT '2026-10-12 12:00:00+05:30',
    opening_balance_due NUMERIC(12,2) NOT NULL DEFAULT 0.00, -- Amount owed as of cutoff snapshot
    security_deposit_pending NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    verified_by VARCHAR(100) DEFAULT 'Admin Cutoff Upload',
    remarks TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_partner_effective_week UNIQUE (partner_id, effective_week_id)
);

-- Ensure columns exist if table was previously created with older schema
ALTER TABLE public.hisaab_partner_opening_balances 
    ADD COLUMN IF NOT EXISTS cutoff_timestamp TIMESTAMPTZ DEFAULT '2026-10-12 12:00:00+05:30';

ALTER TABLE public.hisaab_partner_opening_balances 
    ADD COLUMN IF NOT EXISTS remarks TEXT;

ALTER TABLE public.hisaab_partner_opening_balances 
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE public.hisaab_partner_opening_balances 
    ADD COLUMN IF NOT EXISTS cutoff_date DATE DEFAULT '2026-10-12';

CREATE INDEX IF NOT EXISTS idx_cutoff_partner_week 
ON public.hisaab_partner_opening_balances(partner_id, effective_week_id);


-- ----------------------------------------------------------------------------
-- 3. Table: Master Settlement Weeks
-- Calendar boundary table enforcing Monday 12:00 PM (Noon) IST lock_cutoff_at.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.hisaab_settlement_weeks (
    week_id VARCHAR(20) PRIMARY KEY,                    -- e.g. 'CY26WK41'
    settlement_year INT NOT NULL,                       -- e.g. 2026
    settlement_week INT NOT NULL,                       -- e.g. 41
    week_start DATE NOT NULL,                           -- Monday date
    week_end DATE NOT NULL,                             -- Sunday date
    lock_cutoff_at TIMESTAMPTZ NOT NULL DEFAULT '2026-10-12 12:00:00+05:30', -- Monday 12:00 PM (Noon) IST
    is_locked BOOLEAN NOT NULL DEFAULT FALSE,           -- Locked status switch
    locked_at TIMESTAMPTZ,                              -- Exact freeze timestamp
    locked_by VARCHAR(64),                              -- Admin or system user
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_hisaab_settlement_weeks_year_week UNIQUE (settlement_year, settlement_week)
);

ALTER TABLE public.hisaab_settlement_weeks 
    ADD COLUMN IF NOT EXISTS lock_cutoff_at TIMESTAMPTZ DEFAULT '2026-10-12 12:00:00+05:30';

CREATE INDEX IF NOT EXISTS idx_hisaab_weeks_dates ON public.hisaab_settlement_weeks (week_start, week_end);
CREATE INDEX IF NOT EXISTS idx_hisaab_weeks_locked ON public.hisaab_settlement_weeks (is_locked);


-- ----------------------------------------------------------------------------
-- 4. Table: Partner Weekly Settlement Ledger
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.hisaab_partner_weekly (
    id BIGSERIAL PRIMARY KEY,
    settlement_year INT NOT NULL,
    settlement_week INT NOT NULL,
    week_id VARCHAR(20) NOT NULL REFERENCES public.hisaab_settlement_weeks(week_id),
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    partner_id VARCHAR(50) NOT NULL,
    partner_name VARCHAR(150),
    partner_type VARCHAR(20) DEFAULT 'Individual',
    city VARCHAR(50),
    
    allotted_cars_count INT DEFAULT 0,
    total_onroad_days INT DEFAULT 0,
    total_trips INT DEFAULT 0,
    total_net_rent_billed NUMERIC(12, 2) DEFAULT 0.00,
    total_platform_earnings NUMERIC(12, 2) DEFAULT 0.00,
    total_cash_collected NUMERIC(12, 2) DEFAULT 0.00,
    total_platform_incentives NUMERIC(12, 2) DEFAULT 0.00,
    total_adjustments NUMERIC(12, 2) DEFAULT 0.00,
    total_challans NUMERIC(12, 2) DEFAULT 0.00,
    total_accidents NUMERIC(12, 2) DEFAULT 0.00,
    total_tds NUMERIC(12, 2) DEFAULT 0.00,
    total_dead_mile_penalty NUMERIC(12, 2) DEFAULT 0.00,
    
    current_week_os NUMERIC(12, 2) DEFAULT 0.00,
    previous_outstanding NUMERIC(12, 2) DEFAULT 0.00,
    amount_paid_during_week NUMERIC(12, 2) DEFAULT 0.00,
    prior_period_adjustments NUMERIC(12, 2) DEFAULT 0.00,
    
    security_deposit_target NUMERIC(12, 2) DEFAULT 5000.00,
    security_deposit_paid NUMERIC(12, 2) DEFAULT 5000.00,
    deposit_deduction_current_week NUMERIC(12, 2) DEFAULT 0.00,
    pending_deposit NUMERIC(12, 2) DEFAULT 0.00,
    
    total_outstanding NUMERIC(12, 2) DEFAULT 0.00,
    net_bank_payout NUMERIC(12, 2) DEFAULT 0.00,
    net_amount_to_collect NUMERIC(12, 2) DEFAULT 0.00,
    
    settlement_status VARCHAR(20) DEFAULT 'DRAFT', -- 'DRAFT', 'OPEN', 'LOCKED', 'SETTLED'
    frozen_at TIMESTAMPTZ,
    bank_utr_reference VARCHAR(100),
    payout_account_number VARCHAR(50),
    payout_ifsc VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT uq_hisaab_partner_week UNIQUE (week_id, partner_id)
);

CREATE INDEX IF NOT EXISTS idx_hisaab_partner_week ON public.hisaab_partner_weekly (week_id, partner_id);


-- ----------------------------------------------------------------------------
-- 5. Table: Adjustments Ledger
-- Supports adjustments (challans, accident credits, waivers) and routing.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.hisaab_adjustments_ledger (
    adjustment_id BIGSERIAL PRIMARY KEY,
    partner_id VARCHAR(100) NOT NULL,
    settlement_week_id VARCHAR(50) NOT NULL,
    vehicle_number VARCHAR(50) NULL,
    adjustment_type VARCHAR(50) NOT NULL, -- 'CHALLAN', 'ACCIDENT_CREDIT', 'RENT_WAIVER', 'INCENTIVE_ADJ'
    polarity VARCHAR(10) NOT NULL CHECK (polarity IN ('DEBIT', 'CREDIT')),
    amount NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    is_prior_period BOOLEAN NOT NULL DEFAULT FALSE,
    original_event_date DATE NULL,
    approval_status VARCHAR(50) NOT NULL DEFAULT 'Approved',
    remarks TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_adj_week_partner ON public.hisaab_adjustments_ledger (settlement_week_id, partner_id);


-- ----------------------------------------------------------------------------
-- 6. Helper Function: Ensure Settlement Week Exists
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_ensure_hisaab_week(
    p_week_id VARCHAR, 
    p_ref_date DATE DEFAULT NULL
)
RETURNS VOID AS $$
DECLARE
    v_year INT;
    v_week INT;
    v_start DATE;
    v_end DATE;
    v_cutoff TIMESTAMPTZ;
BEGIN
    IF EXISTS (SELECT 1 FROM public.hisaab_settlement_weeks WHERE week_id = p_week_id) THEN
        RETURN;
    END IF;

    IF p_week_id ~ '^CY[0-9]{2}WK[0-9]{1,2}$' THEN
        v_year := 2000 + SUBSTRING(p_week_id FROM 3 FOR 2)::INT;
        v_week := SUBSTRING(p_week_id FROM 7)::INT;
    ELSIF p_week_id ~ '^[0-9]{4}WK[0-9]{1,2}$' THEN
        v_year := SUBSTRING(p_week_id FROM 1 FOR 4)::INT;
        v_week := SUBSTRING(p_week_id FROM 7)::INT;
    ELSE
        v_year := EXTRACT(ISOYEAR FROM COALESCE(p_ref_date, CURRENT_DATE))::INT;
        v_week := EXTRACT(WEEK FROM COALESCE(p_ref_date, CURRENT_DATE))::INT;
    END IF;

    v_start := (to_date(v_year::TEXT || ' ' || v_week::TEXT || ' 1', 'IYYY IW ID'));
    v_end := v_start + INTERVAL '6 days';
    -- Lock Cutoff is strictly Monday 12:00 PM (Noon) IST:
    v_cutoff := ((v_end + INTERVAL '1 day')::date || ' 12:00:00+05:30')::timestamptz;

    INSERT INTO public.hisaab_settlement_weeks (
        week_id, settlement_year, settlement_week, week_start, week_end, lock_cutoff_at, is_locked
    )
    VALUES (
        p_week_id, v_year, v_week, v_start, v_end, v_cutoff, FALSE
    )
    ON CONFLICT (week_id) DO NOTHING;
END;
$$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 7. Immutability Trigger: Prevent Updates to Locked Weeks
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_prevent_locked_partner_weekly_update()
RETURNS TRIGGER AS $$
DECLARE
    v_is_locked BOOLEAN;
BEGIN
    SELECT is_locked INTO v_is_locked
    FROM public.hisaab_settlement_weeks
    WHERE week_id = OLD.week_id;

    IF v_is_locked = TRUE OR OLD.settlement_status = 'LOCKED' THEN
        RAISE EXCEPTION 'Week % is locked. Data in this week is immutable.', OLD.week_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_locked_partner_weekly ON public.hisaab_partner_weekly;
CREATE TRIGGER trg_prevent_locked_partner_weekly
BEFORE UPDATE OR DELETE ON public.hisaab_partner_weekly
FOR EACH ROW
EXECUTE FUNCTION public.fn_prevent_locked_partner_weekly_update();


-- ----------------------------------------------------------------------------
-- 8. Auto-Routing Trigger: Adjustments for Locked Weeks Flow to Next Week
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_trg_auto_route_prior_period_adjustment()
RETURNS TRIGGER AS $$
DECLARE
    v_is_locked BOOLEAN;
    v_next_week VARCHAR;
BEGIN
    SELECT is_locked INTO v_is_locked
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
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_auto_route_adjustment ON public.hisaab_adjustments_ledger;
CREATE TRIGGER trg_auto_route_adjustment
BEFORE INSERT OR UPDATE ON public.hisaab_adjustments_ledger
FOR EACH ROW
EXECUTE FUNCTION public.fn_trg_auto_route_prior_period_adjustment();


-- ----------------------------------------------------------------------------
-- 9. Table: Audit Log of Balance Adjustments / Payments Applied to Cutoff
-- ----------------------------------------------------------------------------
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
