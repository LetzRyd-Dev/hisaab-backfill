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

### Pan-India Portfolio Breakdown (From Master Summary Sheets)

| City | Master Sheet Name | Total Partners | Status Breakdown | Operational Pending Dues ($B_0$) | Pending Security Deposit | Total Dues incl. Deposit |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Bangalore (BLR)** | `'Summary  Pending Dues '` | **1,663** | 356 Active, 1,307 Inactive | **₹15,783,589.20** | ₹2,335,651.00 | ₹18,119,240.20 |
| **Hyderabad (HYD)** | `'Summary Pending Dues'` | **469** | 79 Active, 390 Inactive | **₹12,987,581.26** | ₹1,046,010.00 | ₹14,033,591.26 |
| **Mumbai (MUM)** | `'Summary  Pending Dues '` | **267** | 57 Active, 210 Inactive | **₹4,046,972.37** | ₹1,243,700.00 | ₹5,290,672.37 |
| **Total Pan-India** | — | **2,399 Partners** | **492 Active, 1,907 Inactive** | **₹32,818,142.83** (~₹3.28 Cr) | **₹4,625,361.00** (~₹46.25 L) | **₹37,443,503.83** (~₹3.74 Cr) |

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

## 4. Cutoff Ingestion Script

A standalone automated ETL script is provided in the repository:
[`scripts/etl_ingest_cutoff_baseline.py`](file:///c:/Users/anura/Downloads/LetzRyd%20Antigravity/scripts/etl_ingest_cutoff_baseline.py).

### How to Run:
```bash
python scripts/etl_ingest_cutoff_baseline.py \
  --hyd "C:\Users\anura\Downloads\HYD_Payment Summary.xlsx" \
  --blr "C:\Users\anura\Downloads\Blr_Payment Summary [ Internal ].xlsx" \
  --mum "C:\Users\anura\Downloads\Mum_Payment Summary.xlsx" \
  --effective-week "CY26WK41"
```

This populates `public.hisaab_partner_opening_balances` in PostgreSQL with:
- `partner_id` (`Vendor Code`)
- `effective_week_id` (`CY26WK41`)
- `opening_balance_due` (`Pending Dues`)
- `remarks` (Detailed audit metadata: City, Type, Active Status, Deposit Paid).
