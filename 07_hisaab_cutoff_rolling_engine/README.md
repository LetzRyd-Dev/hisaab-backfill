# LetzRyd Cutoff-Based Cumulative Rolling Hisaab & Outstanding Engine
========================================================================

## Overview
This dedicated module contains the production-ready implementation, execution blueprint, and mathematical verification test suite for transitioning LetzRyd from **isolated weekly hisaab cycles** to a **Cutoff-Based Cumulative Rolling Ledger**.

### Core Architecture Principles
1. **Monday 12:00 PM (Noon) IST Cutoff Lock:**
   - Settlement cycles lock strictly at Monday 12:00 PM Noon IST (`lock_cutoff_at`).
   - Locked weeks (`is_locked = TRUE`) are completely immutable and cannot be tampered with or modified.
   - Any payment arriving post-12:00 PM automatically credits to the next open settlement cycle.
2. **Priority 1: Baseline Override on Cutoff Week (`CY26WK41`):**
   - For cutoff week `CY26WK41` (covering week ending Oct 11/12, 2026), audited opening balances ($B_0$) from `public.hisaab_partner_opening_balances` override previous outstanding.
3. **Priority 2: Rolling Debt Carry-Forward for Subsequent Weeks:**
   - For all weeks post-cutoff ($W > \text{CY26WK41}$), previous outstanding rolls forward:
     $$\text{Previous Outstanding}_{W} = \max(0.00, \text{Total Outstanding}_{W-1})$$
4. **Real-Time Payment Deductions:**
   - Real-time gateway/webhook payments reduce net balance instantly:
     $$\text{Total Outstanding}_{W} = \max(0.00, (\text{Previous Outstanding}_{W} + \text{Current Period O/S}_{W}) - \text{Payments Received}_{W})$$
5. **Zero Mutation & Core Isolation Safety:**
   - Core production tables (`app_hisaabs`, `app_drivers`, `vehicles`, `app_driver_allocations`, `app_payments`) remain 100% untouched and isolated.

---

## Deliverables & Directory Contents

| Artifact | Type | Description |
| :--- | :--- | :--- |
| **`README.md`** | Markdown | Module overview, architectural principles, runbook, and verification commands. |
| **`CUTOFF_CUMULATIVE_ROLLING_HISAAB_ARCHITECTURE.md`** | Architecture Spec | Master deep architecture specification updated with Monday 12:00 PM lock & Oct 11/12 baseline. |
| **`PAYMENT_SUMMARY_EXCEL_INGESTION_GUIDE.md`** | Ingestion Guide | Field-by-field mapping, formula audit, and column selection guide for HYD, BLR, and MUM Excel workbooks. |
| **`schema_cutoff_baseline.sql`** | PostgreSQL DDL | Production table definitions: `hisaab_partner_opening_balances`, audit history, constraints, and indexes. |
| **`sp_sync_hisaab_cumulative_cutoff.sql`** | PostgreSQL Procedure | Complete stored procedure enforcing Monday 12PM lock, Priority 1 override, Priority 2 roll-forward, and late adjustment routing. |
| **`ingest_cutoff_baseline_oct12.py`** | Python ETL Engine | Automated, idempotent parser extracting `Pending Dues` for 2,446 partners across Bangalore, Hyderabad, and Mumbai. |
| **`cutoff_baseline_oct12_snapshot.csv`** | Staging Dataset | Audited Pan-India snapshot (2,446 partners, ₹36,141,591.90 total dues, ₹8,538,918.00 pending deposit, SHA256: `97673f97ad3a...`). |
| **`test_simulation.py`** | Verification Suite | Automated Python simulation testing all 6 architectural scenarios and edge cases (100% pass rate). |
| **`test_suite_sp_sync_hisaab.sql`** | SQL Test Harness | Comprehensive SQL simulation harness with isolated temporary tables verifying stored procedure behavior. |
| **`api_payment_integration_guide.md`** | API Integration | Technical guide for backend payment gateway (Cashfree/UPI) integration, webhook handling, and real-time ledger updates. |
| **`CORE_TABLES_SAFETY_CERTIFICATE.md`** | Safety Audit | Formal sign-off and checksum audit confirming zero mutation across all core database tables. |
| **`cumulative_engine.py`** | Core Engine | Python implementation of cumulative settlement engine supporting dry-runs, adjustments, and roll-forward logic. |
| **`sample_cutoff_input.csv`** | Template CSV | Reference template format for manual partner cutoff ingestion. |

---

## Quickstart & Verification Commands

### 1. Run Python Simulation Test Suite (100% Pass)
```bash
cd 07_hisaab_cutoff_rolling_engine
python test_simulation.py
```

### 2. Verify Baseline Excel Parsing (Dry-run / Staging)
```bash
python ingest_cutoff_baseline_oct12.py --skip-db
```

### 3. Apply Schema & Stored Procedure to PostgreSQL
```bash
psql -h <HOST> -U <USER> -d letzryd_dev -f schema_cutoff_baseline.sql
psql -h <HOST> -U <USER> -d letzryd_dev -f sp_sync_hisaab_cumulative_cutoff.sql
```

### 4. Execute Full SQL Verification Suite
```bash
psql -h <HOST> -U <USER> -d letzryd_dev -f test_suite_sp_sync_hisaab.sql
```

---

## Pan-India Baseline Summary (`CY26WK41` as of Oct 12, 2026)

* **Hyderabad (HYD):** 479 Partners (90 Active, 389 Inactive) | Pending Dues: **₹12,976,581.26** | Deposit: **₹1,067,010.00**
* **Bangalore (BLR):** 1,698 Partners (402 Active, 1,296 Inactive) | Pending Dues: **₹19,124,238.27** | Deposit: **₹6,219,708.00**
* **Mumbai (MUM):** 269 Partners (59 Active, 210 Inactive) | Pending Dues: **₹4,040,772.37** | Deposit: **₹1,252,200.00**
* **Pan-India Total ($B_0$):** **2,446 Partners** (551 Active, 1,895 Inactive) | **₹36,141,591.90** (~₹3.61 Cr) | Deposit: **₹8,538,918.00** (~₹85.39 L)
* **Snapshot SHA-256 Checksum:** `97673f97ad3a3ed256c42925a064ac29f536ca4eac810472cd19101bc8687030`
