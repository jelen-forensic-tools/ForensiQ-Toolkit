import os
import json
import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extractor import ExtractionResult


def generate_pdf_report(results: list, device_info: dict, output_dir: str) -> str:
    """Vygeneruje PDF report z výsledků extrakce pomocí reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, PageBreak, KeepTogether
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ── Registrace fontů s podporou češtiny ──────────────────────────────────
    # Použijeme Arial ze systému Windows (podporuje celou Latin Extended sadu)
    FONT_DIR = r"C:\Windows\Fonts"
    _fonts = {
        "FQ":      ("arial.ttf",   "Arial"),
        "FQ-Bold": ("arialbd.ttf", "Arial Bold"),
    }
    _registered = {}
    for alias, (fname, _) in _fonts.items():
        fpath = os.path.join(FONT_DIR, fname)
        if os.path.exists(fpath):
            try:
                pdfmetrics.registerFont(TTFont(alias, fpath))
                _registered[alias] = alias
            except Exception:
                _registered[alias] = "Helvetica" if "Bold" not in alias else "Helvetica-Bold"
        else:
            _registered[alias] = "Helvetica" if "Bold" not in alias else "Helvetica-Bold"

    F_NORMAL = _registered["FQ"]
    F_BOLD   = _registered["FQ-Bold"]

    # ── Barvy ─────────────────────────────────────────────────────────────────
    CLR_ACCENT  = colors.HexColor("#1a5276")   # tmavě modrá (tiskatelná)
    CLR_DARK    = colors.HexColor("#1c2833")
    CLR_HEADER  = colors.HexColor("#2e4057")
    CLR_GREEN   = colors.HexColor("#1e8449")
    CLR_RED     = colors.HexColor("#c0392b")
    CLR_MUTED   = colors.HexColor("#7f8c8d")
    CLR_WHITE   = colors.white
    CLR_ROW_ALT = colors.HexColor("#eaf4fb")
    CLR_LABEL   = colors.HexColor("#d6eaf8")
    CLR_BORDER  = colors.HexColor("#aab7b8")

    PAGE_W = A4[0] - 4 * cm   # šířka obsahu

    # ── Styly odstavců ────────────────────────────────────────────────────────
    style_title  = ParagraphStyle("fq_title",  fontName=F_BOLD,   fontSize=20,
                                   textColor=CLR_ACCENT, spaceAfter=3, leading=24)
    style_sub    = ParagraphStyle("fq_sub",    fontName=F_NORMAL,  fontSize=10,
                                   textColor=CLR_MUTED,  spaceAfter=14)
    style_h2     = ParagraphStyle("fq_h2",     fontName=F_BOLD,   fontSize=12,
                                   textColor=CLR_HEADER, spaceAfter=5, spaceBefore=14)
    style_body   = ParagraphStyle("fq_body",   fontName=F_NORMAL,  fontSize=9,
                                   textColor=CLR_MUTED,  spaceAfter=4)
    style_cell   = ParagraphStyle("fq_cell",   fontName=F_NORMAL,  fontSize=8,
                                   leading=11, wordWrap="CJK")
    style_cell_b = ParagraphStyle("fq_cell_b", fontName=F_BOLD,   fontSize=8,
                                   leading=11, textColor=CLR_WHITE)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pdf_path  = os.path.join(output_dir, "report.pdf")

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.5*cm, bottomMargin=2*cm,
        title="Paranoid-Android-User-toolkit Report", author="Paranoid-Android-User-toolkit"
    )

    story = []

    # ── Titulní strana ────────────────────────────────────────────────────────
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph("Paranoid-Android-User-toolkit", style_title))
    story.append(Paragraph("Forenzní report — Android zařízení", style_sub))
    story.append(HRFlowable(width="100%", thickness=1.5, color=CLR_ACCENT, spaceAfter=14))

    # ── Informace o zařízení ─────────────────────────────────────────────────
    story.append(Paragraph("Informace o zařízení", style_h2))

    dev_fields = [
        ("Výrobce",              device_info.get("manufacturer", "N/A")),
        ("Model",                device_info.get("model", "N/A")),
        ("Android verze",        device_info.get("android", "N/A")),
        ("SDK",                  device_info.get("sdk", "N/A")),
        ("Sériové číslo",        device_info.get("serial", "N/A")),
        ("IMEI",                 device_info.get("imei", "N/A")),
        ("Bezpečnostní záplata", device_info.get("security_patch", "N/A")),
        ("Baterie",              device_info.get("battery", "N/A")),
        ("Brand",                device_info.get("brand", "N/A")),
        ("Build ID",             device_info.get("build", "N/A")),
    ]
    # Dvousloupcová tabulka štítek | hodnota | štítek | hodnota
    dev_rows = []
    for i in range(0, len(dev_fields), 2):
        left  = dev_fields[i]
        right = dev_fields[i + 1] if i + 1 < len(dev_fields) else ("", "")
        dev_rows.append([
            Paragraph(left[0],  style_cell_b),
            Paragraph(left[1],  style_cell),
            Paragraph(right[0], style_cell_b),
            Paragraph(right[1], style_cell),
        ])

    dev_col = [3.8*cm, 5.8*cm, 3.8*cm, 5.6*cm]
    dev_tbl = Table(dev_rows, colWidths=dev_col, hAlign="LEFT")
    dev_tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), CLR_LABEL),
        ("BACKGROUND",  (0, 0), (0, -1), CLR_HEADER),
        ("BACKGROUND",  (2, 0), (2, -1), CLR_HEADER),
        ("TEXTCOLOR",   (0, 0), (0, -1), CLR_WHITE),
        ("TEXTCOLOR",   (2, 0), (2, -1), CLR_WHITE),
        ("ROWBACKGROUNDS", (1, 0), (1, -1), [CLR_LABEL, CLR_WHITE]),
        ("ROWBACKGROUNDS", (3, 0), (3, -1), [CLR_LABEL, CLR_WHITE]),
        ("GRID",        (0, 0), (-1, -1), 0.5, CLR_BORDER),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",(0, 0), (-1, -1), 8),
    ]))
    story.append(dev_tbl)
    story.append(Spacer(1, 16))

    # ── Souhrn extrakce ───────────────────────────────────────────────────────
    story.append(Paragraph("Souhrn extrakce", style_h2))

    sum_hdr = [
        Paragraph("Modul",         style_cell_b),
        Paragraph("Stav",          style_cell_b),
        Paragraph("Záznamy",       style_cell_b),
        Paragraph("Poznámka",      style_cell_b),
    ]
    sum_rows = [sum_hdr]
    for r in results:
        stav    = "OK" if r.success else "CHYBA"
        pozn    = "" if r.success else r.error[:80]
        count   = str(r.count) if r.success else ""
        clr_stav = CLR_GREEN if r.success else CLR_RED
        sum_rows.append([
            Paragraph(r.label, style_cell),
            Paragraph(stav,    ParagraphStyle("st", fontName=F_BOLD, fontSize=8,
                                              textColor=clr_stav, leading=11)),
            Paragraph(count,   style_cell),
            Paragraph(pozn,    style_cell),
        ])

    sum_tbl = Table(sum_rows, colWidths=[6.5*cm, 2*cm, 2.5*cm, 8*cm], hAlign="LEFT", repeatRows=1)
    sum_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), CLR_HEADER),
        ("TEXTCOLOR",    (0, 0), (-1, 0), CLR_WHITE),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [CLR_WHITE, CLR_ROW_ALT]),
        ("GRID",         (0, 0), (-1, -1), 0.4, CLR_BORDER),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
        ("LEFTPADDING",  (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(sum_tbl)
    story.append(Paragraph(f"Vygenerováno: {timestamp}", style_body))
    story.append(PageBreak())

    # ── Detailní data každého modulu ──────────────────────────────────────────
    def _make_data_table(data_list: list, max_rows: int = 200) -> Table | None:
        """Sestaví tabulku z listu slovníků s optimálními šířkami sloupců."""
        if not data_list:
            return None
        # Vyfiltruj klíče — přeskoč příliš dlouhé nebo binární hodnoty
        sample = data_list[0]
        keys = [k for k in sample.keys()
                if not any(x in k.lower() for x in ("path", "url", "raw"))]
        if not keys:
            keys = list(sample.keys())

        # Odhadni šířky sloupců podle délky obsahu (max 200 znaků na buňku)
        col_count = len(keys)
        base_w    = PAGE_W / col_count
        col_w     = [max(2*cm, min(base_w, 9*cm))] * col_count

        hdr = [Paragraph(k, style_cell_b) for k in keys]
        rows = [hdr]
        for rec in data_list[:max_rows]:
            row = []
            for k in keys:
                val = str(rec.get(k, ""))[:200]
                row.append(Paragraph(val, style_cell))
            rows.append(row)

        tbl = Table(rows, colWidths=col_w, repeatRows=1, hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), CLR_HEADER),
            ("TEXTCOLOR",     (0, 0), (-1, 0), CLR_WHITE),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [CLR_WHITE, CLR_ROW_ALT]),
            ("GRID",          (0, 0), (-1, -1), 0.3, CLR_BORDER),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ]))
        return tbl

    for r in results:
        if not r.success or not r.data:
            continue

        section = []
        section.append(Paragraph(r.label, style_h2))
        section.append(HRFlowable(width="100%", thickness=0.8,
                                   color=CLR_ACCENT, spaceAfter=6))

        if isinstance(r.data, list) and r.data:
            tbl = _make_data_table(r.data)
            if tbl:
                section.append(tbl)
            if len(r.data) > 200:
                section.append(Paragraph(
                    f"Zobrazeno 200 z {len(r.data)} záznamů. "
                    f"Kompletní data: {os.path.basename(r.file_path)}",
                    style_body
                ))

        elif isinstance(r.data, dict):
            d_rows = []
            for k, v in r.data.items():
                val = str(v)[:300]
                d_rows.append([
                    Paragraph(k,   style_cell_b),
                    Paragraph(val, style_cell),
                ])
            if d_rows:
                d_tbl = Table(d_rows, colWidths=[5*cm, PAGE_W - 5*cm], hAlign="LEFT")
                d_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0, 0), (0, -1), CLR_HEADER),
                    ("TEXTCOLOR",     (0, 0), (0, -1), CLR_WHITE),
                    ("ROWBACKGROUNDS",(1, 0), (1, -1), [CLR_WHITE, CLR_ROW_ALT]),
                    ("GRID",          (0, 0), (-1, -1), 0.4, CLR_BORDER),
                    ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING",    (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                ]))
                section.append(d_tbl)

        section.append(Spacer(1, 10))
        story.append(KeepTogether(section[:3]))   # nadpis + HR + první část tabulky pohromadě
        if len(section) > 3:
            story.extend(section[3:])

    # ── Zápatí na každé stránce ───────────────────────────────────────────────
    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(F_NORMAL, 8)
        canvas.setFillColor(CLR_MUTED)
        canvas.drawString(2*cm, 1.2*cm,
                          "Paranoid-Android-User-toolkit  —  Forenzní analýza Android zařízení")
        canvas.drawRightString(A4[0] - 2*cm, 1.2*cm,
                               f"Strana {doc.page}")
        canvas.setStrokeColor(CLR_BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(2*cm, 1.5*cm, A4[0] - 2*cm, 1.5*cm)
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return pdf_path


def generate_excel_report(results: list, device_info: dict, output_dir: str) -> str:
    """Vygeneruje Excel sešit (.xlsx) z výsledků extrakce."""
    from openpyxl import Workbook
    from openpyxl.styles import (
        PatternFill, Font, Alignment, Border, Side, GradientFill
    )
    from openpyxl.utils import get_column_letter

    FILL_HEADER  = PatternFill("solid", fgColor="16213E")
    FILL_TITLE   = PatternFill("solid", fgColor="0D1117")
    FILL_ROW_ALT = PatternFill("solid", fgColor="F0F4FF")
    FILL_GREEN   = PatternFill("solid", fgColor="C8F0CC")
    FILL_RED     = PatternFill("solid", fgColor="F9D0CF")

    FONT_TITLE  = Font(name="Calibri", size=14, bold=True, color="00D4FF")
    FONT_HEADER = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    FONT_NORMAL = Font(name="Calibri", size=10)
    FONT_MUTED  = Font(name="Calibri", size=9, color="888888")
    FONT_OK     = Font(name="Calibri", size=10, bold=True, color="2E7D32")
    FONT_ERR    = Font(name="Calibri", size=10, bold=True, color="C62828")

    ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ALIGN_LEFT   = Alignment(horizontal="left",   vertical="center", wrap_text=True)

    thin = Side(border_style="thin", color="CCCCCC")
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

    def _auto_width(ws, min_w=10, max_w=50):
        for col in ws.columns:
            length = max(
                len(str(cell.value or "")) for cell in col
            )
            ws.column_dimensions[get_column_letter(col[0].column)].width = max(min_w, min(length + 2, max_w))

    def _header_row(ws, headers: list, row: int = 1):
        for ci, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=ci, value=h)
            c.fill   = FILL_HEADER
            c.font   = FONT_HEADER
            c.alignment = ALIGN_CENTER
            c.border = BORDER

    def _data_row(ws, values: list, row: int, alt: bool = False):
        for ci, v in enumerate(values, start=1):
            c = ws.cell(row=row, column=ci, value=str(v) if v is not None else "")
            c.font      = FONT_NORMAL
            c.alignment = ALIGN_LEFT
            c.border    = BORDER
            if alt:
                c.fill = FILL_ROW_ALT

    wb = Workbook()

    # ── List 1: Informace o zařízení ─────────────────────────────────────────
    ws_dev = wb.active
    ws_dev.title = "Zařízení"
    ws_dev.merge_cells("A1:B1")
    c = ws_dev["A1"]
    c.value     = "Paranoid-Android-User-toolkit — Informace o zařízení"
    c.font      = FONT_TITLE
    c.fill      = FILL_TITLE
    c.alignment = ALIGN_CENTER
    ws_dev.row_dimensions[1].height = 28

    for ri, (k, v) in enumerate(device_info.items(), start=2):
        label = ws_dev.cell(row=ri, column=1, value=k)
        label.font = Font(name="Calibri", size=10, bold=True, color="16213E")
        label.fill = PatternFill("solid", fgColor="E8F4FD")
        label.border = BORDER
        label.alignment = ALIGN_LEFT

        value = ws_dev.cell(row=ri, column=2, value=str(v))
        value.font      = FONT_NORMAL
        value.border    = BORDER
        value.alignment = ALIGN_LEFT

    ws_dev.column_dimensions["A"].width = 28
    ws_dev.column_dimensions["B"].width = 45

    # ── List 2: Souhrn ────────────────────────────────────────────────────────
    ws_sum = wb.create_sheet("Souhrn extrakce")
    ws_sum.merge_cells("A1:D1")
    c = ws_sum["A1"]
    c.value     = f"Souhrn extrakce — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    c.font      = FONT_TITLE
    c.fill      = FILL_TITLE
    c.alignment = ALIGN_CENTER
    ws_sum.row_dimensions[1].height = 28

    _header_row(ws_sum, ["Modul", "Stav", "Počet záznamů", "Soubor"], row=2)
    ws_sum.row_dimensions[2].height = 20

    for ri, r in enumerate(results, start=3):
        alt = ri % 2 == 0
        _data_row(ws_sum, [
            r.label,
            "OK" if r.success else "CHYBA",
            r.count if r.success else 0,
            os.path.basename(r.file_path) if r.file_path else ""
        ], ri, alt)
        # Obarvi stav
        stav_cell = ws_sum.cell(row=ri, column=2)
        if r.success:
            stav_cell.font = FONT_OK
            stav_cell.fill = FILL_GREEN
        else:
            stav_cell.font = FONT_ERR
            stav_cell.fill = FILL_RED
            ws_sum.cell(row=ri, column=3).value = r.error

    _auto_width(ws_sum)

    # ── Listy pro každý modul ─────────────────────────────────────────────────
    for r in results:
        if not r.success or not r.data:
            continue

        # Zkrať název listu na max 31 znaků (limit Excelu)
        sheet_name = r.label[:31]
        ws = wb.create_sheet(sheet_name)

        if isinstance(r.data, list) and r.data:
            keys = list(r.data[0].keys())
            _header_row(ws, keys, row=1)
            ws.row_dimensions[1].height = 20

            for ri, row in enumerate(r.data, start=2):
                alt = ri % 2 == 0
                _data_row(ws, [row.get(k, "") for k in keys], ri, alt)

            ws.auto_filter.ref = f"A1:{get_column_letter(len(keys))}1"
            ws.freeze_panes = "A2"
            _auto_width(ws)

        elif isinstance(r.data, dict):
            _header_row(ws, ["Klíč", "Hodnota"], row=1)
            for ri, (k, v) in enumerate(r.data.items(), start=2):
                _data_row(ws, [k, str(v)], ri, ri % 2 == 0)
            _auto_width(ws)

    xlsx_path = os.path.join(output_dir, "report.xlsx")
    wb.save(xlsx_path)
    return xlsx_path


def generate_report(results: list, device_info: dict, output_dir: str) -> str:
    """Vygeneruje HTML report z výsledků extrakce."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_path = os.path.join(output_dir, "report.html")

    model = device_info.get("model", "N/A")
    manufacturer = device_info.get("manufacturer", "N/A")
    android = device_info.get("android", "N/A")
    serial = device_info.get("serial", "N/A")

    modules_html = ""
    for r in results:
        status_icon = "✅" if r.success else "❌"
        status_color = "#4CAF50" if r.success else "#f44336"
        count_html = f'<span style="color:#888">({r.count} záznamů)</span>' if r.success else f'<span style="color:#f44336">{r.error}</span>'

        data_html = ""
        if r.success and r.data:
            if isinstance(r.data, list) and len(r.data) > 0:
                # Tabulka z dat
                keys = list(r.data[0].keys()) if r.data else []
                data_html = '<div style="overflow-x:auto"><table class="data-table"><thead><tr>'
                for k in keys:
                    data_html += f"<th>{k}</th>"
                data_html += "</tr></thead><tbody>"
                for row in r.data[:100]:  # Max 100 řádků v reportu
                    data_html += "<tr>"
                    for k in keys:
                        val = str(row.get(k, ""))
                        data_html += f"<td>{val}</td>"
                    data_html += "</tr>"
                data_html += "</tbody></table></div>"
                if len(r.data) > 100:
                    data_html += f'<p style="color:#888;font-size:12px">Zobrazeno 100 z {len(r.data)} záznamů. Viz soubor: {os.path.basename(r.file_path)}</p>'
            elif isinstance(r.data, dict):
                data_html = '<table class="data-table">'
                for k, v in r.data.items():
                    data_html += f"<tr><th>{k}</th><td>{v}</td></tr>"
                data_html += "</table>"

        modules_html += f"""
        <div class="module-card">
            <div class="module-header" onclick="toggleModule(this)">
                <span>{status_icon} {r.label}</span>
                <span>{count_html} <span class="toggle-icon">▼</span></span>
            </div>
            <div class="module-body">
                {data_html if data_html else '<p style="color:#888">Žádná data</p>'}
            </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="UTF-8">
<title>Paranoid-Android-User-toolkit Report - {model}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', sans-serif; background: #0f0f1a; color: #e0e0e0; padding: 20px; }}
  h1 {{ color: #00d4ff; margin-bottom: 5px; }}
  .subtitle {{ color: #888; font-size: 13px; margin-bottom: 30px; }}
  .device-card {{ background: #1a1a2e; border: 1px solid #00d4ff33; border-radius: 12px; padding: 20px; margin-bottom: 30px; display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; }}
  .device-field {{ display: flex; flex-direction: column; }}
  .device-field .label {{ color: #888; font-size: 11px; text-transform: uppercase; margin-bottom: 3px; }}
  .device-field .value {{ color: #fff; font-size: 14px; font-weight: 500; }}
  .module-card {{ background: #1a1a2e; border: 1px solid #ffffff11; border-radius: 12px; margin-bottom: 15px; overflow: hidden; }}
  .module-header {{ display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; cursor: pointer; background: #16213e; }}
  .module-header:hover {{ background: #1a2744; }}
  .module-body {{ padding: 15px 20px; display: none; }}
  .module-body.open {{ display: block; }}
  .data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .data-table th {{ background: #0d1117; color: #00d4ff; padding: 8px 12px; text-align: left; font-weight: 500; }}
  .data-table td {{ padding: 7px 12px; border-bottom: 1px solid #ffffff0d; }}
  .data-table tr:hover td {{ background: #ffffff08; }}
  .toggle-icon {{ font-size: 11px; color: #888; }}
  footer {{ margin-top: 30px; text-align: center; color: #444; font-size: 12px; }}
</style>
</head>
<body>
<h1>Paranoid-Android-User-toolkit - Forenzní Report</h1>
<p class="subtitle">Vygenerováno: {timestamp}</p>

<div class="device-card">
  <div class="device-field"><span class="label">Výrobce</span><span class="value">{manufacturer}</span></div>
  <div class="device-field"><span class="label">Model</span><span class="value">{model}</span></div>
  <div class="device-field"><span class="label">Android</span><span class="value">{android}</span></div>
  <div class="device-field"><span class="label">Sériové číslo</span><span class="value">{serial}</span></div>
  <div class="device-field"><span class="label">IMEI</span><span class="value">{device_info.get("imei", "N/A")}</span></div>
  <div class="device-field"><span class="label">Baterie</span><span class="value">{device_info.get("battery", "N/A")}</span></div>
</div>

{modules_html}

<footer>Paranoid-Android-User-toolkit &bull; Forenzní analýza Android zařízení</footer>

<script>
function toggleModule(header) {{
  const body = header.nextElementSibling;
  const icon = header.querySelector('.toggle-icon');
  body.classList.toggle('open');
  icon.textContent = body.classList.contains('open') ? '▲' : '▼';
}}
// Otevři první modul automaticky
document.addEventListener('DOMContentLoaded', () => {{
  const first = document.querySelector('.module-header');
  if (first) toggleModule(first);
}});
</script>
</body>
</html>"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)

    return report_path
