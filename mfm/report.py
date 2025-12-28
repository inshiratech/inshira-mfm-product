from io import BytesIO
from datetime import datetime
import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

def _safe_text(s: str) -> str:
    return (s or "").replace("\n", " ").strip()

def build_pdf_report(site_name: str, boundary_start: str, boundary_end: str, results: dict, sankey_fig=None) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    y = h - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "Gate-to-Gate Material Flow Map Report")
    y -= 22
    c.setFont("Helvetica", 11)
    c.drawString(40, y, f"Site: {_safe_text(site_name)}")
    y -= 16
    c.drawString(40, y, f"Boundary: {_safe_text(boundary_start)}  →  {_safe_text(boundary_end)}")
    y -= 16
    c.drawString(40, y, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    y -= 22

    # KPIs
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Key KPIs")
    y -= 16
    c.setFont("Helvetica", 11)

    kpis = [
        ("Material in (kg)", f"{results.get('mat_in_kg',0):,.0f}"),
        ("Product out (kg)", f"{results.get('prod_out_kg',0):,.0f}"),
        ("Waste out (kg)", f"{results.get('waste_out_kg',0):,.0f}"),
        ("Unaccounted loss (kg)", f"{results.get('unaccounted_kg',0):,.0f}"),
        ("Material efficiency (%)", f"{results.get('material_eff_pct',0):.1f}"),
        ("Energy (kWh) electricity", f"{results.get('energy_elec_kwh',0):,.0f}"),
        ("Energy (kWh) gas", f"{results.get('energy_gas_kwh',0):,.0f}"),
        ("Energy intensity (kWh/kg product)", f"{results.get('energy_intensity_kwh_per_kg',0):.3f}"),
        ("Diversion rate (%)", f"{results.get('diversion_pct',0):.1f}"),
        ("Total emissions (kgCO2e)", f"{results.get('co2e_total_kg',0):,.0f}"),
        ("Avoided emissions (kgCO2e)", f"{results.get('co2e_avoided_kg',0):,.0f}"),
    ]

    for k, v in kpis:
        c.drawString(50, y, f"{k}: {v}")
        y -= 14
        if y < 120:
            c.showPage()
            y = h - 50
            c.setFont("Helvetica", 11)

    # Sankey image
    if sankey_fig is not None:
        try:
            img_bytes = sankey_fig.to_image(format="png", width=1200, height=650, scale=2)
            img = ImageReader(BytesIO(img_bytes))
            c.setFont("Helvetica-Bold", 12)
            c.drawString(40, y, "Material Flow Map")
            y -= 10
            img_w = w - 80
            img_h = img_w * 0.54
            if y - img_h < 60:
                c.showPage()
                y = h - 50
            c.drawImage(img, 40, y - img_h, width=img_w, height=img_h, preserveAspectRatio=True, mask='auto')
            y -= (img_h + 18)
        except Exception:
            c.setFont("Helvetica", 10)
            c.drawString(40, y, "Note: Sankey image export unavailable in this environment.")
            y -= 16

    def bullet_section(title: str, items: list[str]):
        nonlocal y
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, title)
        y -= 16
        c.setFont("Helvetica", 11)
        if not items:
            c.drawString(50, y, "• (none)")
            y -= 14
            return
        for it in items[:12]:
            text = _safe_text(it)
            c.drawString(50, y, f"• {text[:120]}")
            y -= 14
            if y < 60:
                c.showPage()
                y = h - 50
                c.setFont("Helvetica", 11)

    bullet_section("AI assist highlights", results.get("ai_messages", []))
    bullet_section("Assumptions & data gaps", results.get("assumptions", []))
    bullet_section("Circular opportunities", results.get("opportunities", []))

    # Bottleneck summary
    bdf = results.get("bottlenecks_table")
    if isinstance(bdf, pd.DataFrame) and not bdf.empty:
        top = bdf.iloc[0]
        bullet_section("Bottleneck risk (top)", [f"{top['Process']} — {top['Risk']} (utilisation: {float(top['Utilisation'])*100:.0f}%)"])

    c.save()
    return buf.getvalue()
