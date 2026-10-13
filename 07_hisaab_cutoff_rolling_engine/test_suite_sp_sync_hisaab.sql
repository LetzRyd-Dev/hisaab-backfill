-- ============================================================================
-- test_suite_sp_sync_hisaab.sql
-- ============================================================================
-- Comprehensive Automated Verification Test Suite for:
-- 1. Monday 12:00 PM (Noon) IST Lock Enforcement & Immutability
-- 2. Priority 1: opening_balance_due overrides previous_outstanding for 'CY26WK41'
-- 3. Priority 2: Multi-week Roll-Forward: previous_outstanding = GREATEST(0.00, pw.total_outstanding)
-- 4. Payment Deductions: total_outstanding = GREATEST(0.00, (prev_os + cur_os) - amount_paid)
-- 5. Full Clearance & Settled State
-- 6. Flow of Late Adjustments (Challans, Accident Credits, Waivers) into Next Week
-- ============================================================================

DO $$
DECLARE
    v_rec RECORD;
    v_count INT;
BEGIN
    RAISE NOTICE '=================================================================';
    RAISE NOTICE 'STARTING HISAAB CUMULATIVE CUTOFF STORED PROCEDURE TEST SUITE';
    RAISE NOTICE '=================================================================';

    -- -------------------------------------------------------------------------
    -- 0. PREPARATION & SEED CONFIG
    -- -------------------------------------------------------------------------
    INSERT INTO public.hisaab_system_config (config_key, config_value, description)
    VALUES 
        ('enable_roll_forward', 'true', 'Enabled for test verification'),
        ('cutoff_week_id', 'CY26WK41', 'Cutoff baseline week')
    ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value;

    -- Setup settlement calendar weeks
    DELETE FROM public.hisaab_settlement_weeks WHERE week_id IN ('CY26WK41', 'CY26WK42', 'CY26WK43');
    
    INSERT INTO public.hisaab_settlement_weeks (
        week_id, settlement_year, settlement_week, week_start, week_end, lock_cutoff_at, is_locked
    ) VALUES 
        ('CY26WK41', 2026, 41, '2026-10-05', '2026-10-11', '2026-10-12 12:00:00+05:30', FALSE),
        ('CY26WK42', 2026, 42, '2026-10-12', '2026-10-18', '2026-10-19 12:00:00+05:30', FALSE),
        ('CY26WK43', 2026, 43, '2026-10-19', '2026-10-25', '2026-10-26 12:00:00+05:30', FALSE);

    -- Setup test partner onboarding
    CREATE TABLE IF NOT EXISTS public.core_partner_onboarding (
        partner_id VARCHAR(50) PRIMARY KEY,
        driver_name VARCHAR(150),
        account_number VARCHAR(50),
        ifsc_code VARCHAR(50),
        security_deposit NUMERIC(12,2)
    );

    INSERT INTO public.core_partner_onboarding (partner_id, driver_name, account_number, ifsc_code, security_deposit)
    VALUES 
        ('TEST_DRV_001', 'Vivek Sharma', 'ACC112233', 'HDFC0001234', 5000.00),
        ('TEST_DRV_002', 'Ramesh Patel', 'ACC445566', 'ICIC0005678', 5000.00)
    ON CONFLICT (partner_id) DO UPDATE SET driver_name = EXCLUDED.driver_name;

    -- Cleanup test data in partner weekly and vehicle weekly
    DELETE FROM public.hisaab_partner_weekly WHERE partner_id IN ('TEST_DRV_001', 'TEST_DRV_002');
    
    CREATE TABLE IF NOT EXISTS public.hisaab_vehicle_weekly (
        id BIGSERIAL PRIMARY KEY,
        week_id VARCHAR(20) NOT NULL,
        partner_id VARCHAR(50) NOT NULL,
        partner_name VARCHAR(150),
        partner_type VARCHAR(20) DEFAULT 'Individual',
        city VARCHAR(50) DEFAULT 'HYD',
        vehicle_number VARCHAR(20) NOT NULL,
        onroad_days INT DEFAULT 6,
        uber_trips INT DEFAULT 0,
        ola_trips INT DEFAULT 0,
        rapido_trips INT DEFAULT 0,
        net_weekly_lease_rental NUMERIC(12,2) DEFAULT 0.00,
        uber_total_earnings NUMERIC(12,2) DEFAULT 0.00,
        ola_net_revenue NUMERIC(12,2) DEFAULT 0.00,
        rapido_net_revenue NUMERIC(12,2) DEFAULT 0.00,
        uber_cash_collection NUMERIC(12,2) DEFAULT 0.00,
        ola_cash_collection NUMERIC(12,2) DEFAULT 0.00,
        rapido_cash_collected NUMERIC(12,2) DEFAULT 0.00,
        weekly_platform_incentive NUMERIC(12,2) DEFAULT 0.00,
        vehicle_adjustments NUMERIC(12,2) DEFAULT 0.00,
        challan_amount NUMERIC(12,2) DEFAULT 0.00,
        accident_penalties NUMERIC(12,2) DEFAULT 0.00,
        tds_amount NUMERIC(12,2) DEFAULT 0.00,
        current_week_os NUMERIC(12,2) DEFAULT 0.00
    );

    DELETE FROM public.hisaab_vehicle_weekly WHERE partner_id IN ('TEST_DRV_001', 'TEST_DRV_002');
    DELETE FROM public.hisaab_adjustments_ledger WHERE partner_id IN ('TEST_DRV_001', 'TEST_DRV_002');
    DELETE FROM public.hisaab_partner_opening_balances WHERE partner_id IN ('TEST_DRV_001', 'TEST_DRV_002');

    -- Seed baseline opening balance for TEST_DRV_001
    INSERT INTO public.hisaab_partner_opening_balances (
        partner_id, effective_week_id, opening_balance_due, cutoff_timestamp, remarks
    ) VALUES (
        'TEST_DRV_001', 'CY26WK41', 1850.50, '2026-10-12 12:00:00+05:30', 'Audited baseline cutoff'
    )
    ON CONFLICT (partner_id, effective_week_id) DO UPDATE SET
        opening_balance_due = EXCLUDED.opening_balance_due,
        cutoff_timestamp = EXCLUDED.cutoff_timestamp,
        remarks = EXCLUDED.remarks;

    -- -------------------------------------------------------------------------
    -- TEST 1: PRIORITY 1 OVERRIDE FOR WEEK 'CY26WK41' (CUTOFF WEEK)
    -- -------------------------------------------------------------------------
    RAISE NOTICE 'Running Test 1: Priority 1 Cutoff Opening Balance Override...';
    
    INSERT INTO public.hisaab_vehicle_weekly (
        week_id, partner_id, partner_name, vehicle_number, current_week_os
    ) VALUES (
        'CY26WK41', 'TEST_DRV_001', 'Vivek Sharma', 'TS09UB1001', 1000.00
    );

    CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK41');

    SELECT previous_outstanding, current_week_os, amount_paid_during_week, total_outstanding, net_amount_to_collect
    INTO v_rec
    FROM public.hisaab_partner_weekly
    WHERE week_id = 'CY26WK41' AND partner_id = 'TEST_DRV_001';

    IF v_rec.previous_outstanding <> 1850.50 THEN
        RAISE EXCEPTION 'Test 1 FAILED: Expected previous_outstanding 1850.50, got %', v_rec.previous_outstanding;
    END IF;

    IF v_rec.total_outstanding <> 2850.50 THEN
        RAISE EXCEPTION 'Test 1 FAILED: Expected total_outstanding 2850.50, got %', v_rec.total_outstanding;
    END IF;
    RAISE NOTICE 'Test 1 PASSED: Opening balance Rs. 1,850.50 successfully overrides previous dues for CY26WK41 (Total: Rs. 2,850.50).';

    -- -------------------------------------------------------------------------
    -- TEST 2: MONDAY 12 NOON LOCK IMMUTABILITY FOR 'CY26WK41'
    -- -------------------------------------------------------------------------
    RAISE NOTICE 'Running Test 2: Monday 12 Noon Lock Immutability...';

    UPDATE public.hisaab_settlement_weeks
    SET is_locked = TRUE, locked_at = '2026-10-12 12:00:00+05:30', locked_by = 'FINANCE_MONDAY_LOCK'
    WHERE week_id = 'CY26WK41';

    UPDATE public.hisaab_partner_weekly
    SET settlement_status = 'LOCKED'
    WHERE week_id = 'CY26WK41' AND partner_id = 'TEST_DRV_001';

    -- Attempt to change vehicle weekly dues for the locked week
    UPDATE public.hisaab_vehicle_weekly
    SET current_week_os = 9999.00
    WHERE week_id = 'CY26WK41' AND partner_id = 'TEST_DRV_001';

    -- Run procedure again on locked week: must abort/skip and leave data untouched
    CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK41');

    SELECT total_outstanding, settlement_status
    INTO v_rec
    FROM public.hisaab_partner_weekly
    WHERE week_id = 'CY26WK41' AND partner_id = 'TEST_DRV_001';

    IF v_rec.total_outstanding <> 2850.50 THEN
        RAISE EXCEPTION 'Test 2 FAILED: Locked week was mutated! total_outstanding is %', v_rec.total_outstanding;
    END IF;

    IF v_rec.settlement_status <> 'LOCKED' THEN
        RAISE EXCEPTION 'Test 2 FAILED: Expected settlement_status LOCKED, got %', v_rec.settlement_status;
    END IF;
    RAISE NOTICE 'Test 2 PASSED: Locked week CY26WK41 is completely immutable (preserved at Rs. 2,850.50).';

    -- -------------------------------------------------------------------------
    -- TEST 3: PRIORITY 2 ROLL-FORWARD INTO 'CY26WK42' (> 'CY26WK41')
    -- -------------------------------------------------------------------------
    RAISE NOTICE 'Running Test 3: Priority 2 Roll-Forward into CY26WK42...';

    INSERT INTO public.hisaab_vehicle_weekly (
        week_id, partner_id, partner_name, vehicle_number, current_week_os
    ) VALUES (
        'CY26WK42', 'TEST_DRV_001', 'Vivek Sharma', 'TS09UB1001', 1200.00
    );

    CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK42');

    SELECT previous_outstanding, current_week_os, total_outstanding
    INTO v_rec
    FROM public.hisaab_partner_weekly
    WHERE week_id = 'CY26WK42' AND partner_id = 'TEST_DRV_001';

    IF v_rec.previous_outstanding <> 2850.50 THEN
        RAISE EXCEPTION 'Test 3 FAILED: Expected previous_outstanding 2850.50, got %', v_rec.previous_outstanding;
    END IF;

    IF v_rec.total_outstanding <> 4050.50 THEN
        RAISE EXCEPTION 'Test 3 FAILED: Expected total_outstanding 4050.50, got %', v_rec.total_outstanding;
    END IF;
    RAISE NOTICE 'Test 3 PASSED: Prior debt Rs. 2,850.50 rolled forward to CY26WK42 + new dues Rs. 1,200 = Rs. 4,050.50.';

    -- -------------------------------------------------------------------------
    -- TEST 4: MID-WEEK PARTIAL PAYMENT REDUCES TOTAL OUTSTANDING
    -- -------------------------------------------------------------------------
    RAISE NOTICE 'Running Test 4: Payment Deduction Reduces total_outstanding...';

    -- Driver pays Rs 2,000 partial payment
    UPDATE public.hisaab_partner_weekly
    SET amount_paid_during_week = 2000.00
    WHERE week_id = 'CY26WK42' AND partner_id = 'TEST_DRV_001';

    -- Re-run sync to simulate scheduled re-calc; payment must be preserved
    CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK42');

    SELECT previous_outstanding, current_week_os, amount_paid_during_week, total_outstanding, net_amount_to_collect
    INTO v_rec
    FROM public.hisaab_partner_weekly
    WHERE week_id = 'CY26WK42' AND partner_id = 'TEST_DRV_001';

    IF v_rec.amount_paid_during_week <> 2000.00 THEN
        RAISE EXCEPTION 'Test 4 FAILED: amount_paid_during_week lost! Got %', v_rec.amount_paid_during_week;
    END IF;

    -- Formula check: GREATEST(0.00, (2850.50 + 1200.00) - 2000.00) = 2050.50
    IF v_rec.total_outstanding <> 2050.50 THEN
        RAISE EXCEPTION 'Test 4 FAILED: Expected total_outstanding 2050.50, got %', v_rec.total_outstanding;
    END IF;

    IF v_rec.net_amount_to_collect <> 2050.50 THEN
        RAISE EXCEPTION 'Test 4 FAILED: Expected net_amount_to_collect 2050.50, got %', v_rec.net_amount_to_collect;
    END IF;
    RAISE NOTICE 'Test 4 PASSED: Payment of Rs. 2,000 reduced total_outstanding to Rs. 2,050.50.';

    -- -------------------------------------------------------------------------
    -- TEST 5: FULL PAYMENT & COMPLETE CLEARANCE IN 'CY26WK43'
    -- -------------------------------------------------------------------------
    RAISE NOTICE 'Running Test 5: Full Payment and Settlement Clearance in CY26WK43...';

    -- Freeze week 42
    UPDATE public.hisaab_settlement_weeks
    SET is_locked = TRUE, locked_at = '2026-10-19 12:00:00+05:30', locked_by = 'FINANCE_MONDAY_LOCK'
    WHERE week_id = 'CY26WK42';

    UPDATE public.hisaab_partner_weekly
    SET settlement_status = 'LOCKED'
    WHERE week_id = 'CY26WK42' AND partner_id = 'TEST_DRV_001';

    -- Week 43 generates Rs 800.00 in dues
    INSERT INTO public.hisaab_vehicle_weekly (
        week_id, partner_id, partner_name, vehicle_number, current_week_os
    ) VALUES (
        'CY26WK43', 'TEST_DRV_001', 'Vivek Sharma', 'TS09UB1001', 800.00
    );

    CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK43');

    -- Total due before payment: 2050.50 carried forward + 800.00 = 2850.50
    SELECT previous_outstanding, current_week_os, total_outstanding
    INTO v_rec
    FROM public.hisaab_partner_weekly
    WHERE week_id = 'CY26WK43' AND partner_id = 'TEST_DRV_001';

    IF v_rec.previous_outstanding <> 2050.50 THEN
        RAISE EXCEPTION 'Test 5 FAILED: Expected previous_outstanding 2050.50, got %', v_rec.previous_outstanding;
    END IF;

    IF v_rec.total_outstanding <> 2850.50 THEN
        RAISE EXCEPTION 'Test 5 FAILED: Expected total_outstanding 2850.50, got %', v_rec.total_outstanding;
    END IF;

    -- Driver makes full payment of Rs. 2,850.50!
    UPDATE public.hisaab_partner_weekly
    SET amount_paid_during_week = 2850.50
    WHERE week_id = 'CY26WK43' AND partner_id = 'TEST_DRV_001';

    CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK43');

    SELECT total_outstanding, net_amount_to_collect, net_bank_payout
    INTO v_rec
    FROM public.hisaab_partner_weekly
    WHERE week_id = 'CY26WK43' AND partner_id = 'TEST_DRV_001';

    IF v_rec.total_outstanding <> 0.00 THEN
        RAISE EXCEPTION 'Test 5 FAILED: Expected total_outstanding 0.00, got %', v_rec.total_outstanding;
    END IF;

    IF v_rec.net_amount_to_collect <> 0.00 THEN
        RAISE EXCEPTION 'Test 5 FAILED: Expected net_amount_to_collect 0.00, got %', v_rec.net_amount_to_collect;
    END IF;
    RAISE NOTICE 'Test 5 PASSED: Full payment cleared debt to exactly Rs. 0.00 (Settled!).';

    -- -------------------------------------------------------------------------
    -- TEST 6: LATE ADJUSTMENTS (CHALLAN & ACCIDENT CREDIT) FLOW TO NEXT WEEK
    -- -------------------------------------------------------------------------
    RAISE NOTICE 'Running Test 6: Late Adjustments Flowing into Next Week...';

    -- For partner TEST_DRV_002: Week 41 is locked, but a challan (debit Rs 500)
    -- and accident credit (credit Rs 300) are logged for Week 42 as prior period adjustments.
    INSERT INTO public.hisaab_adjustments_ledger (
        partner_id, settlement_week_id, adjustment_type, polarity, amount, is_prior_period, approval_status
    ) VALUES 
        ('TEST_DRV_002', 'CY26WK42', 'CHALLAN', 'DEBIT', 500.00, TRUE, 'Approved'),
        ('TEST_DRV_002', 'CY26WK42', 'ACCIDENT_CREDIT', 'CREDIT', 300.00, TRUE, 'Approved');

    INSERT INTO public.hisaab_vehicle_weekly (
        week_id, partner_id, partner_name, vehicle_number, current_week_os
    ) VALUES (
        'CY26WK42', 'TEST_DRV_002', 'Ramesh Patel', 'TS09UB2002', 1500.00
    );

    -- Run sync on week 42 (unlock temporarily for test 6)
    UPDATE public.hisaab_settlement_weeks SET is_locked = FALSE WHERE week_id = 'CY26WK42';
    CALL public.sp_sync_hisaab_cumulative_cutoff('CY26WK42', 'TEST_DRV_002');

    SELECT current_week_os, prior_period_adjustments, total_outstanding
    INTO v_rec
    FROM public.hisaab_partner_weekly
    WHERE week_id = 'CY26WK42' AND partner_id = 'TEST_DRV_002';

    -- Net adjustment: +500 (debit) - 300 (credit) = +200.00
    IF v_rec.prior_period_adjustments <> 200.00 THEN
        RAISE EXCEPTION 'Test 6 FAILED: Expected prior_period_adjustments 200.00, got %', v_rec.prior_period_adjustments;
    END IF;

    -- Total: 0 previous + 1500 current + 200 adjustments = 1700.00
    IF v_rec.total_outstanding <> 1700.00 THEN
        RAISE EXCEPTION 'Test 6 FAILED: Expected total_outstanding 1700.00, got %', v_rec.total_outstanding;
    END IF;
    RAISE NOTICE 'Test 6 PASSED: Late adjustments (Rs. 500 challan - Rs. 300 credit = Rs. 200) flowed forward into CY26WK42 without modifying locked CY26WK41.';

    RAISE NOTICE '=================================================================';
    RAISE NOTICE 'ALL 6 STORED PROCEDURE & LOCK TESTS PASSED SUCCESSFULLY!';
    RAISE NOTICE '=================================================================';
END;
$$ LANGUAGE plpgsql;
