"""
Property Inspection PDF Generator — Halstead Holdings
Deploy on Railway. Make.com calls POST /generate-pdf with JSON from Airtable.

Requirements:
  pip install flask reportlab requests resend pillow dropbox

Environment variables:
  RESEND_API_KEY         - your Resend API key
  FROM_EMAIL             - verified sender email
  DROPBOX_ACCESS_TOKEN   - Dropbox app access token (for archiving PDFs)
"""

import io
import os
import requests
import tempfile
import resend
import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError, AuthError
from flask import Flask, request, jsonify
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable, PageBreak, KeepTogether
)
from collections import defaultdict
import base64

app = Flask(__name__)

# ── Recipients ────────────────────────────────────────────────────────────────
REPORT_RECIPIENTS = [
    "Mark@Halsteadholdings.com",
    "Mike@Halsteadholdings.com",
    "Roxanne@Halsteadholdings.com",
]

# ── Halstead Holdings Brand Colors ───────────────────────────────────────────
BLACK       = colors.HexColor("#1A1A1A")
ORANGE      = colors.HexColor("#C8570A")
ORANGE_LIGHT= colors.HexColor("#F0A040")
OFF_BLACK   = colors.HexColor("#2C2C2C")
OFF_WHITE   = colors.HexColor("#F7F5F0")
MID_GRAY    = colors.HexColor("#777777")
LIGHT_GRAY  = colors.HexColor("#EEEEEE")

COMPANY_NAME = "Halstead Holdings"
COMPANY_LOGO = "logo.png"

# ── Status colors ─────────────────────────────────────────────────────────────
STATUS_COLORS = {
    "Repair":   colors.HexColor("#FDE8C8"),
    "Replace":  colors.HexColor("#F8D7DA"),
    "Cleaning": colors.HexColor("#D6EAF8"),
    "Good":     colors.HexColor("#D5F5E3"),
    "N/A":      colors.HexColor("#F2F3F4"),
}

SUMMARY_STATUSES = ["Repair", "Replace", "Cleaning", "Good"]

TURN_LEVEL_COLORS = {
    "Light":  colors.HexColor("#D5F5E3"),
    "Medium": colors.HexColor("#FDE8C8"),
    "Heavy":  colors.HexColor("#F8D7DA"),
}

TURN_LEVEL_DESCRIPTIONS = {
    "Light":  "Minor cleaning and touch-ups only. Unit is in good overall condition.",
    "Medium": "Moderate work required. Some repairs and cleaning needed before re-leasing.",
    "Heavy":  "Significant work required. Major repairs, replacements, and deep cleaning needed.",
}


def download_image(url: str):
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        suffix = ".jpg" if "jpg" in url.lower() else ".png"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(resp.content)
        tmp.close()
        return tmp.name
    except Exception as e:
        print(f"Failed to download image {url}: {e}")
        return None


def make_style(name, parent, **kwargs):
    return ParagraphStyle(name, parent=parent, **kwargs)


def sanitize_folder_name(name: str) -> str:
    """Make a string safe to use as a Dropbox folder or file name."""
    if not name:
        return ""
    name = str(name).strip()
    # Replace path separators (Dropbox uses / as path delimiter)
    name = name.replace("/", "-").replace("\\", "-")
    # Strip other problematic characters
    for ch in [":", "*", "?", '"', "<", ">", "|"]:
        name = name.replace(ch, "")
    # Collapse multiple spaces
    while "  " in name:
        name = name.replace("  ", " ")
    return name.strip()


