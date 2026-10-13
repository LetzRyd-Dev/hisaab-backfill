# PAYMENT SUMMARY EXCEL INGESTION & OUTSTANDING BASELINE GUIDE

**Audit Date:** October 12, 2026  
**Files Audited:**
1. `HYD_Payment Summary.xlsx` (Hyderabad Fleet Ledger)
2. `Blr_Payment Summary [ Internal ].xlsx` (Bangalore Fleet Ledger)
3. `Mum_Payment Summary.xlsx` (Mumbai Fleet Ledger)

---

## 1. Executive Summary: What is in the 3 Files?

The three Excel files represent the master financial reconciliation workbooks maintained by the LetzRyd Finance and Operations teams. Each workbook reconciles:
- **Cumulative Weekly Trip Debits** from `'Raw Data'` (all hisaabs generated since fleet launch).
- **Cumulative Collections & Driver Payments** from `'Payment Raw'` and `'Weekly Hisaab Paid to Driver'`.
- **Security Deposit Escrow & Deductions** from `'Deposit Conversion'`.

### Pan-India Portfolio Breakdown (From Master Summary Sheets - Audited Baseline Oct 12, 2026)

| City | Master Sheet Name | Total Partners | Status Breakdown | Operational Pending Dues ($B_0$) | Pending Security Deposit | Total Dues incl. Deposit |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Bangalore (BLR)** | `'Summary  Pending Dues '` | **1,698** | 402 Active, 1,296 Inactive | **₹19,124,238.27** | ₹6,219,708.00 | ₹25,343,946.27 |
| **Hyderabad (HYD)** | `'Summary Pending Dues'` | **479** | 90 Active, 389 Inactive | **₹12,976,581.26** | ₹1,067,010.00 | ₹14,043,591.26 |
| **Mumbai (MUM)** | `'Summary  Pending Dues '` | **269** | 59 Active, 210 Inactive | **₹4,040,772.37** | ₹1,252,200.00 | ₹5,292,972.37 |
| **Total Pan-India** | — | **2,446 Partners** | **551 Active, 1,895 Inactive** | **₹36,141,591.90** (~₹3.61 Cr) | **₹8,538,918.00** (~₹85.39 L) | **₹44,680,509.90** (~₹4.47 Cr) |

---

## 2. Which Tab, Columns, and Rows to Use

### Master Tab Selection
- **Definitive Master Sheet:** **`'Summary Pending Dues'`** (or `'Summary  Pending Dues '` in BLR & MUM).
- **Rows to Ingest:** **Row 3 to End of Table** (Row 1 contains `=SUBTOTAL(9, ...)` totals; Row 2 contains column headers).

> [!CAUTION]
> **Do NOT use `'City Receivable Report'`:**
> 1. In Mumbai and Hyderabad, `'City Receivable Report'` omits driver payouts/refunds (`Amount Paid to Drivers`), understating debt for refunded drivers.
> 2. The formula in row 1 of `'City Receivable Report'` is hardcoded to row 182, accidentally dropping over 280 drivers from the sheet's displayed total.
> 3. In Bangalore, Column I contains `#VALUE!` / `#REF!` broken formula errors.

### Column Mapping Specification

#### A. Hyderabad (`HYD_Payment Summary.xlsx`) & Bangalore (`Blr_Payment Summary [ Internal ].xlsx`):
| Field | Column Letter | Column Header Name | Role in Cutoff Engine |
| :--- | :--- | :--- | :--- |
| **Operating Status** | **Col A** | `Status` | `Active` vs `Inactive` |
| **Partner Identifier** | **Col B** | `Vendor Code` | Primary Key (`LETZHYD...` / `LETZBLR...`) |
| **Partner Name** | **Col C** | `Vendor Name` | Partner display name |
| **Partner Type** | **Col D** | `Type` | `Individual` vs `Operator` |
| **Allocated Vehicles** | **Col E** | `Current allotted Car count` | Vehicle fleet count |
| **Refundable Deposit** | **Col F** | `Refundable Deposit` | Security deposit net liability |
| **Deposit Paid** | **Col H** | `Deposit Paid` | Cash deposit collected |
| **Pending Deposit** | **Col I** | `Pending Deposit` | Unpaid security deposit target |
| **Operational Dues ($B_0$)** | **Col J** | **`Pending Dues`** | **Opening Balance to ingest into $B_0$** |
| **Cumulative Billed** | **Col K** | `Total Weekly O/S` | All-time billed hisaab gross |
| **Cumulative Collections** | **Col M** | `Cumulative Amount Paid ` | All-time paid hisaab cash |
| **Total Operational O/S** | **Col N** | `Total O/s` | `Pending Dues + Current Week O/S` |
| **Total O/S incl. Deposit** | **Col O** | `Total O/s incl. Deposit` | Operational Dues + Unpaid Security Deposit |

