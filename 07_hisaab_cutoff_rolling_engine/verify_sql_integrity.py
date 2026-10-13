"""
verify_sql_integrity.py
========================
Static verification and sanity checker for SQL schemas and stored procedures.
"""

import os
import re

def verify_file(filepath):
    print(f"\n--- Checking {os.path.basename(filepath)} ---")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    errors = []

    # Check for basic balance of quotes and dollar quotes
    single_quotes = content.count("'")
    if single_quotes % 2 != 0:
        errors.append(f"Odd number of single quotes: {single_quotes}")

    # Check dollar quotes
    dollar_procs = content.count("$procedure$")
    if dollar_procs % 2 != 0:
        errors.append(f"Unmatched $procedure$ tags: {dollar_procs}")

    dollar_blocks = content.count("$$")
    if dollar_blocks % 2 != 0:
        errors.append(f"Unmatched $$ blocks: {dollar_blocks}")

    # Specific requirements verification:
    if "schema_cutoff_baseline.sql" in filepath:
        required_cols = [
            "partner_id", "effective_week_id", "opening_balance_due", 
            "cutoff_timestamp", "remarks", "created_at"
        ]
        for col in required_cols:
            if col not in content:
                errors.append(f"Missing required column in schema: {col}")
            else:
                print(f"  [OK] Found required column: {col}")

        if "uq_partner_effective_week" not in content and "UNIQUE (partner_id, effective_week_id)" not in content:
            errors.append("Missing UNIQUE constraint on (partner_id, effective_week_id)")
        else:
            print("  [OK] Found UNIQUE (partner_id, effective_week_id) constraint")

    if "sp_sync_hisaab_cumulative_cutoff.sql" in filepath:
        checks = [
            ("lock_cutoff_at", "lock_cutoff_at timestamp check"),
            ("12:00:00+05:30", "Monday 12:00 PM (Noon) IST cutoff"),
            ("is_locked = TRUE", "Immutable check for locked week"),
            ("CY26WK41", "Cutoff anchor week CY26WK41"),
            ("GREATEST(0.00", "Floor at 0.00 for total_outstanding and previous_outstanding"),
            ("amount_paid_during_week", "Deduction of payments made during the week"),
            ("prior_period_adjustments", "Flow of prior period adjustments")
        ]
        for term, desc in checks:
            if term not in content:
                errors.append(f"Missing required logic element: {desc} ('{term}')")
            else:
                print(f"  [OK] Found logic element: {desc}")

    if errors:
        for err in errors:
            print(f"  [ERROR] {err}")
        return False
    else:
        print(f"  All static checks passed for {os.path.basename(filepath)}.")
        return True

def main():
    base_dir = os.path.dirname(__file__)
    files = [
        os.path.join(base_dir, "schema_cutoff_baseline.sql"),
        os.path.join(base_dir, "sp_sync_hisaab_cumulative_cutoff.sql"),
        os.path.join(base_dir, "test_suite_sp_sync_hisaab.sql")
    ]
    all_ok = True
    for f in files:
        if not verify_file(f):
            all_ok = False

    print("\n=======================================================")
    if all_ok:
        print("SQL STATIC INTEGRITY VERIFICATION: 100% SUCCESS")
    else:
        print("SQL STATIC INTEGRITY VERIFICATION: FAILED")
    print("=======================================================")

if __name__ == "__main__":
    main()