def upload_to_dropbox(pdf_bytes: bytes, property_name: str, unit: str,
                      inspection_type: str, short_date: str) -> str:
    """
    Upload PDF to Dropbox in a Property → Unit folder structure.
    Returns the Dropbox path on success, or None on failure.
    Never raises — failures are logged but don't break the pipeline.
    """
    token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    if not token:
        print("DROPBOX_ACCESS_TOKEN not set — skipping Dropbox upload")
        return None

    try:
        # Sanitize all path components
        property_clean = sanitize_folder_name(property_name) or "Unknown Property"
        unit_clean = sanitize_folder_name(unit) or "No Unit"
        type_clean = sanitize_folder_name(inspection_type) or "Inspection"
        date_clean = sanitize_folder_name(short_date) or "Unknown Date"

        # Convert date from MM-DD-YYYY to YYYY-MM-DD for chronological sorting
        date_for_filename = date_clean
        if len(date_clean) == 10 and date_clean[2] == "-" and date_clean[5] == "-":
            try:
                mm, dd, yyyy = date_clean.split("-")
                date_for_filename = f"{yyyy}-{mm}-{dd}"
            except Exception:
                pass

        filename = f"{date_for_filename}_{type_clean}.pdf"
        # Full Dropbox access — write directly into the team's shared corporate folder.
        path = f"/Halstead Holdings Corporate Documents/Unit Inspections/{property_clean}/Unit {unit_clean}/{filename}"

        dbx = dropbox.Dropbox(token)
        result = dbx.files_upload(
            pdf_bytes,
            path,
            mode=WriteMode("add"),   # never overwrite existing files
            autorename=True,         # auto-add (1), (2) on collision
        )
        print(f"Dropbox upload successful: {result.path_display}")
        return result.path_display

    except AuthError as e:
        print(f"Dropbox auth error (token may be invalid or expired): {e}")
        return None
    except ApiError as e:
        print(f"Dropbox API error: {e}")
        return None
    except Exception as e:
        print(f"Dropbox upload failed: {e}")
        return None


