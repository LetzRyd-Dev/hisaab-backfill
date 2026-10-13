# LetzRyd Hisaab Engine: Team Operating Guide
**Weekly Settlement, Monday Cutoff & Rolling Balance Rules**

---

### 1. What Changed?
Previously, past driver balances were tracked across separate Google Sheets and individual weekly tables. 

Starting **October 12, 2026**, the system runs on a **Cutoff-Based Cumulative Rolling Hisaab**. Every partner's balance rolls forward automatically from week to week in the database.

* **Cutoff Baseline Date:** Monday, October 12, 2026, 12:00 PM Noon IST (Week `CY26WK41`)
* **Verified Starting Dues:** **₹3,61,41,591.90** across **2,446 partners**
  * Hyderabad: 479 partners (₹1.30 Cr)
  * Bangalore: 1,698 partners (₹1.91 Cr)
  * Mumbai: 269 partners (₹40.4 Lakh)

---

### 2. The Weekly Cycle & Monday 12:00 PM Cutoff

Every settlement week follows a strict timetable:

```
Monday 00:00                     Sunday 23:59:59         Monday 12:00 PM Noon
     |                                  |                         |
     v                                  v                         v
+-----------------------------------------------+-----------------+----------------------->
|            TRIP & DRIVING WINDOW              |  GRACE WINDOW   | WEEK LOCKED PERMANENTLY
| Drivers complete trips, earn, collect cash    | Cash collection | New week opens.
|                                               | & weekend sync  | Statements become final.
+-----------------------------------------------+-----------------+----------------------->
```

1. **Operating Week (Monday to Sunday):** Drivers drive, earn on Uber/Ola, collect cash, and accrue vehicle rent.
2. **Grace Window (Sunday midnight to Monday 12:00 PM):** Operations and fleet managers have a 12-hour grace window to sync weekend cash collections and final trip logs.
3. **The Cutoff (Monday 12:00 PM Noon):**
   * The week status changes to **LOCKED**.
   * Hisaab statements and PDF slips generated for that week become **final and immutable**.
   * The new week opens immediately.

---

### 3. What Happens When Data Arrives Late?

**Rule:** Once a week is locked at Monday 12:00 PM, **it is never reopened or recalculated for routine data.**

If any variable belonging to the previous week arrives after Monday 12:00 PM (such as delayed Ola/Uber trip files, Sunday night incentives, traffic police e-challans, accident repairs, or manual adjustments):

* **The locked week stays 100% untouched.** The driver's historical statement does not change.
* **The amount flows automatically into the CURRENT OPEN WEEK as an adjustment.**
  * If the driver earned extra money on Ola/Uber $\rightarrow$ added as a **Credit** in the open week.
  * If a traffic challan or damage cost arrives $\rightarrow$ added as a **Debit** in the open week.
* In the driver's next hisaab statement, it shows clearly under **Prior Period Adjustments** with the reason and original date.

---

### 4. How the Balance Math Works

In the driver app and admin portal, the math is straightforward:

$$\text{Total Outstanding} = \text{Previous Balance} + \text{This Week's Rent} + \text{Prior Period Adjustments} - \text{Payments Made}$$

* **If Total Outstanding > 0:** Driver owes money to LetzRyd (Net to Collect).
* **If Total Outstanding < 0:** Platform earnings exceeded rent and debt; LetzRyd owes a payout to the driver's bank account (Net Bank Payout).
* **If a driver doesn't drive for a week:** Their previous balance simply carries forward unchanged.

---

### 5. Payments (UPI / Cashfree / Bank)

* **Before Monday 12:00 PM:** Payments credit toward the current week before the lock.
* **After Monday 12:00 PM:** Payments credit toward the new open week.
* **Real-time Balance Drop:** Regardless of timing, as soon as a payment webhook succeeds, the driver's live outstanding balance in the mobile app drops immediately.
* **Partial Payments:** If a driver owes ₹3,000 and pays ₹2,000, the remaining ₹1,000 stays as live debt and rolls into the next week at the Monday cutoff.

---

### 6. Emergency Unlock (Admin Only)

If finance or engineering must correct a critical error in a locked week, there is an administrative unlock option.

#### Using the Admin Script:
```bash
# Unlock a week for 2 hours (example for CY26WK41)
python 07_hisaab_cutoff_rolling_engine/admin_manage_lock.py unlock CY26WK41 --hours 2 --reason "Fixing Ola trip mismatch" --user "your_name"

# Re-lock immediately once changes are made
python 07_hisaab_cutoff_rolling_engine/admin_manage_lock.py lock CY26WK41 --reason "Done with corrections" --user "your_name"
```

#### Key Safeguards:
1. **Auto-Relock Timer:** You set the hours (e.g. 2 hours). If you forget to lock it back, the system locks it automatically when the timer runs out.
2. **Audit Trail:** Every single unlock and lock is logged with who did it, timestamp, and reason.

---

### 7. Quick Checklist for Ops & Support Teams

| Situation | What to Tell the Driver / What to Do |
| :--- | :--- |
| **Driver asks: "Why did my previous balance increase?"** | Check if an unpaid balance rolled over from last Monday's 12 PM cutoff, or if a late challan/adjustment was added. |
| **Ola/Uber sends delayed trip data on Tuesday.** | You do not need to edit last week. The system puts the net earnings/cash delta into this week's adjustment automatically. |
| **Driver makes a partial payment on Sunday night.** | It reflects immediately on their screen and reduces the rollover balance on Monday at 12 PM. |
| **Challan from 2 weeks ago is entered today.** | The system accepts it and attaches it to the current open hisaab statement. |
