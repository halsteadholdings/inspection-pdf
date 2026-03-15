"""
Property Inspection PDF Generator
----------------------------------
Deploy on Railway, Render, or AWS Lambda.
Make.com calls POST /generate-pdf with JSON from Airtable.

Requirements:
  pip install flask reportlab requests sendgrid pillow

Environment variables needed:
  SENDGRID_API_KEY   - your SendGrid API key
  FROM_EMAIL         - verified sender email (e.g. reports@yourcompany.com)
  WEBHOOK_SECRET     - optional shared secret for request validation
"""

import io
import os
import requests
import tempfile
from flask import Flask, request, jsonify
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable, PageBreak, KeepTogether
)
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)
import base64

app = Flask(__name__)

# ── Recipients ────────────────────────────────────────────────────────────────
# Add or remove emails here anytime. Save the file and Railway updates automatically.
REPORT_RECIPIENTS = [
    "Mark@Halsteadholdings.com",
    "Mike@Halsteadholdings.com",
    "Roxanne@Halsteadholdings.com",
]

# ── Branding ──────────────────────────────────────────────────────────────────
COMPANY_NAME  = "Halstead Holdings"
COMPANY_LOGO  = "logo.png"          # path to your logo file, or a URL
BRAND_COLOR   = colors.HexColor("#1A3C5E")   # deep navy — change to your brand
ACCENT_COLOR  = colors.HexColor("#E8F0F8")   # light tint for table headers
WARN_COLOR    = colors.HexColor("#FFF3CD")   # amber for "needs improvement"
PASS_COLOR    = colors.HexColor("#D4EDDA")   # green for passing items

# ── Condition severity labels ─────────────────────────────────────────────────
SEVERITY_ORDER = ["Critical", "Major", "Minor", "Pass"]
SEVERITY_COLORS = {
    "Critical": colors.HexColor("#F8D7DA"),
    "Major":    colors.HexColor("#FFF3CD"),
    "Minor":    colors.HexColor("#D1ECF1"),
    "Pass":     colors.HexColor("#D4EDDA"),
}


