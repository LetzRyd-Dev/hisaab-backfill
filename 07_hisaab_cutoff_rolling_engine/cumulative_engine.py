"""
cumulative_engine.py
====================
Standalone Execution & Simulation Engine for LetzRyd Cutoff Hisaab.

Features:
1. Cutoff Loader: Ingests verified driver/operator balances as of Monday 12:00 PM (Noon) IST cutoff.
2. Monday 12 Noon Lock: Freezes weekly settlements; locked weeks are completely immutable.
3. Priority 1: opening_balance_due overrides previous_outstanding for week 'CY26WK41'.
4. Priority 2: For weeks > 'CY26WK41', previous_outstanding rolls forward:
   previous_outstanding = max(0.0, pw.total_outstanding).
5. Payments reduce total_outstanding:
   total_outstanding = max(0.0, (previous_outstanding + current_period_os) - amount_paid_during_week).
6. Late Adjustments Routing: Challans, accident credits, waivers for locked weeks flow into next week.
"""

import csv
import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Set

@dataclass
class PartnerCutoff:
    partner_id: str
    driver_name: str
    phone: str
    cutoff_date: str
    effective_week: str
    opening_balance_due: float
    security_deposit_pending: float
    cutoff_timestamp: str = "2026-10-12 12:00:00+05:30"
    remarks: str = "Audited baseline cutoff"

@dataclass
class WeeklyHisaabRecord:
    week_id: str
    partner_id: str
    gross_earnings: float
    rent_charges: float
    cash_collected: float
    penalties_tds: float
    prior_period_adjustments: float = 0.0  # challans (debit +), accident credits/waivers (credit -)
    
    @property
    def current_week_os(self) -> float:
        # Net dues generated solely from vehicle operations this week:
        # Rent + Penalties/TDS + Cash Kept - Gross Earnings
        return round(self.rent_charges + self.penalties_tds + self.cash_collected - self.gross_earnings, 2)

    @property
    def current_period_os(self) -> float:
        # Current period dues including operational delta and prior adjustments:
        return round(self.current_week_os + self.prior_period_adjustments, 2)

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
    def __init__(self, cutoff_week_id: str = "CY26WK41"):
        self.cutoff_week_id = cutoff_week_id
        self.openings: Dict[str, PartnerCutoff] = {}
        self.history: Dict[str, List[RollingSettlementState]] = {}
        self.history_by_week: Dict[str, List[RollingSettlementState]] = {}
        self.locked_weeks: Set[str] = set()
        self.pending_adjustments: Dict[str, Dict[str, float]] = {}  # week_id -> {partner_id: net_adj}

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
                    cutoff_date=row.get('cutoff_date', '2026-10-12'),
                    effective_week=self.cutoff_week_id,
                    opening_balance_due=float(row.get('opening_balance_due', 0.0)),
                    security_deposit_pending=float(row.get('security_deposit_pending', 0.0)),
                    cutoff_timestamp=row.get('cutoff_timestamp', '2026-10-12 12:00:00+05:30'),
                    remarks=row.get('remarks', 'Audited baseline cutoff')
                )
        print(f"Loaded {len(self.openings)} partner cutoffs for week {self.cutoff_week_id}.")

    def lock_week(self, week_id: str):
        """Enforces Monday 12:00 PM (Noon) IST cutoff lock. Week becomes completely immutable."""
        self.locked_weeks.add(week_id)
        print(f"Week {week_id} LOCKED at Monday 12:00 PM (Noon) IST cutoff threshold.")

    def route_adjustment(self, partner_id: str, origin_week_id: str, target_week_id: str, 
                         amount: float, polarity: str, adj_type: str = "CHALLAN"):
        """
        Routes an adjustment. If origin week is locked, routes to target open week.
        Polarity: 'DEBIT' increases dues (+amount), 'CREDIT' decreases dues (-amount).
        """
        signed_amount = amount if polarity.upper() == 'DEBIT' else -amount
        if origin_week_id in self.locked_weeks:
            print(f"Origin week {origin_week_id} is locked. Routing {adj_type} ({polarity} Rs {amount}) "
                  f"to next open week {target_week_id} as prior_period_adjustment.")
            effective_week = target_week_id
        else:
            effective_week = origin_week_id

        if effective_week not in self.pending_adjustments:
            self.pending_adjustments[effective_week] = {}
        self.pending_adjustments[effective_week][partner_id] = (
            self.pending_adjustments[effective_week].get(partner_id, 0.0) + signed_amount
        )

    def process_week(self, week_id: str, hisaab_data: List[WeeklyHisaabRecord], 
                     payments: Dict[str, float]) -> List[RollingSettlementState]:
        """
        Processes weekly settlement for all partners.
        If week is locked, skips mutation (immutable).
        Cumulatively rolls forward unpaid balances from previous weeks.
        """
        # Immutability Check:
        if week_id in self.locked_weeks:
            print(f"Notice: Week {week_id} is locked (Monday 12:00 PM IST cutoff). Week is completely immutable. Skipping sync.")
            return self.history_by_week.get(week_id, [])

        results = []

        for h in hisaab_data:
            pid = h.partner_id
            paid = float(payments.get(pid, 0.0))

            # Apply any routed prior period adjustments
            auto_adj = self.pending_adjustments.get(week_id, {}).get(pid, 0.0)
            net_prior_adj = round(h.prior_period_adjustments + auto_adj, 2)

            # Determine previous outstanding
            # Priority 1: opening_balance_due overrides previous_outstanding for week 'CY26WK41'
            if week_id == self.cutoff_week_id:
                prev_os = self.openings.get(pid, PartnerCutoff(pid, '', '', '', week_id, 0.0, 0.0)).opening_balance_due
            else:
                # Priority 2: For weeks > 'CY26WK41', previous_outstanding rolls forward from previous week (pw.total_outstanding)
                prev_weeks = [s for s in self.history.get(pid, []) if s.week_id < week_id]
                if prev_weeks:
                    prev_week_state = prev_weeks[-1]
                    prev_os = max(0.0, prev_week_state.total_outstanding)
                else:
                    prev_os = 0.0

            # Current period dues = vehicle operational OS + prior period adjustments
            current_period_os = round(h.current_week_os + net_prior_adj, 2)

            # Cumulative Total Outstanding Formula:
            # total_outstanding = max(0.0, (previous_outstanding + current_period_os) - amount_paid_during_week)
            gross_dues = prev_os + current_period_os
            net_balance = round(gross_dues - paid, 2)
            total_os = max(0.0, net_balance)

            to_collect = max(0.0, net_balance)
            to_pay = max(0.0, -net_balance)
            status = "settled" if to_collect == 0.0 and to_pay == 0.0 else ("to_collect" if to_collect > 0 else "to_pay")

            state = RollingSettlementState(
                week_id=week_id,
                partner_id=pid,
                previous_outstanding=prev_os,
                current_week_os=current_period_os,
                payment_received=paid,
                total_outstanding=total_os,
                to_collect=to_collect,
                to_pay=to_pay,
                status=status
            )

            if pid not in self.history:
                self.history[pid] = []
            
            # Update history replacing previous draft state for this week if present
            self.history[pid] = [s for s in self.history[pid] if s.week_id != week_id] + [state]
            results.append(state)

        self.history_by_week[week_id] = results
        return results
