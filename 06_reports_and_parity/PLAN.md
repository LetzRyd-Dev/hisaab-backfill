# MODULE 06: MULTI-GRAIN PARITY & EXCEL AUDIT REPORTING

**Module Directory:** `06_reports_and_parity/`  
**Target Output Artifacts:**
- Console Parity Scorecards (Zero-variance mathematical audit)
- Multi-Tab Financial Excel Statements (`CY26WKxx_HISAAB_STATEMENT.xlsx`)

---

## 1. OBJECTIVE & ARCHITECTURAL ROLE
Module 06 provides the **financial sign-off and verification layer**. 

Before any backfilled settlement cycle is considered complete or shared with leadership/finance, it must satisfy two requirements:
1. **Mathematical Multi-Grain Parity:** The sum of all individual vehicle liabilities in Tier 2 (`hisaab_vehicle_weekly`) must exactly equal the sum of all partner consolidated balances in Tier 3 (`hisaab_partner_weekly`):
   $$\sum \text{hisaab\_vehicle\_weekly.current\_week\_os} \equiv \sum \text{hisaab\_partner\_weekly.current\_week\_os} \quad (\text{Variance} = ₹0.00)$$
2. **Executive Financial Statements:** Generate clean, standardized Excel workbooks mirroring the historical financial templates used by city operations teams across Bangalore, Hyderabad, and Mumbai.

---

## 2. PARITY AUDIT GATES

| Check # | Verification Domain | Expected Result | Failure Action |
| :---: | :--- | :---: | :--- |
| **Gate 1** | **Multi-Grain Parity** | $\Delta \text{Current O/S} = ₹0.00$ | Reject week; inspect unmapped partner aggregation. |
| **Gate 2** | **Standalone Debt Isolation** | $\sum \text{previous\_outstanding} \equiv ₹0.00$ | Fatal error; roll-forward is leaking historical debt. |
| **Gate 3** | **Payout & Collection Split** | $\text{Net Payout} + \text{Net Collect} = \sum |\text{O/S}|$ | Check sign polarity partitioning. |
| **Gate 4** | **Bank Account Attribution** | $\text{Partners with Payout} > 0$ have valid IFSC & Account # | Alert operations to collect partner bank details. |

---

## 3. EXCEL STATEMENT SHEETS STRUCTURE

When `export_hisaab_excel.py` runs, it generates a multi-tab workbook:
1. **Sheet 1: `Executive Summary`**: Week dates, fleet size, total lease rent, platform earnings, passenger cash, TDS, net bank payouts, and net dues to collect.
2. **Sheet 2: `Vehicle Weekly Detail`**: Row-by-row mirror of `hisaab_vehicle_weekly` (Plate, Partner ID, Driver Name, City, Days, Rent, Uber, Ola, Challans, Adjustments, GPS, TDS, Net O/S).
3. **Sheet 3: `Partner Consolidated`**: Row-by-row rollup for operators (Cars Count, Total Rent, Net Bank Payout, Amount to Collect, Bank Account Number, IFSC Code).
4. **Sheet 4: `Bank Payouts Clearing`**: Filtered list of partners who are owed money (`net_bank_payout > 0`) formatted for treasury disbursement.

---

## 4. SCRIPTS IN THIS MODULE

1. **`audit_multi_grain_parity.py` (100% Read-Only):**
   - Asserts mathematical parity between Tier 2 and Tier 3 down to the exact paisa (diff = ₹0.00).
   - Generates the executive verification scorecard.
2. **`export_hisaab_excel.py` (Statement Generator):**
   - Queries `hisaab_vehicle_weekly` and `hisaab_partner_weekly` to produce an executive Excel workbook for finance and management review.