def download_image(url: str) -> str | None:
    """Download a Cloudinary image URL to a temp file. Returns temp file path."""
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
    """
    Build the full inspection PDF and return raw bytes.

    Expected `data` keys (map these to your Airtable field names):
      property_address  str
      inspection_date   str
      inspector_name    str
      client_name       str
      client_email      str
      conditions        list[dict]  — each has: area, description, severity, photo_url
    """
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

    # Custom styles
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=22,
        textColor=BRAND_COLOR,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#555555"),
        spaceAfter=2,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=BRAND_COLOR,
        spaceBefore=16,
        spaceAfter=6,
        borderPad=4,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        spaceAfter=4,
    )
    caption_style = ParagraphStyle(
        "Caption",
        parent=styles["Italic"],
        fontSize=9,
        textColor=colors.HexColor("#666666"),
        spaceAfter=8,
    )

    story = []

    # ── Header: logo + title ──────────────────────────────────────────────────
    header_data = []
    logo_cell = ""
    if os.path.exists(COMPANY_LOGO):
        logo_img = Image(COMPANY_LOGO, width=1.4 * inch, height=0.7 * inch)
        logo_img.hAlign = "LEFT"
        logo_cell = logo_img

    title_cell = [
        Paragraph("Property Inspection Report", title_style),
        Paragraph(COMPANY_NAME, subtitle_style),
    ]
    header_data.append([logo_cell, title_cell])

    header_table = Table(header_data, colWidths=[1.6 * inch, 5.4 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "LEFT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=2, color=BRAND_COLOR, spaceAfter=12))

    # ── Property info block ───────────────────────────────────────────────────
    info_rows = [
        ["Property Address", data.get("property_address", "—")],
        ["Inspection Date",  data.get("inspection_date", "—")],
        ["Inspector",        data.get("inspector_name", "—")],
        ["Prepared For",     data.get("client_name", "—")],
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

    # ── Summary table ─────────────────────────────────────────────────────────
    conditions = data.get("conditions", [])
    counts = {s: 0 for s in SEVERITY_ORDER}
    for c in conditions:
        sev = c.get("severity", "Minor")
        if sev in counts:
            counts[sev] += 1

    story.append(Paragraph("Summary", section_style))

    total_issues = sum(v for k, v in counts.items() if k != "Pass")
    summary_intro = (
        f"This inspection identified <b>{total_issues} item(s) requiring attention</b> "
        f"across {len(conditions)} total observations."
    )
    story.append(Paragraph(summary_intro, body_style))
    story.append(Spacer(1, 8))

    summary_header = [["Severity", "Count", "Action Required"]]
    action_map = {
        "Critical": "Immediate repair required",
        "Major":    "Repair within 30 days",
        "Minor":    "Repair within 90 days",
        "Pass":     "No action needed",
    }
    summary_rows = [
        [sev, str(counts[sev]), action_map[sev]]
        for sev in SEVERITY_ORDER
    ]
    summary_data = summary_header + summary_rows

    summary_table = Table(summary_data, colWidths=[1.5 * inch, 1 * inch, 4.5 * inch])
    style_cmds = [
        ("BACKGROUND",  (0, 0), (-1, 0), BRAND_COLOR),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("ALIGN",       (1, 0), (1, -1), "CENTER"),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ACCENT_COLOR]),
        ("TOPPADDING",  (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    # Color-code severity rows
    for i, sev in enumerate(SEVERITY_ORDER, start=1):
        bg = SEVERITY_COLORS.get(sev, colors.white)
        style_cmds.append(("BACKGROUND", (0, i), (-1, i), bg))

    summary_table.setStyle(TableStyle(style_cmds))
    story.append(summary_table)
    story.append(PageBreak())

    # ── Detailed conditions ───────────────────────────────────────────────────
    story.append(Paragraph("Inspection Findings", section_style))
    story.append(HRFlowable(width="100%", thickness=1, color=BRAND_COLOR, spaceAfter=10))

    for i, condition in enumerate(conditions, start=1):
        area        = condition.get("area", "General")
        description = condition.get("description", "")
        severity    = condition.get("severity", "Minor")
        photo_url   = condition.get("photo_url", "")

        sev_color = SEVERITY_COLORS.get(severity, colors.white)

        # Condition header row
        cond_header = Table(
            [[f"{i}. {area}", severity]],
            colWidths=[5 * inch, 2 * inch]
        )
        cond_header.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), sev_color),
            ("FONTNAME",      (0, 0), (0, 0), "Helvetica-Bold"),
            ("FONTNAME",      (1, 0), (1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 10),
            ("TEXTCOLOR",     (0, 0), (-1, -1), colors.HexColor("#333333")),
            ("ALIGN",         (1, 0), (1, 0), "RIGHT"),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (0, 0), 8),
            ("RIGHTPADDING",  (1, 0), (1, 0), 8),
            ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#AAAAAA")),
        ]))

        description_para = Paragraph(description, body_style)

        block = [cond_header, Spacer(1, 4), description_para]

        # Embed photo if available
        if photo_url:
            tmp_path = download_image(photo_url)
            if tmp_path:
                try:
                    img = Image(tmp_path, width=4 * inch, height=3 * inch)
                    img.hAlign = "LEFT"
                    block.append(Spacer(1, 6))
                    block.append(img)
                    block.append(Paragraph(f"Photo — {area}", caption_style))
                except Exception as e:
                    print(f"Could not embed image for condition {i}: {e}")

        block.append(Spacer(1, 14))
        story.append(KeepTogether(block))

    # ── Footer note ───────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=BRAND_COLOR, spaceBefore=12))
    story.append(Paragraph(
        f"This report was prepared by {data.get('inspector_name', COMPANY_NAME)} "
        f"on {data.get('inspection_date', '—')}. "
        "All findings are based on visual inspection only and do not constitute a warranty.",
        caption_style
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def send_email_with_pdf(property_address: str, pdf_bytes: bytes):
    """Send the PDF to all recipients in REPORT_RECIPIENTS via SendGrid."""
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("FROM_EMAIL", "reports@halsteadholdings.com")

    if not api_key:
        print("SENDGRID_API_KEY not set — skipping email send")
        return

    encoded = base64.b64encode(pdf_bytes).decode()
    filename = f"Inspection_Report_{property_address.replace(' ', '_')}.pdf"

    message = Mail(
        from_email=from_email,
        to_emails=REPORT_RECIPIENTS,
        subject=f"Property Inspection Report — {property_address}",
        html_content=f"""
        <p>A new inspection report is ready for <strong>{property_address}</strong>.</p>
        <p>Please find the full report attached.</p>
        <p>{COMPANY_NAME}</p>
        """
    )

    attachment = Attachment(
        FileContent(encoded),
        FileName(filename),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    message.attachment = attachment

    try:
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"Email sent to {to_email} — status {response.status_code}")
    except Exception as e:
        print(f"Email send failed: {e}")


# ── Webhook endpoint ──────────────────────────────────────────────────────────
@app.route("/generate-pdf", methods=["POST"])
def generate_pdf_endpoint():
    """
    Called by Make.com when an Airtable row is marked Completed.

    Expected JSON body (you configure this mapping in Make.com):
    {
      "property_address": "123 Main St, Dallas TX",
      "inspection_date":  "2026-03-15",
      "inspector_name":   "Jane Smith",
      "client_name":      "John Doe",
      "client_email":     "john@example.com",
      "conditions": [
        {
          "area":        "Roof",
          "description": "Missing shingles on north-facing slope.",
          "severity":    "Major",
          "photo_url":   "https://res.cloudinary.com/yourcloud/image/upload/abc123.jpg"
        },
        ...
      ]
    }
    """
    # Optional: validate a shared secret header
    secret = os.environ.get("WEBHOOK_SECRET")
    if secret and request.headers.get("X-Webhook-Secret") != secret:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    try:
        pdf_bytes = build_pdf(data)
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {e}"}), 500

    property_address = data.get("property_address", "Property")

    send_email_with_pdf(property_address, pdf_bytes)

    # Return the PDF as base64 so Make.com can also upload it back to Airtable
    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    return jsonify({
        "status": "ok",
        "filename": f"Inspection_{property_address.replace(' ', '_')}.pdf",
        "pdf_base64": pdf_b64,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
