import os
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

def set_cell_background(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    tcPr.append(shd)

def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(f'<w:tcMar {nsdecls("w")}><w:top w:w="{top}" w:type="dxa"/><w:bottom w:w="{bottom}" w:type="dxa"/><w:left w:w="{left}" w:type="dxa"/><w:right w:w="{right}" w:type="dxa"/></w:tcMar>')
    tcPr.append(tcMar)

def create_guide_docx(output_path):
    doc = docx.Document()

    # Set page margins (1 inch)
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    # Styles
    DARK_BLUE = RGBColor(24, 43, 73)
    SLATE_GRAY = RGBColor(90, 105, 120)
    BLACK = RGBColor(30, 30, 30)

    # Document Header / Title
    title_p = doc.add_paragraph()
    title_run = title_p.add_run("LetzRyd Hisaab Engine: Weekly Settlement & Rolling Outstanding Guide")
    title_run.font.name = "Calibri"
    title_run.font.size = Pt(22)
    title_run.font.bold = True
    title_run.font.color.rgb = DARK_BLUE
    title_p.paragraph_format.space_after = Pt(2)

    subtitle_p = doc.add_paragraph()
    sub_run = subtitle_p.add_run("Standard Operating Guide for Finance, Fleet Operations, and Support Teams")
    sub_run.font.name = "Calibri"
    sub_run.font.size = Pt(11)
    sub_run.font.italic = True
    sub_run.font.color.rgb = SLATE_GRAY
    subtitle_p.paragraph_format.space_after = Pt(14)

    # Divider Line
    div_p = doc.add_paragraph()
    div_run = div_p.add_run("―" * 58)
    div_run.font.color.rgb = RGBColor(200, 205, 215)
    div_p.paragraph_format.space_after = Pt(10)

    # Section 1: Overview
    h1 = doc.add_heading(level=1)
    r1 = h1.add_run("1. What Is This & Why Did We Build It?")
    r1.font.name = "Calibri"
    r1.font.color.rgb = DARK_BLUE

    p = doc.add_paragraph()
    p.add_run("Previously, driver outstanding dues were recorded across multiple offline Google Sheets and separate single-week database tables. This made it difficult for finance to track exact historical balances and often led to driver confusion about changing numbers.\n\n"
              "Starting Monday, October 12, 2026, the entire fleet runs on a Cutoff-Based Cumulative Rolling Hisaab. Every partner's balance rolls forward automatically in the database from one week to the next.")
    p.paragraph_format.space_after = Pt(8)

    # Key stats bullet box
    stats_p = doc.add_paragraph()
    stats_p.paragraph_format.left_indent = Inches(0.2)
    stats_p.add_run("• Cutoff Baseline Date: ").bold = True
    stats_p.add_run("Monday, October 12, 2026, at 12:00 PM Noon IST (Week CY26WK41)\n")
    stats_p.add_run("• Total Baseline Dues: ").bold = True
    stats_p.add_run("INR 3,61,41,591.90 across 2,446 partners\n")
    stats_p.add_run("• City Breakdown: ").bold = True
    stats_p.add_run("Hyderabad: 479 partners (INR 1.30 Cr) | Bangalore: 1,698 partners (INR 1.91 Cr) | Mumbai: 269 partners (INR 40.4 Lakh)")
    stats_p.paragraph_format.space_after = Pt(14)

    # Section 2: Cutoff Timeline
    h2 = doc.add_heading(level=1)
    r2 = h2.add_run("2. The Weekly Settlement Cycle & Monday 12:00 PM Cutoff")
    r2.font.name = "Calibri"
    r2.font.color.rgb = DARK_BLUE

    doc.add_paragraph(
        "Every settlement week follows a strict 3-stage timeline:\n\n"
        "1. Operating Window (Monday 00:00 to Sunday 23:59:59):\n"
        "   Drivers complete trips on Ola/Uber, collect platform cash, and accrue weekly vehicle lease rent.\n\n"
        "2. Grace Window (Sunday midnight to Monday 12:00 PM Noon IST):\n"
        "   A 12-hour grace period allows operations teams and automated cron jobs to sync Sunday night trips, UPI payments, and cash collections.\n\n"
        "3. The Lock Moment (Monday 12:00 PM Noon IST):\n"
        "   The week is marked LOCKED. Once locked, the hisaab statement for that week is permanently frozen. The driver's PDF hisaab slip is finalized and can never be changed retroactively."
    )

    # Section 3: Database Tables & Variable Changes
    h3 = doc.add_heading(level=1)
    r3 = h3.add_run("3. Database Tables: Exactly What Changes")
    r3.font.name = "Calibri"
    r3.font.color.rgb = DARK_BLUE

    doc.add_paragraph(
        "Here is the exact breakdown of how the database handles the data and which variables change:"
    )

    # Table of DB variables
    table = doc.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    # Header Row
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "Table Name"
    hdr_cells[1].text = "Key Variables / Columns That Change"
    hdr_cells[2].text = "How It Works in Business Terms"
    hdr_cells[0].width = Inches(1.8)
    hdr_cells[1].width = Inches(2.3)
    hdr_cells[2].width = Inches(2.7)

    for cell in hdr_cells:
        set_cell_background(cell, "182B49")
        set_cell_margins(cell, 120, 120, 150, 150)
        for cp in cell.paragraphs:
            for cr in cp.runs:
                cr.font.name = "Calibri"
                cr.font.bold = True
                cr.font.color.rgb = RGBColor(255, 255, 255)
                cr.font.size = Pt(9.5)

    data = [
        (
            "public.hisaab_partner_opening_balances",
            "partner_id\neffective_week_id\nopening_balance_due\nremarks",
            "Permanent Day-Zero baseline. Stamped once on Oct 12 at 12:00 PM. Stores the audited starting balance (B_0) for each of the 2,446 partners. Never mutated."
        ),
        (
            "public.hisaab_settlement_weeks",
            "is_locked (BOOLEAN)\nlock_cutoff_at (TIMESTAMPTZ)\nnotes (TEXT)",
            "Governs the status of each week. Turns from FALSE to TRUE at Monday 12:00 PM Noon. When TRUE, recalculation is blocked."
        ),
        (
            "public.hisaab_partner_weekly",
            "previous_outstanding\ncurrent_week_os\nprior_period_adjustments\namount_paid_during_week\ntotal_outstanding\nsettlement_status",
            "The live weekly hisaab record per partner.\n• previous_outstanding: Carried over from last week's lock.\n• current_week_os: Net rent, cash, and earnings this week.\n• prior_period_adjustments: Any late items from past weeks.\n• total_outstanding: Final net balance to collect or pay.\n• settlement_status: 'OPEN' -> 'LOCKED' at cutoff."
        ),
        (
            "public.hisaab_adjustments_ledger",
            "settlement_week_id\nadjustment_category\namount\npolarity ('DEBIT'/'CREDIT')\nis_prior_period (BOOLEAN)",
            "Holds late variables (late Ola/Uber trips, challans, accidents). If inserted post-cutoff, the database trigger automatically redirects settlement_week_id to the NEXT OPEN WEEK and sets is_prior_period = TRUE."
        ),
        (
            "Core Tables:\napp_hisaabs\napp_drivers\nvehicles\napp_payments",
            "ZERO CHANGES\n(100% Preserved)",
            "Legacy production tables remain completely untouched. Driver profiles, vehicle allocations, and raw payment webhooks are never modified or dropped by the hisaab engine."
        )
    ]

    for row_idx, (tname, cols, desc) in enumerate(data):
        row = table.add_row()
        cells = row.cells
        cells[0].text = tname
        cells[1].text = cols
        cells[2].text = desc
        cells[0].width = Inches(1.8)
        cells[1].width = Inches(2.3)
        cells[2].width = Inches(2.7)

        bg = "F4F6F9" if row_idx % 2 == 1 else "FFFFFF"
        for c in cells:
            set_cell_background(c, bg)
            set_cell_margins(c, 100, 100, 120, 120)
            for cp in c.paragraphs:
                for cr in cp.runs:
                    cr.font.name = "Calibri"
                    cr.font.size = Pt(8.5)
                    cr.font.color.rgb = BLACK

    doc.add_paragraph().paragraph_format.space_after = Pt(10)

    # Section 4: Practical Step-by-Step Example
    h4 = doc.add_heading(level=1)
    r4 = h4.add_run("4. Real-World Driver Example: How It Works Step-by-Step")
    r4.font.name = "Calibri"
    r4.font.color.rgb = DARK_BLUE

    doc.add_paragraph(
        "Let's follow a real partner (e.g. Driver Ramesh, Partner ID: HYD102) over two consecutive settlement weeks to see the exact numbers and variable changes."
    )

    # Step A: Week 1
    doc.add_heading(level=2, text="Week 1 (Oct 5 to Oct 11) - Regular Settlement")
    doc.add_paragraph(
        "1. Opening Baseline Balance: Ramesh starts with an audited pending balance of INR 2,000.00.\n"
        "2. Ramesh Drives During Week 1:\n"
        "   • Vehicle Rent Billed: INR 5,000.00\n"
        "   • Uber/Ola Platform Earnings: INR 4,000.00\n"
        "   • Cash Collected from Passengers: INR 1,000.00\n"
        "   • Week 1 Operating OS = (Rent + Cash - Earnings) = 5,000 + 1,000 - 4,000 = INR 2,000.00.\n"
        "3. Total Due for Week 1:\n"
        "   • Total Due = Previous Balance (2,000) + Week 1 OS (2,000) = INR 4,000.00.\n"
        "4. Ramesh Makes a Payment: On Sunday night, Ramesh pays INR 2,500.00 via UPI.\n"
        "   • Remaining Outstanding = 4,000.00 - 2,500.00 = INR 1,500.00.\n"
        "5. Monday 12:00 PM Noon Cutoff: Week 1 is locked. Ramesh's Week 1 hisaab slip is finalized with an ending balance of INR 1,500.00."
    )

    # Step B: Late Data Arrives
    doc.add_heading(level=2, text="Tuesday Afternoon - Late Data Arrives (Post-Cutoff)")
    doc.add_paragraph(
        "On Tuesday at 3:00 PM (after Week 1 is already locked), two late items arrive from external portals:\n"
        "   • Item 1 (Ola Delayed Trips): Ola sends delayed Sunday night trips showing Ramesh earned an extra INR 800.00 on platform.\n"
        "   • Item 2 (Traffic Challan): Traffic police logs an over-speeding e-challan of INR 1,000.00 that occurred on October 7.\n\n"
        "What happens in the system?\n"
        "• Week 1 remains 100% LOCKED and unchanged. Ramesh's Week 1 receipt is not touched.\n"
        "• The database automatically routes both items to the active open week (Week 2) as Prior Period Adjustments:\n"
        "   • Late Ola Earnings: -INR 800.00 (Credit to driver)\n"
        "   • Late Challan: +INR 1,000.00 (Debit to driver)\n"
        "   • Net Prior Period Adjustment for Week 2 = +INR 200.00."
    )

    # Step C: Week 2
    doc.add_heading(level=2, text="Week 2 (Oct 12 to Oct 18) - The Roll-Forward Settlement")
    doc.add_paragraph(
        "When Week 2 hisaab is generated for Ramesh, the numbers assemble seamlessly:\n\n"
        "   • Previous Outstanding = INR 1,500.00 (carried over directly from Week 1 lock)\n"
        "   • Prior Period Adjustments = +INR 200.00 (net of late challan and late Ola earnings)\n"
        "   • Week 2 Operating OS = Rent (INR 5,000) + Cash (INR 500) - Earnings (INR 4,500) = INR 1,000.00\n"
        "   • Total Due in Week 2 = 1,500.00 (previous) + 1,000.00 (this week rent) + 200.00 (late adjustments) = INR 2,700.00.\n\n"
        "On Thursday, Ramesh opens the app, sees his Total Due is INR 2,700.00, taps 'Pay', and clears INR 2,700.00 via UPI. His live balance immediately updates to INR 0.00."
    )

    # Section 5: Temporary Administrative Unlock
    h5 = doc.add_heading(level=1)
    r5 = h5.add_run("5. How to Temporarily Unlock a Week (Admin Emergency Only)")
    r5.font.name = "Calibri"
    r5.font.color.rgb = DARK_BLUE

    doc.add_paragraph(
        "If finance or engineering discovers an urgent accounting error in a locked week and needs to adjust it directly, an administrative unlock option is available:\n\n"
        "Command Line Tool (Recommended):\n"
        "   # Unlock week CY26WK41 for 2 hours\n"
        "   python 07_hisaab_cutoff_rolling_engine/admin_manage_lock.py unlock CY26WK41 --hours 2 --reason \"Trip sync correction\" --user \"admin_name\"\n\n"
        "   # Lock back immediately when done\n"
        "   python 07_hisaab_cutoff_rolling_engine/admin_manage_lock.py lock CY26WK41 --reason \"Corrections verified\" --user \"admin_name\"\n\n"
        "Safety Built-In:\n"
        "• Auto-Relock: If the user forgets to lock it back, the system automatically locks the week when the specified hours expire.\n"
        "• Permanent Audit Trail: Every unlock and lock is recorded in public.hisaab_cutoff_audit_log with the user's name, timestamp, and reason."
    )

    # Section 6: App & Driver Impact Summary
    h6 = doc.add_heading(level=1)
    r6 = h6.add_run("6. Mobile App & Operational Impact")
    r6.font.name = "Calibri"
    r6.font.color.rgb = DARK_BLUE

    doc.add_paragraph(
        "• Zero App Bugs or Breakage: All mobile and web APIs continue using the existing schema without breaking.\n"
        "• Instant Payment Reflection: Payments made through UPI/Cashfree instantly reduce the driver's live outstanding balance on the home screen.\n"
        "• Transparent Slips: Past receipts never change. Drivers can clearly see why their balance rolled forward, eliminating disputes and customer support complaints."
    )

    # Save document
    doc.save(output_path)
    print(f"Document successfully created and saved to: {output_path}")

if __name__ == '__main__':
    target = r'C:\Users\anura\Downloads\LetzRyd_Hisaab_Settlement_and_Outstanding_Guide.docx'
    create_guide_docx(target)