#### B. Mumbai (`Mum_Payment Summary.xlsx`):
*(Note: Mumbai includes two extra columns on the left: `Duplicate Check` and `Phone`, shifting all data columns by +1 or +2)*
| Field | Column Letter | Column Header Name | Role in Cutoff Engine |
| :--- | :--- | :--- | :--- |
| **Partner Phone** | **Col B** | `Phone` | 10-digit mobile number |
| **Partner Identifier** | **Col C** | `Vendor Code` | Primary Key (`LETZMUM...`) |
| **Partner Name** | **Col D** | `Vendor Name` | Partner display name |
| **Partner Type** | **Col E** | `Type` | `Individual` vs `Operator` |
| **Operating Status** | **Col G** | `Stutas` | `Active` vs `Inactive` |
| **Pending Deposit** | **Col K** | `Pending [ Deposit ]` | Unpaid security deposit target |
| **Operational Dues ($B_0$)** | **Col P** | **`Pending Dues`** | **Opening Balance to ingest into $B_0$** |
| **Total Operational O/S** | **Col Q** | `Total O/s` | Operational dues |
| **Total O/S incl. Deposit** | **Col R** | `Total O/s incl. Deposit` | Operational Dues + Unpaid Security Deposit |

---

## 3. How Drivers vs Operators are Managed

1. **Clean Partner Grain (`Vendor Code`):**
   - Individual Drivers are prefixed: `LETZ<CITY><PHONE>` (e.g. `LETZBLR9535627470`).
   - Fleet Operators are prefixed: `LETZ<CITY>IP<PHONE>` or `LETZ<CITY>OP<PHONE>` (e.g. `LETZHYDIP8919747762`).
2. **Fleet Vehicle Consolidation:**
   - In `'Raw Data'`, each vehicle trip hisaab is tagged with a vehicle registration number.
   - For operators, all vehicles attached to their fleet roll up under the master operator `Vendor Code` via the `Remapping` column.
   - For example, Operator `LETZMUMIP9004200105` (Gaadylo Enterprises) has 101 vehicles across 2,469 entries in raw data, consolidated into a single row in `'Summary  Pending Dues '`.
3. **Deposit Separation:**
   - The engine strictly keeps Security Deposits separate from Hisaab Rent Debt.
   - When ingesting $B_0$, ingest **`Pending Dues`** as the hisaab opening balance.
   - The security deposit remains in its own ledger account and is only converted into hisaab debt upon formal driver offboarding.

---

## 4. Cutoff Ingestion Script & Staging Snapshot

An automated, idempotent ETL ingestion engine and snapshot are provided in this directory:
- **ETL Script:** `07_hisaab_cutoff_rolling_engine/ingest_cutoff_baseline_oct12.py`
- **Staging Snapshot CSV:** `07_hisaab_cutoff_rolling_engine/cutoff_baseline_oct12_snapshot.csv` (2,446 audited partners, SHA256: `97673f97ad3a3ed256c42925a064ac29f536ca4eac810472cd19101bc8687030`)

### How to Run:
```bash
# 1. Verification and staging CSV generation (Dry-run, zero DB changes)
python ingest_cutoff_baseline_oct12.py --skip-db

# 2. Database Ingestion (Idempotent ON CONFLICT into public.hisaab_partner_opening_balances)
python ingest_cutoff_baseline_oct12.py --commit
```

This populates `public.hisaab_partner_opening_balances` in PostgreSQL with:
- `partner_id` (`Vendor Code`)
- `effective_week_id` (`CY26WK41`)
- `opening_balance_due` (`Pending Dues`)
- `cutoff_timestamp` (`2026-10-12 12:00:00+05:30` - Monday Noon Lock)
- `remarks` (Detailed audit metadata: City, Type, Active Status, Deposit Paid).