def build_pdf(data: dict) -> bytes:
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
    )

    styles = getSampleStyleSheet()
    N = styles["Normal"]

    raw_date = data.get("inspection_date", "—")
    if raw_date and len(raw_date) >= 10:
        try:
            parts = raw_date[:10].split("-")
            short_date = f"{parts[1]}-{parts[2]}-{parts[0]}"
        except:
            short_date = raw_date[:10]
    else:
        short_date = raw_date
    property_name = data.get("property", "—")
    unit = data.get("unit", "—")
    inspection_type = data.get("inspection_type", "—")
    inspector = data.get("inspector", "—")
    notes = data.get("notes", "")
    turn_level = data.get("turn_level", "") or data.get("Turn Level", "") or ""

    # ── Unit-level spec fields (captured in inspection app) ──
    unit_specs = {
        "window_inventory": data.get("window_inventory") or data.get("Window Inventory") or "",
        "outlet_count":     data.get("outlet_count")     or data.get("Outlet Count")     or "",
        "switch_count":     data.get("switch_count")     or data.get("Switch Count")     or "",
        "plate_count":      data.get("plate_count")      or data.get("Plate Count")      or "",
        "tub_drain_sides":  data.get("tub_drain_sides")  or data.get("Tub Drain Sides")  or "",
        "oven_fuel":        data.get("oven_fuel")        or data.get("Oven Fuel")        or "",
        "oven_model":       data.get("oven_model")       or data.get("Oven Model")       or "",
        "fridge_model":     data.get("fridge_model")     or data.get("Fridge Model")     or "",
        "wh_year":          data.get("wh_year")          or data.get("Water Heater Year") or "",
        "wh_capacity":      data.get("wh_capacity")      or data.get("Water Heater Capacity") or "",
        "furnace_year":     data.get("furnace_year")     or data.get("Furnace Year")     or "",
        "furnace_filter":   data.get("furnace_filter")   or data.get("Furnace Filter Size") or "",
    }

    def spec_for(area, component):
        """Return small inline spec string for a condition row, or empty string."""
        parts = []
        if component == "Oven":
            if unit_specs["oven_fuel"]: parts.append(str(unit_specs["oven_fuel"]))
            if unit_specs["oven_model"]: parts.append(f"Model: {unit_specs['oven_model']}")
        elif component == "Refrigerator":
            if unit_specs["fridge_model"]: parts.append(f"Model: {unit_specs['fridge_model']}")
        elif component == "Water Heater":
            if unit_specs["wh_capacity"]: parts.append(f"{unit_specs['wh_capacity']} gal")
            if unit_specs["wh_year"]: parts.append(f"Installed {unit_specs['wh_year']}")
        elif component == "Furnace/HVAC":
            if unit_specs["furnace_year"]: parts.append(f"Installed {unit_specs['furnace_year']}")
            if unit_specs["furnace_filter"]: parts.append(f"Filter: {unit_specs['furnace_filter']}")
        elif component == "Electrical Outlets":
            counts = []
            if unit_specs["outlet_count"]: counts.append(f"{unit_specs['outlet_count']} outlets")
            if unit_specs["switch_count"]: counts.append(f"{unit_specs['switch_count']} switches")
            if unit_specs["plate_count"]: counts.append(f"{unit_specs['plate_count']} plates")
            if counts: parts.append(" / ".join(counts))
        elif component == "Shower/Tub" and unit_specs["tub_drain_sides"]:
            for chunk in str(unit_specs["tub_drain_sides"]).split("/"):
                if area.strip() in chunk:
                    side = chunk.split(":")[-1].strip()
                    if side: parts.append(f"Drain: {side}")
                    break
        return " · ".join(parts)

    story = []

    # ── HEADER BANNER ─────────────────────────────────────────────────────
    logo_cell = Paragraph("", N)
    if os.path.exists(COMPANY_LOGO):
        logo_img = Image(COMPANY_LOGO, width=2.8 * inch, height=0.78 * inch)
        logo_img.hAlign = "LEFT"
        logo_cell = logo_img

    title_cell = [
        Paragraph("Property Inspection Report",
            make_style("T1", N, fontSize=19, textColor=colors.white, fontName="Helvetica-Bold", leading=24)),
    ]

    banner = Table([[logo_cell, title_cell]], colWidths=[3.1*inch, 4.05*inch])
    banner.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), BLACK),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("LEFTPADDING",   (0,0),(0,0),   14),
        ("LEFTPADDING",   (1,0),(1,0),   18),
        ("TOPPADDING",    (0,0),(-1,-1), 18),
        ("BOTTOMPADDING", (0,0),(-1,-1), 18),
    ]))
    story.append(banner)
    story.append(HRFlowable(width="100%", thickness=3, color=ORANGE, spaceAfter=10, spaceBefore=0))

    # ── PROPERTY INFO ─────────────────────────────────────────────────────
    def info_cell(label, value):
        return [
            Paragraph(label.upper(), make_style("IL", N, fontSize=7, textColor=ORANGE_LIGHT, fontName="Helvetica-Bold", leading=11)),
            Paragraph(str(value), make_style("IV", N, fontSize=10, textColor=colors.white, fontName="Helvetica", leading=14)),
        ]

    row1 = [info_cell("Property", property_name), info_cell("Unit", unit), info_cell("Inspection Type", inspection_type)]
    row2 = [info_cell("Inspector", inspector), info_cell("Date", short_date), info_cell("Turn Level", turn_level or "—")]

    for row in [row1, row2]:
        t = Table([row], colWidths=[2.38*inch, 2.38*inch, 2.38*inch])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), OFF_BLACK),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("TOPPADDING",    (0,0),(-1,-1), 9),
            ("BOTTOMPADDING", (0,0),(-1,-1), 9),
            ("LEFTPADDING",   (0,0),(-1,-1), 12),
            ("RIGHTPADDING",  (0,0),(-1,-1), 12),
            ("LINEAFTER",     (0,0),(1,0),   0.5, colors.HexColor("#3A3A3A")),
        ]))
        story.append(t)
        story.append(Spacer(1, 2))

    story.append(Spacer(1, 14))

    # ── SUMMARY PAGE ──────────────────────────────────────────────────────
    story.append(Paragraph("Inspection Summary",
        make_style("SH", N, fontSize=12, textColor=BLACK, fontName="Helvetica-Bold",
                   spaceBefore=0, spaceAfter=4, leading=16)))
    story.append(HRFlowable(width="100%", thickness=1.5, color=ORANGE, spaceAfter=10, spaceBefore=0))

    # Conditions
    conditions = data.get("conditions", [])
    if isinstance(conditions, dict):
        conditions = conditions.get("array", [conditions])

    counts = {s: 0 for s in SUMMARY_STATUSES}
    for c in conditions:
        sev = c.get("Status") or c.get("status") or ""
        if sev in counts:
            counts[sev] += 1

    total_issues = counts["Repair"] + counts["Replace"] + counts["Cleaning"]

    story.append(Paragraph(
        f"This inspection identified <b>{total_issues} item(s) requiring attention</b> "
        f"across <b>{len(conditions)}</b> total observations.",
        make_style("BI", N, fontSize=10, textColor=BLACK, fontName="Helvetica", leading=14, spaceAfter=10)
    ))

    # Turn level callout box
    if turn_level and turn_level in TURN_LEVEL_COLORS:
        tl_bg = TURN_LEVEL_COLORS[turn_level]
        tl_desc = TURN_LEVEL_DESCRIPTIONS.get(turn_level, "")
        tl_table = Table([
            [
                Paragraph("Turn Level", make_style("TLL", N, fontSize=8, textColor=OFF_BLACK, fontName="Helvetica-Bold")),
                Paragraph(turn_level, make_style("TLV", N, fontSize=14, textColor=OFF_BLACK, fontName="Helvetica-Bold")),
                Paragraph(tl_desc, make_style("TLD", N, fontSize=9, textColor=OFF_BLACK, fontName="Helvetica", leading=13)),
            ]
        ], colWidths=[1.0*inch, 1.2*inch, 4.95*inch])
        tl_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), tl_bg),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("TOPPADDING",    (0,0),(-1,-1), 10),
            ("BOTTOMPADDING", (0,0),(-1,-1), 10),
            ("LEFTPADDING",   (0,0),(-1,-1), 12),
            ("BOX",           (0,0),(-1,-1), 0.5, colors.HexColor("#BBBBBB")),
        ]))
        story.append(tl_table)
        story.append(Spacer(1, 10))

    # Window inventory box (unit-wide)
    if unit_specs["window_inventory"]:
        win_table = Table([
            [
                Paragraph("Window Inventory", make_style("WL", N, fontSize=8, textColor=ORANGE, fontName="Helvetica-Bold")),
                Paragraph(str(unit_specs["window_inventory"]), make_style("WV", N, fontSize=10, textColor=BLACK, fontName="Helvetica", leading=14)),
            ]
        ], colWidths=[1.3*inch, 5.85*inch])
        win_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), OFF_WHITE),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("TOPPADDING",    (0,0),(-1,-1), 10),
            ("BOTTOMPADDING", (0,0),(-1,-1), 10),
            ("LEFTPADDING",   (0,0),(-1,-1), 12),
            ("LINEBEFORE",    (0,0),(0,-1),  3, ORANGE),
            ("BOX",           (0,0),(-1,-1), 0.3, colors.HexColor("#DDDDDD")),
        ]))
        story.append(win_table)
        story.append(Spacer(1, 10))

    # Inspector notes box
    if notes:
        notes_table = Table([
            [
                Paragraph("Inspector Notes", make_style("NL", N, fontSize=8, textColor=ORANGE, fontName="Helvetica-Bold")),
                Paragraph(notes, make_style("NV", N, fontSize=10, textColor=BLACK, fontName="Helvetica", leading=14)),
            ]
        ], colWidths=[1.3*inch, 5.85*inch])
        notes_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), OFF_WHITE),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("TOPPADDING",    (0,0),(-1,-1), 10),
            ("BOTTOMPADDING", (0,0),(-1,-1), 10),
            ("LEFTPADDING",   (0,0),(-1,-1), 12),
            ("LINEBEFORE",    (0,0),(0,-1),  3, ORANGE),
            ("BOX",           (0,0),(-1,-1), 0.3, colors.HexColor("#DDDDDD")),
        ]))
        story.append(notes_table)
        story.append(Spacer(1, 10))

    # Status summary table
    sum_header = [
        Paragraph("Status", make_style("SHH", N, fontSize=9, textColor=colors.white, fontName="Helvetica-Bold")),
        Paragraph("Count",  make_style("SHH2", N, fontSize=9, textColor=colors.white, fontName="Helvetica-Bold")),
        Paragraph("Description", make_style("SHH3", N, fontSize=9, textColor=colors.white, fontName="Helvetica-Bold")),
    ]
    status_descriptions = {
        "Repair":   "Items requiring physical repair",
        "Replace":  "Items requiring full replacement",
        "Cleaning": "Items requiring cleaning or touch-up",
        "Good":     "Items in good condition — no action needed",
    }
    sum_rows = [sum_header]
    for s in SUMMARY_STATUSES:
        sum_rows.append([
            Paragraph(s, make_style(f"SR{s}", N, fontSize=10, textColor=BLACK, fontName="Helvetica-Bold")),
            Paragraph(str(counts[s]), make_style(f"SC{s}", N, fontSize=10, textColor=BLACK, fontName="Helvetica-Bold")),
            Paragraph(status_descriptions[s], make_style(f"SD{s}", N, fontSize=10, textColor=BLACK, fontName="Helvetica")),
        ])

    sum_table = Table(sum_rows, colWidths=[1.4*inch, 0.9*inch, 4.95*inch])
    sum_ts = [
        ("BACKGROUND",    (0,0),(-1,0),  BLACK),
        ("TOPPADDING",    (0,0),(-1,-1), 7),
        ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
        ("GRID",          (0,0),(-1,-1), 0.3, colors.HexColor("#DDDDDD")),
    ]
    for i, s in enumerate(SUMMARY_STATUSES, start=1):
        sum_ts.append(("BACKGROUND", (0,i),(-1,i), STATUS_COLORS.get(s, LIGHT_GRAY)))
    sum_table.setStyle(TableStyle(sum_ts))
    story.append(sum_table)
    story.append(Spacer(1, 16))

    # ── DETAILED FINDINGS ─────────────────────────────────────────────────
    story.append(Paragraph("Inspection Findings",
        make_style("FH", N, fontSize=12, textColor=BLACK, fontName="Helvetica-Bold",
                   spaceBefore=0, spaceAfter=4, leading=16)))
    story.append(HRFlowable(width="100%", thickness=1.5, color=ORANGE, spaceAfter=10, spaceBefore=0))

    areas = defaultdict(list)
    for condition in conditions:
        area = condition.get("Area") or condition.get("area") or "General"
        areas[area].append(condition)

    for area_name, area_conditions in areas.items():
        area_header = Table(
            [[Paragraph(area_name.upper(),
                make_style("AH", N, fontSize=10, textColor=colors.white, fontName="Helvetica-Bold"))]],
            colWidths=[7.15*inch]
        )
        area_header.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), OFF_BLACK),
            ("TOPPADDING",    (0,0),(-1,-1), 6),
            ("BOTTOMPADDING", (0,0),(-1,-1), 6),
            ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ("LINEBEFORE",    (0,0),(0,-1),  3, ORANGE),
        ]))

        col_header = [
            Paragraph("Component", make_style("CH", N, fontSize=8, textColor=MID_GRAY, fontName="Helvetica-Bold")),
            Paragraph("Status",    make_style("CH2", N, fontSize=8, textColor=MID_GRAY, fontName="Helvetica-Bold")),
            Paragraph("Notes",     make_style("CH3", N, fontSize=8, textColor=MID_GRAY, fontName="Helvetica-Bold")),
        ]
        table_data = [col_header]
        row_bgs = []
        # Track which rows are photo rows (spanning all 3 columns)
        photo_row_indices = []

        for condition in area_conditions:
            component = condition.get("Component") or condition.get("component") or "—"
            status    = condition.get("Status") or condition.get("status") or ""
            notes_c   = condition.get("Notes") or condition.get("notes") or ""
            raw_photos = condition.get("Photo") or condition.get("photo") or condition.get("Photos") or condition.get("photos") or []

            # Skip blank and N/A rows
            if not status or status == "N/A":
                continue

            bg = STATUS_COLORS.get(status, LIGHT_GRAY)

            # Build component cell — add spec info as small italic text below name if applicable
            spec_text = spec_for(area_name, component)
            if spec_text:
                component_html = f"{component}<br/><font size=8 color='#777777'><i>{spec_text}</i></font>"
            else:
                component_html = component

            table_data.append([
                Paragraph(component_html, make_style("CR", N, fontSize=10, textColor=BLACK, fontName="Helvetica", leading=13)),
                Paragraph(status or "—", make_style("SR2", N, fontSize=9, textColor=BLACK, fontName="Helvetica-Bold")),
                Paragraph(str(notes_c) if notes_c else "—", make_style("NR", N, fontSize=9, textColor=MID_GRAY, fontName="Helvetica")),
            ])
            row_bgs.append(bg)

            # ── Parse photo URLs from Airtable attachment field ──
            photo_urls = []
            if isinstance(raw_photos, list):
                # Airtable native attachment: [{"url": "...", ...}, ...]
                for item in raw_photos:
                    if isinstance(item, dict):
                        url = item.get("url") or item.get("URL") or ""
                        if url and url.startswith("http"):
                            photo_urls.append(url)
                    elif isinstance(item, str) and item.startswith("http"):
                        photo_urls.append(item)
            elif isinstance(raw_photos, str) and raw_photos.startswith("http"):
                # Single URL string fallback
                photo_urls.append(raw_photos)

            # ── Download and embed photos inline below this row ──
            if photo_urls:
                photo_images = []
                for url in photo_urls:
                    tmp_path = download_image(url)
                    if tmp_path:
                        try:
                            img = Image(tmp_path, width=3.3*inch, height=2.5*inch)
                            img.hAlign = "LEFT"
                            photo_images.append(img)
                        except Exception as e:
                            print(f"Could not embed image: {e}")

                if photo_images:
                    # Arrange photos in rows of 2
                    photo_grid_rows = []
                    for i in range(0, len(photo_images), 2):
                        row_imgs = photo_images[i:i+2]
                        # Pad row to 2 columns
                        while len(row_imgs) < 2:
                            row_imgs.append(Paragraph("", N))
                        photo_grid_rows.append(row_imgs)

                    photo_grid = Table(photo_grid_rows, colWidths=[3.57*inch, 3.57*inch])
                    photo_grid.setStyle(TableStyle([
                        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
                        ("TOPPADDING",    (0,0),(-1,-1), 4),
                        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
                        ("LEFTPADDING",   (0,0),(-1,-1), 4),
                        ("RIGHTPADDING",  (0,0),(-1,-1), 4),
                    ]))

                    caption = Paragraph(
                        f"{component} — {len(photo_images)} {'photo' if len(photo_images) == 1 else 'photos'}",
                        make_style("PC", N, fontSize=8, textColor=MID_GRAY, fontName="Helvetica-Oblique"))

                    photo_cell_content = [photo_grid, Spacer(1, 2), caption]

                    # Add as a spanning row in the main table
                    table_data.append([photo_cell_content, "", ""])
                    photo_row_indices.append(len(table_data) - 1)
                    row_bgs.append(OFF_WHITE)

        # Skip this area entirely if no valid rows were added
        valid_data_rows = [i for i in range(1, len(table_data)) if i not in photo_row_indices]
        if not valid_data_rows:
            continue

        area_table = Table(table_data, colWidths=[2.0*inch, 1.2*inch, 3.95*inch])

        ts = [
            ("TOPPADDING",    (0,0),(-1,-1), 6),
            ("BOTTOMPADDING", (0,0),(-1,-1), 6),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("GRID",          (0,0),(-1,-1), 0.3, colors.HexColor("#DDDDDD")),
            ("BACKGROUND",    (0,0),(-1,0),  OFF_WHITE),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
        ]
        for i, bg in enumerate(row_bgs, start=1):
            ts.append(("BACKGROUND", (0,i),(-1,i), bg))
        # Span photo rows across all 3 columns
        for pr in photo_row_indices:
            ts.append(("SPAN",          (0,pr),(-1,pr)))
            ts.append(("TOPPADDING",    (0,pr),(-1,pr), 8))
            ts.append(("BOTTOMPADDING", (0,pr),(-1,pr), 8))
            ts.append(("LEFTPADDING",   (0,pr),(-1,pr), 12))
        area_table.setStyle(TableStyle(ts))

        block = [area_header, area_table, Spacer(1, 12)]
        story.append(KeepTogether(block))

    # ── FOOTER ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=ORANGE, spaceBefore=12, spaceAfter=6))
    story.append(Paragraph(
        f"Report prepared by <b>{inspector}</b> on <b>{short_date}</b>. "
        "All findings are based on visual inspection only and do not constitute a warranty. "
        f"© {COMPANY_NAME}",
        make_style("FT", N, fontSize=8, textColor=MID_GRAY, fontName="Helvetica", leading=12)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def send_email_with_pdf(property_address: str, unit: str, inspection_type: str, date: str, pdf_bytes: bytes):
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("FROM_EMAIL", "reports@halsteadholdings.com")

    if not api_key:
        print("RESEND_API_KEY not set — skipping email")
        return

    resend.api_key = api_key
    filename = f"Inspection_{property_address}_{unit}_{inspection_type}_{date}.pdf".replace(" ", "_")
    encoded = base64.b64encode(pdf_bytes).decode()

    try:
        resend.Emails.send({
            "from": from_email,
            "to": REPORT_RECIPIENTS,
            "subject": f"Inspection Report — {property_address} Unit {unit} ({inspection_type}) {date}",
            "html": f"""
            <div style="font-family:sans-serif;color:#1A1A1A;max-width:600px;">
                <div style="background:#1A1A1A;padding:24px 28px;">
                    <h2 style="color:#F0A040;margin:0;font-size:20px;">Property Inspection Report</h2>
                    <p style="color:#ffffff;margin:4px 0 0;font-size:13px;">Halstead Holdings</p>
                </div>
                <div style="padding:24px 28px;background:#f7f5f0;">
                    <p style="margin:0 0 8px;"><b>Property:</b> {property_address} — Unit {unit}</p>
                    <p style="margin:0 0 8px;"><b>Type:</b> {inspection_type}</p>
                    <p style="margin:0 0 8px;"><b>Date:</b> {date}</p>
                    <p style="margin:16px 0 0;">Please find the full inspection report attached.</p>
                </div>
            </div>
            """,
            "attachments": [{"filename": filename, "content": encoded}],
        })
        print(f"Email sent successfully for {property_address} unit {unit}")
    except Exception as e:
        print(f"Email send failed: {e}")


@app.route("/generate-pdf", methods=["POST"])
def generate_pdf_endpoint():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    print("Received data:", data)

    try:
        pdf_bytes = build_pdf(data)
    except Exception as e:
        print(f"PDF generation error: {e}")
        return jsonify({"error": f"PDF generation failed: {e}"}), 500

    property_address = data.get("property", "Property")
    unit = data.get("unit", "")
    inspection_type = data.get("inspection_type", "")
    raw_date = data.get("inspection_date", "")
    if raw_date and len(raw_date) >= 10:
        try:
            parts = raw_date[:10].split("-")
            short_date = f"{parts[1]}-{parts[2]}-{parts[0]}"
        except:
            short_date = raw_date[:10]
    else:
        short_date = raw_date

    # Archive PDF to Dropbox (failure here does not break the pipeline)
    dropbox_path = upload_to_dropbox(
        pdf_bytes, property_address, unit, inspection_type, short_date
    )

    # Send PDF via email
    send_email_with_pdf(property_address, unit, inspection_type, short_date, pdf_bytes)

    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    filename = f"Inspection_{property_address}_{unit}_{inspection_type}_{short_date}.pdf".replace(" ", "_")
    return jsonify({
        "status": "ok",
        "filename": filename,
        "pdf_base64": pdf_b64,
        "dropbox_path": dropbox_path,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
