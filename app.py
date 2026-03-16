"""
Property Inspection PDF Generator
----------------------------------
Deploy on Railway. Make.com calls POST /generate-pdf with JSON from Airtable.

Requirements:
  pip install flask reportlab requests resend pillow

Environment variables needed:
  RESEND_API_KEY  - your Resend API key
  FROM_EMAIL      - verified sender email
"""

import io
import os
import requests
import tempfile
import resend
from flask import Flask, request, jsonify
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable, PageBreak, KeepTogether
)
import base64

app = Flask(__name__)

# ── Recipients ────────────────────────────────────────────────────────────────
# Add or remove emails here anytime. Save on GitHub and Railway updates automatically.
REPORT_RECIPIENTS = [
    "Mark@Halsteadholdings.com",
    "Mike@Halsteadholdings.com",
    "Roxanne@Halsteadholdings.com",
]

# ── Branding ──────────────────────────────────────────────────────────────────
COMPANY_NAME = "Halstead Holdings"
COMPANY_LOGO = "logo.png"
BRAND_COLOR  = colors.HexColor("#1A3C5E")
ACCENT_COLOR = colors.HexColor("#E8F0F8")

# ── Condition severity ────────────────────────────────────────────────────────
SEVERITY_ORDER = ["Critical", "Major", "Minor", "Pass"]
SEVERITY_COLORS = {
    "Critical": colors.HexColor("#F8D7DA"),
    "Major":    colors.HexColor("#FFF3CD"),
    "Minor":    colors.HexColor("#D1ECF1"),
    "Pass":     colors.HexColor("#D4EDDA"),
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


def build_pdf(data: dict) -> bytes:
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"],
        fontSize=22, textColor=BRAND_COLOR, spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontSize=11, textColor=colors.HexColor("#555555"), spaceAfter=2,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"],
        fontSize=13, textColor=BRAND_COLOR, spaceBefore=16, spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontSize=10, leading=14, spaceAfter=4,
    )
    caption_style = ParagraphStyle(
        "Caption", parent=styles["Italic"],
        fontSize=9, textColor=colors.HexColor("#666666"), spaceAfter=8,
    )

    story = []

    # Header
    logo_cell = ""
    if os.path.exists(COMPANY_LOGO):
        logo_img = Image(COMPANY_LOGO, width=1.4 * inch, height=0.7 * inch)
        logo_img.hAlign = "LEFT"
        logo_cell = logo_img

    title_cell = [
        Paragraph("Property Inspection Report", title_style),
        Paragraph(COMPANY_NAME, subtitle_style),
    ]
    header_table = Table([[logo_cell, title_cell]], colWidths=[1.6 * inch, 5.4 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2, color=BRAND_COLOR, spaceAfter=12))

    # Property info
    raw_date = data.get("inspection_date", "—")
    short_date = raw_date[:10] if raw_date and len(raw_date) > 10 else raw_date

    info_rows = [
        ["Property",        data.get("property", "—")],
        ["Unit",            data.get("unit", "—")],
        ["Inspection Type", data.get("inspection_type", "—")],
        ["Inspection Date", short_date],
        ["Inspector",       data.get("inspector", "—")],
        ["Notes",           data.get("notes", "—")],
    ]
    info_table = Table(info_rows, colWidths=[1.8 * inch, 5.2 * inch])
    info_table.setStyle(TableStyle([
        ("FONTNAME",  (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",  (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), BRAND_COLOR),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, ACCENT_COLOR]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 16))

    # Summary table
    conditions = data.get("conditions", [])
    if isinstance(conditions, dict):
        conditions = conditions.get("array", [conditions])

    counts = {s: 0 for s in SEVERITY_ORDER}
    for c in conditions:
        sev = c.get("status", "Minor")
        if sev in counts:
            counts[sev] += 1

    story.append(Paragraph("Summary", section_style))
    total_issues = sum(v for k, v in counts.items() if k != "Pass")
    story.append(Paragraph(
        f"This inspection identified <b>{total_issues} item(s) requiring attention</b> "
        f"across {len(conditions)} total observations.",
        body_style
    ))
    story.append(Spacer(1, 8))

    action_map = {
        "Critical": "Immediate repair required",
        "Major":    "Repair within 30 days",
        "Minor":    "Repair within 90 days",
        "Pass":     "No action needed",
    }
    summary_data = [["Severity", "Count", "Action Required"]] + [
        [sev, str(counts[sev]), action_map[sev]] for sev in SEVERITY_ORDER
    ]
    summary_table = Table(summary_data, colWidths=[1.5 * inch, 1 * inch, 4.5 * inch])
    style_cmds = [
        ("BACKGROUND",  (0, 0), (-1, 0), BRAND_COLOR),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("ALIGN",       (1, 0), (1, -1), "CENTER"),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",  (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for i, sev in enumerate(SEVERITY_ORDER, start=1):
        style_cmds.append(("BACKGROUND", (0, i), (-1, i), SEVERITY_COLORS.get(sev, colors.white)))
    summary_table.setStyle(TableStyle(style_cmds))
    story.append(summary_table)
    story.append(PageBreak())

    # Detailed conditions
    story.append(Paragraph("Inspection Findings", section_style))
    story.append(HRFlowable(width="100%", thickness=1, color=BRAND_COLOR, spaceAfter=10))

    for i, condition in enumerate(conditions, start=1):
        area      = condition.get("area", "General")
        component = condition.get("component", "")
        status    = condition.get("status", "")
        notes     = condition.get("notes", "")
        photo_url = condition.get("photo", "")

        sev_color = SEVERITY_COLORS.get(status, colors.white)
        label = f"{i}. {area} — {component}" if component else f"{i}. {area}"

        cond_header = Table([[label, status]], colWidths=[5 * inch, 2 * inch])
        cond_header.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), sev_color),
            ("FONTNAME",      (0, 0), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 10),
            ("TEXTCOLOR",     (0, 0), (-1, -1), colors.HexColor("#333333")),
            ("ALIGN",         (1, 0), (1, 0), "RIGHT"),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (0, 0), 8),
            ("RIGHTPADDING",  (1, 0), (1, 0), 8),
            ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#AAAAAA")),
        ]))

        block = [cond_header]
        if notes:
            block += [Spacer(1, 4), Paragraph(str(notes), body_style)]

        if photo_url and isinstance(photo_url, str) and photo_url.startswith("http"):
            tmp_path = download_image(photo_url)
            if tmp_path:
                try:
                    img = Image(tmp_path, width=4 * inch, height=3 * inch)
                    img.hAlign = "LEFT"
                    block += [Spacer(1, 6), img, Paragraph(f"Photo — {area}", caption_style)]
                except Exception as e:
                    print(f"Could not embed image: {e}")

        block.append(Spacer(1, 14))
        story.append(KeepTogether(block))

    # Footer
    story.append(HRFlowable(width="100%", thickness=1, color=BRAND_COLOR, spaceBefore=12))
    story.append(Paragraph(
        f"Report prepared by {data.get('inspector', COMPANY_NAME)} on "
        f"{data.get('inspection_date', '—')}. "
        "All findings are based on visual inspection only.",
        caption_style
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
            "subject": f"Property Inspection Report — {property_address}",
            "html": f"<p>A new inspection report is ready for <strong>{property_address}</strong>.</p><p>Please find the full report attached.</p><p>{COMPANY_NAME}</p>",
            "attachments": [{"filename": filename, "content": encoded}],
        })
        print(f"Email sent successfully for {property_address}")
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
    # Format date from 2026-03-15T20:10:42.269Z to 2026-03-15
    short_date = raw_date[:10] if raw_date else ""

    send_email_with_pdf(property_address, unit, inspection_type, short_date, pdf_bytes)

    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    filename = f"Inspection_{property_address}_{unit}_{inspection_type}_{short_date}.pdf".replace(" ", "_")
    return jsonify({
        "status": "ok",
        "filename": filename,
        "pdf_base64": pdf_b64,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
