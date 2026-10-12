"""
cumulative_engine.py
====================
Standalone Execution & Simulation Engine for LetzRyd Cutoff Hisaab.

Features:
1. Cutoff Loader: Ingests verified driver/operator balances as of a given cutoff date.
2. Weekly Settlement Calculator: Simulates subsequent weeks where:
   - Week 1: Cutoff Balance + Week 1 Hisaab
   - Week 2: If Week 1 not paid, (Remaining Week 1) + Week 2 Hisaab
   - Week 3: If partial payment made, (Previous Remaining) + Week 3 Hisaab - Payment
"""

import csv
import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional

@dataclass
class PartnerCutoff:
    partner_id: str
    driver_name: str
    phone: str
    cutoff_date: str
    effective_week: str
    opening_balance_due: float
    security_deposit_pending: float

@dataclass
class WeeklyHisaabRecord:
    week_id: str
    partner_id: str
    gross_earnings: float
    rent_charges: float
    cash_collected: float
    penalties_tds: float
    
    @property
    def current_week_os(self) -> float:
        # Net dues generated solely in this week:
        # Deductions + Cash Kept by Driver - Gross Earnings
        return round(self.rent_charges + self.penalties_tds + self.cash_collected - self.gross_earnings, 2)

@dataclass
class RollingSettlementState:
    week_id: str
    partner_id: str
    previous_outstanding: float
    current_week_os: float
    payment_received: float
    total_outstanding: float
    to_collect: float
    to_pay: float
    status: str

class CumulativeHisaabEngine:
    def __init__(self, cutoff_week_id: str):
        self.cutoff_week_id = cutoff_week_id
        self.openings: Dict[str, PartnerCutoff] = {}
        self.history: Dict[str, List[RollingSettlementState]] = {}

    def load_cutoff_from_csv(self, csv_filepath: str):
        """Loads opening balances from verified cutoff CSV file."""
        with open(csv_filepath, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = row['partner_id'].strip()
                self.openings[pid] = PartnerCutoff(
                    partner_id=pid,
                    driver_name=row.get('driver_name', ''),
                    phone=row.get('phone', ''),
                    cutoff_date=row.get('cutoff_date', ''),
                    effective_week=self.cutoff_week_id,
                    opening_balance_due=float(row.get('opening_balance_due', 0.0)),
                    security_deposit_pending=float(row.get('security_deposit_pending', 0.0))
                )
        print(f"Loaded {len(self.openings)} partner cutoffs for week {self.cutoff_week_id}.")

    def process_week(self, week_id: str, hisaab_data: List[WeeklyHisaabRecord], payments: Dict[str, float]) -> List[RollingSettlementState]:
        """
        Processes a weekly settlement for all partners.
        Cumulatively rolls forward unpaid balances from previous weeks.
        """
        results = []

        for h in hisaab_data:
            pid = h.partner_id
            paid = float(payments.get(pid, 0.0))

            # Determine previous outstanding
            if week_id == self.cutoff_week_id:
                # Week 1 (Cutoff week): Use baseline opening balance
                prev_os = self.openings.get(pid, PartnerCutoff(pid, '', '', '', week_id, 0.0, 0.0)).opening_balance_due
            else:
                # Subsequent weeks: Take previous week's ending total_outstanding after previous payments
                prev_weeks = self.history.get(pid, [])
                if prev_weeks:
                    prev_week_state = prev_weeks[-1]
                    prev_os = prev_week_state.total_outstanding
                else:
                    prev_os = 0.0

            # Calculate cumulative balance
            # Total Outstanding = (Previous Carried O/S) + (Current Week Generated O/S) - (Payment Made This Week)
            net_balance = round(prev_os + h.current_week_os - paid, 2)

            to_collect = max(0.0, net_balance)
            to_pay = max(0.0, -net_balance)
            status = "settled" if to_collect == 0.0 and to_pay == 0.0 else ("to_collect" if to_collect > 0 else "to_pay")

            state = RollingSettlementState(
                week_id=week_id,
                partner_id=pid,
                previous_outstanding=prev_os,
                current_week_os=h.current_week_os,
                payment_received=paid,
                total_outstanding=net_balance,
                to_collect=to_collect,
                to_pay=to_pay,
                status=status
            )

            if pid not in self.history:
                self.history[pid] = []
            self.history[pid].append(state)
            results.append(state)

        return results
