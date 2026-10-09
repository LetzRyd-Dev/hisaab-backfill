"""
Safety, Immutability & Backfill Isolation Guards for LetzRyd Hisaab Engine.
Enforces non-negotiable isolation rules:
1. Protected Weeks Guard (prevents touching production weeks).
2. Lock Status Guard (prevents touching is_locked weeks).
3. Roll-Forward Zero-Check (ensures enable_roll_forward = 'false' & previous_outstanding = 0.00).
4. Date-Range Isolation Guard (validates dates strictly match target week).
5. Dry-Run Transaction Enforcement (rolls back unless --commit is passed).
"""

import sys
import logging
from typing import Optional, List
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("HisaabSafetyGuard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Weeks where Hisaab is already finalized / protected in production
PROTECTED_PRODUCTION_WEEKS = {
    "CY26WK26", "CY26WK27",
    "CY26WK36", "CY26WK37", "CY26WK38", "CY26WK39", "CY26WK40", "CY26WK41"
}

class SafetyViolationError(Exception):
    """Raised when an operation violates backfill safety isolation policies."""
    pass

class HisaabSafetyGuard:
    def __init__(self, session: Session, target_week_id: str, commit_enabled: bool = False):
        self.session = session
        self.target_week_id = target_week_id.strip().upper()
        self.commit_enabled = commit_enabled
        self.week_metadata = None

    def run_preflight_checks(self, allow_protected_override: bool = False) -> dict:
        """
        Runs exhaustive pre-flight verification before any SQL is executed.
        Aborts with SafetyViolationError if any guard fails.
        """
        logger.info(f"Running Pre-Flight Safety Checks for target week: {self.target_week_id}")

        # Guard 1: Protected Production List Check
        if self.target_week_id in PROTECTED_PRODUCTION_WEEKS and not allow_protected_override:
            raise SafetyViolationError(
                f"[BLOCKED] Target week {self.target_week_id} is in the PROTECTED PRODUCTION LIST "
                f"({sorted(list(PROTECTED_PRODUCTION_WEEKS))}). "
                f"Backfilling protected weeks is strictly prohibited to prevent data contamination."
            )

        # Guard 2: Settlement Week Lock Check
        stmt = text("""
            SELECT week_id, settlement_year, settlement_week, week_start, week_end, is_locked, locked_by
            FROM public.hisaab_settlement_weeks
            WHERE week_id = :week_id
        """)
        row = self.session.execute(stmt, {"week_id": self.target_week_id}).fetchone()
        if not row:
            raise SafetyViolationError(
                f"[NOT FOUND] Week {self.target_week_id} does not exist in hisaab_settlement_weeks."
            )

        self.week_metadata = {
            "week_id": row[0],
            "year": row[1],
            "week_num": row[2],
            "week_start": row[3],
            "week_end": row[4],
            "is_locked": row[5],
            "locked_by": row[6]
        }

        if self.week_metadata["is_locked"]:
            raise SafetyViolationError(
                f"[LOCKED] Settlement week {self.target_week_id} has is_locked = TRUE (locked by: {self.week_metadata['locked_by']}). "
                f"Direct backfill on locked weeks is blocked. Unlock requires authorized protocol."
            )

        # Guard 3: Roll-Forward Config Check
        cfg_stmt = text("""
            SELECT config_value FROM public.hisaab_system_config 
            WHERE config_key ILIKE 'enable_roll_forward'
        """)
        cfg_val = self.session.execute(cfg_stmt).scalar()
        if cfg_val is not None and str(cfg_val).strip().lower() == 'true':
            raise SafetyViolationError(
                f"[CONFIG HAZARD] hisaab_system_config.enable_roll_forward is '{cfg_val}'! "
                f"Must be 'false' for standalone historical backfill to prevent phantom debt cascading."
            )

        logger.info(
            f"[PASSED] Week {self.target_week_id} validated: "
            f"Range {self.week_metadata['week_start']} to {self.week_metadata['week_end']}, "
            f"is_locked=FALSE, Roll-Forward=DISABLED."
        )
        return self.week_metadata

    def validate_date_boundaries(self, start_date, end_date):
        """Ensures input date ranges exactly match the target settlement week."""
        if not self.week_metadata:
            self.run_preflight_checks()

        w_start = self.week_metadata["week_start"]
        w_end = self.week_metadata["week_end"]

        if str(start_date) != str(w_start) or str(end_date) != str(w_end):
            raise SafetyViolationError(
                f"[BOUNDARY MISMATCH] Date range {start_date}..{end_date} does not strictly match "
                f"settlement cycle {self.target_week_id} ({w_start}..{w_end}). "
                f"Cross-week date ranges are forbidden."
            )
        logger.info(f"[PASSED] Date boundaries strictly confined to {w_start}..{w_end}.")

    def run_post_calculation_audits(self):
        """
        Runs mathematical integrity and isolation checks after calculation.
        """
        logger.info(f"Running Post-Calculation Isolation Audits for {self.target_week_id}...")

        # Audit 1: Roll-Forward Zero-Check
        os_stmt = text("""
            SELECT 
                COUNT(*) as partner_count,
                COALESCE(SUM(ABS(previous_outstanding)), 0.00) as sum_prev_os
            FROM public.hisaab_partner_weekly
            WHERE week_id = :week_id
        """)
        row = self.session.execute(os_stmt, {"week_id": self.target_week_id}).fetchone()
        if row and row[1] > 0.00:
            raise SafetyViolationError(
                f"[CORRUPTION DETECTED] Hisaab partner weekly for {self.target_week_id} has non-zero "
                f"previous_outstanding: {row[1]} across {row[0]} partners! Must be exactly 0.00."
            )

        # Audit 2: Protected Weeks Contamination Check
        prot_stmt = text("""
            SELECT week_id, COUNT(*) 
            FROM public.hisaab_partner_weekly 
            WHERE week_id IN :protected_weeks 
              AND updated_at > NOW() - INTERVAL '5 minutes'
            GROUP BY week_id
        """)
        mutated_prot = self.session.execute(
            prot_stmt, 
            {"protected_weeks": tuple(PROTECTED_PRODUCTION_WEEKS)}
        ).fetchall()

        if mutated_prot:
            raise SafetyViolationError(
                f"[CRITICAL ALERT] Protected weeks were mutated during this run: {mutated_prot}! "
                f"Aborting immediately."
            )

        logger.info(f"[PASSED] Post-calculation audits passed: previous_outstanding = 0.00, zero protected weeks touched.")

    def finish_transaction(self):
        """Commits if --commit flag was passed; otherwise performs safe rollback."""
        if self.commit_enabled:
            self.session.commit()
            logger.info(f"[COMMITTED] Changes for {self.target_week_id} successfully committed to database.")
        else:
            self.session.rollback()
            logger.info(f"[DRY-RUN] Safe rollback executed. Use --commit flag to persist changes.")
