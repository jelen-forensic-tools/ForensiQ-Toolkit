import os
import json
import datetime
from typing import TYPE_CHECKING
from .i18n import t, has, field_label, get_lang


def _dev_label(key: str) -> str:
    """Label for a device_info key (dashboard f.* keys first, then kl.*)."""
    if key == "battery":
        return t("rpt.f_battery")
    if has(f"f.{key}"):
        return t(f"f.{key}")
    return field_label(key)

if TYPE_CHECKING:
    from .extractor import ExtractionResult


def generate_pdf_report(results: list, device_info: dict, output_dir: str) -> str:
    """Vygeneruje profesionální PDF report z výsledků extrakce."""
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

    # ── Fonty ─────────────────────────────────────────────────────────────────
    FONT_DIR = r"C:\Windows\Fonts"
    _fonts = {"R": "arial.ttf", "B": "arialbd.ttf", "I": "ariali.ttf"}
    _reg = {}
    for alias, fname in _fonts.items():
        fpath = os.path.join(FONT_DIR, fname)
        if os.path.exists(fpath):
            try:
                pdfmetrics.registerFont(TTFont(f"AR{alias}", fpath))
                _reg[alias] = f"AR{alias}"
            except Exception:
                _reg[alias] = "Helvetica-Bold" if alias == "B" else "Helvetica"
        else:
            _reg[alias] = "Helvetica-Bold" if alias == "B" else "Helvetica"

    F  = _reg["R"]
    FB = _reg["B"]

    # ── Barvy ─────────────────────────────────────────────────────────────────
    C_NAVY    = colors.HexColor("#0d1b3e")   # hlavička, tmavá
    C_BLUE    = colors.HexColor("#1565c0")   # akcentní modrá
    C_BLUE_L  = colors.HexColor("#1976d2")   # světlejší modrá pro záhlaví tabulek
    C_GREEN   = colors.HexColor("#1b5e20")
    C_GREEN_L = colors.HexColor("#e8f5e9")
    C_RED     = colors.HexColor("#b71c1c")
    C_RED_L   = colors.HexColor("#ffebee")
    C_MUTED   = colors.HexColor("#546e7a")
    C_WHITE   = colors.white
    C_ROW_ALT = colors.HexColor("#f0f4ff")
    C_ROW_W   = colors.HexColor("#ffffff")
    C_BORDER  = colors.HexColor("#c5cae9")
    C_LABEL   = colors.HexColor("#e8eaf6")
    C_TEXT    = colors.HexColor("#1a1a2e")

    PAGE_W = A4[0] - 4 * cm
    MAX_ROWS_PDF = 500   # limit řádků v PDF (kompletní data → Excel/JSON)

    # ── Styly ─────────────────────────────────────────────────────────────────
    def _style(name, font=None, size=9, color=C_TEXT, bold=False, align=TA_LEFT,
               before=0, after=4, leading=None):
        return ParagraphStyle(
            name, fontName=font or (FB if bold else F),
            fontSize=size, textColor=color,
            alignment=align, spaceBefore=before, spaceAfter=after,
            leading=leading or (size * 1.35),
        )

    s_cover_title = _style("ct", font=FB, size=26, color=C_WHITE, align=TA_LEFT, after=6)
    s_cover_sub   = _style("cs", size=12, color=colors.HexColor("#90caf9"), align=TA_LEFT, after=4)
    s_cover_meta  = _style("cm", size=9,  color=colors.HexColor("#bbdefb"), align=TA_LEFT, after=2)
    s_h2          = _style("h2", font=FB, size=11, color=C_BLUE, before=14, after=5)
    s_body        = _style("bd", size=8,  color=C_MUTED, after=3)
    s_cell        = _style("cl", size=8,  color=C_TEXT, after=0, leading=11)
    s_cell_hdr    = _style("ch", font=FB, size=8, color=C_WHITE, after=0, leading=11)
    s_cell_ok     = _style("co", font=FB, size=8, color=C_GREEN, after=0, leading=11)
    s_cell_err    = _style("ce", font=FB, size=8, color=C_RED,   after=0, leading=11)
    s_stat_num    = _style("sn", font=FB, size=20, color=C_BLUE, align=TA_CENTER, after=2)
    s_stat_lbl    = _style("sl", size=7,  color=C_MUTED, align=TA_CENTER, after=0)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pdf_path  = os.path.join(output_dir, "report.pdf")

    model        = device_info.get("model", "N/A")
    manufacturer = device_info.get("manufacturer", "N/A")
    android      = device_info.get("android", "N/A")
    serial       = device_info.get("serial", "N/A")

    ok_count   = sum(1 for r in results if r.success)
    fail_count = len(results) - ok_count
    total_rec  = sum(r.count for r in results if r.success)

    # ── Dokument ──────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=0, bottomMargin=2*cm,
        title=t("rpt.doc_title"), author="",
    )

    story = []

    # ── Cover header (canvas-drawn dark band) ─────────────────────────────────
    # Simulujeme tmavou hlavičku přes tabulku
    cover_hdr = Table(
        [[Paragraph(t("rpt.title_upper"), s_cover_title)],
         [Paragraph(f"{manufacturer} {model}", s_cover_sub)],
         [Paragraph(f"Android {android}  ·  S/N: {serial}  ·  {timestamp}", s_cover_meta)]],
        colWidths=[PAGE_W + 0*cm],
    )
    cover_hdr.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_NAVY),
        ("TOPPADDING",   (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
        ("LEFTPADDING",  (0, 0), (-1, -1), 18),
        ("RIGHTPADDING", (0, 0), (-1, -1), 18),
        ("LINEBELOW",    (0, -1), (-1, -1), 3, C_BLUE),
    ]))
    story.append(cover_hdr)
    story.append(Spacer(1, 0.7*cm))

    # ── Summary stats (4 karty) ───────────────────────────────────────────────
    def _stat_card(num, label, bg, num_color=None):
        n_style = ParagraphStyle("_sn", fontName=FB, fontSize=20,
                                 textColor=num_color or C_BLUE,
                                 alignment=TA_CENTER, leading=26)
        l_style = ParagraphStyle("_sl", fontName=F, fontSize=7,
                                 textColor=C_MUTED,
                                 alignment=TA_CENTER, leading=10)
        return [[Paragraph(str(num), n_style)], [Paragraph(label, l_style)]]

    stat_w = PAGE_W / 4
    stats_data = [[
        Table(_stat_card(len(results), t("rpt.modules_total"), C_LABEL),          colWidths=[stat_w]),
        Table(_stat_card(ok_count,     t("rpt.ok"),       C_GREEN_L, C_GREEN), colWidths=[stat_w]),
        Table(_stat_card(fail_count,   t("rpt.failed"),   C_RED_L,   C_RED),   colWidths=[stat_w]),
        Table(_stat_card(f"{total_rec:,}", t("rpt.records_total"), C_LABEL),         colWidths=[stat_w]),
    ]]
    for card_tbl in stats_data[0]:
        card_tbl.setStyle(TableStyle([
            ("TOPPADDING",   (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
        ]))

    stats_tbl = Table(stats_data, colWidths=[stat_w]*4)
    stats_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (0, 0), C_LABEL),
        ("BACKGROUND",   (1, 0), (1, 0), C_GREEN_L),
        ("BACKGROUND",   (2, 0), (2, 0), C_RED_L),
        ("BACKGROUND",   (3, 0), (3, 0), C_LABEL),
        ("GRID",         (0, 0), (-1, -1), 0.5, C_BORDER),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(stats_tbl)
    story.append(Spacer(1, 0.6*cm))

    # ── Informace o zařízení ─────────────────────────────────────────────────
    story.append(Paragraph(t("rpt.device_info"), s_h2))

    dev_fields = [
        (t("rpt.f_manufacturer"), device_info.get("manufacturer", "N/A")),
        ("Model",                device_info.get("model", "N/A")),
        (t("rpt.f_android"),      device_info.get("android", "N/A")),
        ("SDK",                  device_info.get("sdk", "N/A")),
        (t("rpt.f_serial"),       device_info.get("serial", "N/A")),
        ("IMEI",                 device_info.get("imei", "N/A")),
        (t("rpt.f_patch"),        device_info.get("security_patch", "N/A")),
        (t("rpt.f_battery"),      device_info.get("battery", "N/A")),
        ("Brand",                device_info.get("brand", "N/A")),
        ("Build ID",             device_info.get("build", "N/A")),
    ]
    dev_rows = []
    for i in range(0, len(dev_fields), 2):
        l = dev_fields[i]
        r2 = dev_fields[i + 1] if i + 1 < len(dev_fields) else ("", "")
        dev_rows.append([
            Paragraph(l[0],  s_cell_hdr),
            Paragraph(l[1],  s_cell),
            Paragraph(r2[0], s_cell_hdr),
            Paragraph(r2[1], s_cell),
        ])

    dev_tbl = Table(dev_rows, colWidths=[3.8*cm, 5.8*cm, 3.8*cm, 5.6*cm], hAlign="LEFT")
    dev_tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (0, -1), C_BLUE_L),
        ("BACKGROUND",     (2, 0), (2, -1), C_BLUE_L),
        ("ROWBACKGROUNDS", (1, 0), (1, -1), [C_LABEL, C_ROW_W]),
        ("ROWBACKGROUNDS", (3, 0), (3, -1), [C_LABEL, C_ROW_W]),
        ("GRID",           (0, 0), (-1, -1), 0.4, C_BORDER),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",     (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 6),
        ("LEFTPADDING",    (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 8),
    ]))
    story.append(dev_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── Souhrn extrakce ───────────────────────────────────────────────────────
    story.append(Paragraph(t("rpt.summary"), s_h2))

    sum_rows = [[
        Paragraph(t("rpt.col_module"),  s_cell_hdr),
        Paragraph(t("rpt.col_status"),  s_cell_hdr),
        Paragraph(t("rpt.col_records"), s_cell_hdr),
        Paragraph(t("rpt.col_note"),    s_cell_hdr),
    ]]
    for r in results:
        stav_s = s_cell_ok if r.success else s_cell_err
        sum_rows.append([
            Paragraph(r.label,                                       s_cell),
            Paragraph("OK" if r.success else t("rpt.status_err"), stav_s),
            Paragraph(f"{r.count:,}" if r.success else "",           s_cell),
            Paragraph("" if r.success else r.error[:80],            s_cell),
        ])

    sum_tbl = Table(sum_rows, colWidths=[6.5*cm, 2*cm, 2.5*cm, PAGE_W-11*cm],
                    hAlign="LEFT", repeatRows=1)
    sum_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE_L),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_ROW_W, C_ROW_ALT]),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
    ]))
    story.append(sum_tbl)
    story.append(Paragraph(t("rpt.generated").format(ts=timestamp), s_body))
    story.append(PageBreak())

    # ── Detailní data modulů ──────────────────────────────────────────────────
    def _data_table(data_list: list) -> Table | None:
        if not data_list:
            return None
        sample = data_list[0]
        # Přeskoč klíče s binárním/URL obsahem pro přehlednost
        keys = [k for k in sample.keys()
                if not any(x in k.lower() for x in ("raw", "blob"))]
        if not keys:
            keys = list(sample.keys())

        col_count = len(keys)
        col_w = [max(1.8*cm, min(PAGE_W / col_count, 8*cm))] * col_count

        rows = [[Paragraph(field_label(k), s_cell_hdr) for k in keys]]
        for rec in data_list[:MAX_ROWS_PDF]:
            rows.append([
                Paragraph(str(rec.get(k, ""))[:180], s_cell)
                for k in keys
            ])

        tbl = Table(rows, colWidths=col_w, repeatRows=1, hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE_L),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_ROW_W, C_ROW_ALT]),
            ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
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

        # Sekční hlavička s modrým levým pruhem (simulace přes tabulku)
        hdr_tbl = Table(
            [[Paragraph(r.label, ParagraphStyle(
                "_sh", fontName=FB, fontSize=11, textColor=C_BLUE,
                leading=14, spaceBefore=0, spaceAfter=0,
            ))]],
            colWidths=[PAGE_W],
        )
        hdr_tbl.setStyle(TableStyle([
            ("LINEBELOW",    (0, 0), (-1, -1), 1.5, C_BLUE),
            ("TOPPADDING",   (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        section.append(hdr_tbl)

        if isinstance(r.data, list) and r.data:
            tbl = _data_table(r.data)
            if tbl:
                section.append(tbl)
            if len(r.data) > MAX_ROWS_PDF:
                section.append(Paragraph(
                    t("rpt.truncated").format(shown=MAX_ROWS_PDF, total=len(r.data)),
                    s_body,
                ))

        elif isinstance(r.data, dict):
            kv_rows = [
                [Paragraph(field_label(k), s_cell_hdr), Paragraph(str(v)[:300], s_cell)]
                for k, v in r.data.items()
            ]
            if kv_rows:
                kv_tbl = Table(kv_rows, colWidths=[5*cm, PAGE_W - 5*cm], hAlign="LEFT")
                kv_tbl.setStyle(TableStyle([
                    ("BACKGROUND",    (0, 0), (0, -1), C_BLUE_L),
                    ("ROWBACKGROUNDS",(1, 0), (1, -1), [C_ROW_W, C_ROW_ALT]),
                    ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
                    ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING",    (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
                ]))
                section.append(kv_tbl)

        section.append(Spacer(1, 8))
        story.append(KeepTogether(section[:2]))
        if len(section) > 2:
            story.extend(section[2:])

    # ── Zápatí ────────────────────────────────────────────────────────────────
    _total_pages = [0]

    def _footer(canvas, doc):
        canvas.saveState()
        w, _ = A4
        y_line = 1.6*cm
        y_text = 1.2*cm
        canvas.setStrokeColor(C_BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(2*cm, y_line, w - 2*cm, y_line)
        canvas.setFont(F, 7.5)
        canvas.setFillColor(C_MUTED)
        canvas.drawString(2*cm, y_text, f"{t('rpt.footer')}  ·  {timestamp}")
        canvas.drawRightString(w - 2*cm, y_text, t("rpt.page").format(n=doc.page))
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

    def _sanitize(v) -> str:
        """Odstraní znaky ilegální v XML 1.0 (openpyxl/xlsx je požaduje čisté)."""
        import re
        s = str(v) if v is not None else ""
        # Zakázané znaky v XML 1.0: U+0000–U+0008, U+000B, U+000C, U+000E–U+001F, U+FFFE, U+FFFF
        return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\uFFFE\uFFFF]", "", s)

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
            c = ws.cell(row=row, column=ci, value=_sanitize(v))
            c.font      = FONT_NORMAL
            c.alignment = ALIGN_LEFT
            c.border    = BORDER
            if alt:
                c.fill = FILL_ROW_ALT

    wb = Workbook()

    # ── List 1: Informace o zařízení ─────────────────────────────────────────
    ws_dev = wb.active
    ws_dev.title = t("rpt.device_sheet")
    ws_dev.merge_cells("A1:B1")
    c = ws_dev["A1"]
    c.value     = t("rpt.device_info")
    c.font      = FONT_TITLE
    c.fill      = FILL_TITLE
    c.alignment = ALIGN_CENTER
    ws_dev.row_dimensions[1].height = 28

    for ri, (k, v) in enumerate(device_info.items(), start=2):
        label = ws_dev.cell(row=ri, column=1, value=_dev_label(k))
        label.font = Font(name="Calibri", size=10, bold=True, color="16213E")
        label.fill = PatternFill("solid", fgColor="E8F4FD")
        label.border = BORDER
        label.alignment = ALIGN_LEFT

        value = ws_dev.cell(row=ri, column=2, value=_sanitize(v))
        value.font      = FONT_NORMAL
        value.border    = BORDER
        value.alignment = ALIGN_LEFT

    ws_dev.column_dimensions["A"].width = 28
    ws_dev.column_dimensions["B"].width = 45

    # ── List 2: Souhrn ────────────────────────────────────────────────────────
    ws_sum = wb.create_sheet(t("rpt.summary")[:31])
    ws_sum.merge_cells("A1:D1")
    c = ws_sum["A1"]
    c.value     = f"{t('rpt.summary')} — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    c.font      = FONT_TITLE
    c.fill      = FILL_TITLE
    c.alignment = ALIGN_CENTER
    ws_sum.row_dimensions[1].height = 28

    _header_row(ws_sum, [t("rpt.col_module"), t("rpt.col_status"), t("rpt.col_count"), t("rpt.col_file")], row=2)
    ws_sum.row_dimensions[2].height = 20

    for ri, r in enumerate(results, start=3):
        alt = ri % 2 == 0
        _data_row(ws_sum, [
            r.label,
            "OK" if r.success else t("rpt.status_err"),
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
            ws_sum.cell(row=ri, column=3).value = _sanitize(r.error)

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
            _header_row(ws, [field_label(k) for k in keys], row=1)
            ws.row_dimensions[1].height = 20

            for ri, row in enumerate(r.data, start=2):
                alt = ri % 2 == 0
                _data_row(ws, [row.get(k, "") for k in keys], ri, alt)

            ws.auto_filter.ref = f"A1:{get_column_letter(len(keys))}1"
            ws.freeze_panes = "A2"
            _auto_width(ws)

        elif isinstance(r.data, dict):
            _header_row(ws, [t("rpt.col_key"), t("rpt.col_value")], row=1)
            for ri, (k, v) in enumerate(r.data.items(), start=2):
                _data_row(ws, [field_label(k), v], ri, ri % 2 == 0)
            _auto_width(ws)

    xlsx_path = os.path.join(output_dir, "report.xlsx")
    wb.save(xlsx_path)
    return xlsx_path


def generate_report(results: list, device_info: dict, output_dir: str) -> str:
    """Vygeneruje profesionální HTML report se stránkováním a vyhledáváním."""
    import json as _json
    import html as _html

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_path = os.path.join(output_dir, "report.html")

    model        = device_info.get("model", "N/A")
    manufacturer = device_info.get("manufacturer", "N/A")
    android      = device_info.get("android", "N/A")
    serial       = device_info.get("serial", "N/A")

    def _safe(v) -> str:
        import re
        s = str(v) if v is not None else ""
        s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)
        return _html.escape(s)

    # Připrav JSON data pro JS engine (všechna data, bez limitu)
    js_modules = []
    for r in results:
        entry = {
            "key":     r.module,
            "label":   r.label,
            "success": r.success,
            "count":   r.count,
            "error":   r.error if not r.success else "",
            "type":    "none",
            "columns": [],
            "rows":    [],
            "kv":      {},
        }
        if r.success and r.data:
            if isinstance(r.data, list) and r.data and isinstance(r.data[0], dict):
                entry["type"]    = "table"
                col_keys = list(r.data[0].keys())
                entry["columns"] = [field_label(k) for k in col_keys]
                entry["rows"]    = [
                    [str(row.get(k, "")) for k in col_keys]
                    for row in r.data
                ]
            elif isinstance(r.data, dict):
                entry["type"] = "kv"
                entry["kv"]   = {field_label(k): str(v) for k, v in r.data.items()}
        js_modules.append(entry)

    js_data = _json.dumps(js_modules, ensure_ascii=False)
    js_tr = _json.dumps({
        "search":     t("rpt.js_search"),
        "no_data":    t("rpt.js_no_data"),
        "error":      t("rpt.js_error"),
        "shown":      t("rpt.js_shown"),
        "prev":       t("rpt.js_prev"),
        "next":       t("rpt.js_next"),
        "records":    t("rpt.js_records"),
        "status_err": t("rpt.status_err"),
    }, ensure_ascii=False)

    # Souhrn pro dashboard
    ok_count   = sum(1 for r in results if r.success)
    fail_count = len(results) - ok_count
    total_rec  = sum(r.count for r in results if r.success)

    # Device info řádky
    device_fields_html = ""
    important = ["manufacturer", "model", "android", "sdk", "serial", "imei",
                 "battery", "storage_total", "storage_free", "security_patch"]
    shown = {k: device_info[k] for k in important if k in device_info}
    for k, v in shown.items():
        label = _dev_label(k)
        device_fields_html += f'<div class="df"><span class="dl">{_safe(label)}</span><span class="dv">{_safe(v)}</span></div>\n'

    html = f"""<!DOCTYPE html>
<html lang="{get_lang()}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_safe(t("rpt.title"))} — {_safe(model)}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#0b0d17;color:#d8dce8;min-height:100vh}}
a{{color:#00d4ff;text-decoration:none}}
/* ── Layout ── */
.wrap{{max-width:1400px;margin:0 auto;padding:28px 24px}}
/* ── Header ── */
.rpt-header{{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:28px;border-bottom:1px solid #1e2a4a;padding-bottom:18px}}
.rpt-title{{font-size:22px;font-weight:700;color:#00d4ff;letter-spacing:.5px}}
.rpt-title span{{color:#fff}}
.rpt-meta{{font-size:11px;color:#556;text-align:right;line-height:1.6}}
/* ── Summary cards ── */
.summary{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:14px;margin-bottom:26px}}
.sc{{background:#131929;border:1px solid #1e2a4a;border-radius:10px;padding:16px 20px}}
.sc .sv{{font-size:26px;font-weight:700;color:#00d4ff}}
.sc .sl{{font-size:11px;color:#556;margin-top:4px;text-transform:uppercase;letter-spacing:.5px}}
.sc.ok .sv{{color:#00c853}}.sc.fail .sv{{color:#ff5252}}.sc.rec .sv{{color:#00d4ff}}
/* ── Device info ── */
.dev-card{{background:#131929;border:1px solid #1e2a4a;border-radius:10px;padding:20px;margin-bottom:26px}}
.dev-card h2{{font-size:12px;color:#556;text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px}}
.dev-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}}
.df{{display:flex;flex-direction:column}}
.dl{{font-size:10px;color:#556;text-transform:uppercase;letter-spacing:.4px;margin-bottom:2px}}
.dv{{font-size:13px;color:#e8ecf8;font-weight:500}}
/* ── Module cards ── */
.mod-card{{background:#131929;border:1px solid #1e2a4a;border-radius:10px;margin-bottom:14px;overflow:hidden}}
.mod-hdr{{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;cursor:pointer;user-select:none;background:#161d30;transition:background .15s}}
.mod-hdr:hover{{background:#1a2440}}
.mod-hdr-left{{display:flex;align-items:center;gap:10px}}
.mod-icon{{font-size:18px;width:28px;text-align:center}}
.mod-name{{font-size:14px;font-weight:600;color:#c8d4f0}}
.mod-badge{{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600}}
.badge-ok{{background:#003d1a;color:#00c853}}.badge-fail{{background:#3d0000;color:#ff5252}}
.mod-count{{font-size:12px;color:#556;margin-left:8px}}
.chevron{{font-size:10px;color:#556;transition:transform .2s}}
.mod-body{{display:none;padding:0 16px 16px}}
.mod-body.open{{display:block}}
/* ── Table toolbar ── */
.tbl-toolbar{{display:flex;align-items:center;gap:10px;padding:12px 0 8px;flex-wrap:wrap}}
.search-box{{flex:1;min-width:180px;max-width:340px;background:#0d1120;border:1px solid #2a3a5a;border-radius:6px;padding:6px 12px;color:#d8dce8;font-size:12px;outline:none}}
.search-box:focus{{border-color:#00d4ff}}
.page-info{{font-size:12px;color:#556;margin-left:auto}}
.pager{{display:flex;gap:4px}}
.pager button{{background:#1a2440;border:1px solid #2a3a5a;color:#99aac8;border-radius:5px;padding:4px 10px;font-size:12px;cursor:pointer}}
.pager button:hover{{background:#243050;color:#fff}}
.pager button.active{{background:#00d4ff;color:#000;border-color:#00d4ff;font-weight:700}}
.pager button:disabled{{opacity:.35;cursor:default}}
/* ── Data table ── */
.tbl-wrap{{overflow-x:auto;border-radius:6px;border:1px solid #1e2a4a}}
table.dt{{width:100%;border-collapse:collapse;font-size:12px;min-width:400px}}
table.dt thead tr{{background:#0d1120}}
table.dt th{{padding:9px 12px;color:#00d4ff;font-weight:600;text-align:left;white-space:nowrap;border-bottom:1px solid #1e2a4a;cursor:pointer;user-select:none}}
table.dt th:hover{{color:#fff}}
table.dt th .sort-arrow{{color:#2a3a5a;font-size:9px;margin-left:3px}}
table.dt th.sort-asc .sort-arrow::after{{content:'▲';color:#00d4ff}}
table.dt th.sort-desc .sort-arrow::after{{content:'▼';color:#00d4ff}}
table.dt th:not(.sort-asc):not(.sort-desc) .sort-arrow::after{{content:'⇅'}}
table.dt td{{padding:7px 12px;border-bottom:1px solid #0f1828;vertical-align:top;max-width:340px;overflow-wrap:break-word}}
table.dt tr:nth-child(even) td{{background:#10172a}}
table.dt tr:hover td{{background:#1a2440}}
table.dt td mark{{background:#00d4ff22;color:#00d4ff;border-radius:2px;padding:0 2px}}
/* ── KV table ── */
table.kv{{width:100%;border-collapse:collapse;font-size:13px}}
table.kv td{{padding:8px 14px;border-bottom:1px solid #0f1828;vertical-align:top}}
table.kv tr:nth-child(even) td{{background:#10172a}}
table.kv td:first-child{{color:#00d4ff;font-weight:600;width:220px;white-space:nowrap}}
/* ── No data ── */
.no-data{{padding:24px;text-align:center;color:#556;font-size:13px}}
/* ── Footer ── */
footer{{margin-top:36px;padding-top:18px;border-top:1px solid #1e2a4a;text-align:center;color:#334;font-size:11px;line-height:1.8}}
</style>
</head>
<body>
<div class="wrap">

<div class="rpt-header">
  <div>
    <div class="rpt-title">{_safe(t("rpt.title"))}</div>
    <div style="font-size:12px;color:#556;margin-top:4px">{_safe(manufacturer)} {_safe(model)} &nbsp;·&nbsp; Android {_safe(android)} &nbsp;·&nbsp; S/N: {_safe(serial)}</div>
  </div>
  <div class="rpt-meta">{_safe(t("rpt.generated").format(ts=timestamp))}</div>
</div>

<div class="summary">
  <div class="sc ok"><div class="sv">{ok_count}</div><div class="sl">{_safe(t("rpt.ok_modules"))}</div></div>
  <div class="sc fail"><div class="sv">{fail_count}</div><div class="sl">{_safe(t("rpt.failed_modules"))}</div></div>
  <div class="sc rec"><div class="sv">{total_rec:,}</div><div class="sl">{_safe(t("rpt.records_total"))}</div></div>
  <div class="sc"><div class="sv">{len(results)}</div><div class="sl">{_safe(t("rpt.modules_total"))}</div></div>
</div>

<div class="dev-card">
  <h2>{_safe(t("rpt.device_info"))}</h2>
  <div class="dev-grid">
{device_fields_html}  </div>
</div>

<div id="modules"></div>

<footer>
  {_safe(t("rpt.footer"))}<br>
  {_safe(t("rpt.generated").format(ts=timestamp))}
</footer>

</div><!-- .wrap -->

<script>
const MODULES = {js_data};
const PAGE_SIZE = 50;
const TR = {js_tr};

const ICONS = {{
  device_info:"📋",contacts:"👥",sms:"💬",call_logs:"📞",apps:"📦",
  media:"🖼",documents:"📄",wifi:"📶",accounts:"👤",browser:"🌐",
  user_data_tree:"🗂",file_analysis:"🔍",file_hashes:"#️",deleted_data:"🗑",
  adb_backup:"💾",root_backup:"🔓",app_providers:"🔗",usage_stats:"📊",termux_agent:"🤖"
}};

const state = {{}};   // keyed by module index: {{ page, filter, sortCol, sortDir }}

function esc(s) {{
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}}

function highlight(s, q) {{
  if (!q) return esc(s);
  const re = new RegExp('(' + q.replace(/[.*+?^${{}}()|[\\]\\\\]/g,'\\\\$&') + ')', 'gi');
  return esc(s).replace(re, '<mark>$1</mark>');
}}

function getState(i) {{
  if (!state[i]) state[i] = {{ page: 0, filter: "", sortCol: -1, sortDir: 1 }};
  return state[i];
}}

// Inicializuje strukturu modulu jednou (search box, thead, kv) – nesmí se volat opakovaně
function initModule(i) {{
  const m = MODULES[i];
  const bodyEl = document.getElementById('mb-' + i);
  if (!bodyEl) return;

  if (m.type === 'table') {{
    // Sestavíme thead HTML
    let theadHtml = '';
    m.columns.forEach((col, ci) => {{
      theadHtml += `<th id="th-${{i}}-${{ci}}" onclick="sortTable(${{i}},${{ci}})">${{esc(col)}}<span class="sort-arrow"></span></th>`;
    }});
    bodyEl.innerHTML = `
      <div class="tbl-toolbar">
        <input class="search-box" id="search-${{i}}" placeholder="${{esc(TR.search)}}"
               oninput="filterTable(${{i}},this.value)">
        <span class="page-info" id="pinfo-${{i}}"></span>
        <div class="pager" id="pager-${{i}}"></div>
      </div>
      <div class="tbl-wrap">
        <table class="dt">
          <thead><tr id="thead-${{i}}">${{theadHtml}}</tr></thead>
          <tbody id="tbody-${{i}}"></tbody>
        </table>
      </div>`;
    renderTable(i);

  }} else if (m.type === 'kv') {{
    let html = '<div class="tbl-wrap"><table class="kv">';
    for (const [k, v] of Object.entries(m.kv))
      html += `<tr><td>${{esc(k)}}</td><td>${{esc(v)}}</td></tr>`;
    html += '</table></div>';
    bodyEl.innerHTML = html;

  }} else {{
    const msg = m.success ? esc(TR.no_data) : `${{esc(TR.error)}}: ${{esc(m.error)}}`;
    bodyEl.innerHTML = `<div class="no-data">${{msg}}</div>`;
  }}
}}

// Aktualizuje pouze tbody + paginaci – search box se nedotýká
function renderTable(i) {{
  const m = MODULES[i];
  const s = getState(i);

  let rows = m.rows;
  if (s.filter) {{
    const q = s.filter.toLowerCase();
    rows = rows.filter(r => r.some(c => c.toLowerCase().includes(q)));
  }}
  if (s.sortCol >= 0) {{
    rows = [...rows].sort((a, b) => {{
      const av = a[s.sortCol], bv = b[s.sortCol];
      return av < bv ? -s.sortDir : av > bv ? s.sortDir : 0;
    }});
  }}

  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  s.page = Math.min(s.page, pages - 1);
  const slice = rows.slice(s.page * PAGE_SIZE, (s.page + 1) * PAGE_SIZE);

  // Aktualizuj záhlaví sloupců (třída řazení)
  m.columns.forEach((_, ci) => {{
    const th = document.getElementById(`th-${{i}}-${{ci}}`);
    if (!th) return;
    th.className = s.sortCol===ci ? (s.sortDir===1 ? 'sort-asc' : 'sort-desc') : '';
  }});

  // Aktualizuj tbody
  const tbody = document.getElementById('tbody-' + i);
  if (tbody) {{
    const q = s.filter;
    tbody.innerHTML = slice.map(row =>
      '<tr>' + row.map(c => `<td>${{highlight(c,q)}}</td>`).join('') + '</tr>'
    ).join('');
  }}

  // Aktualizuj info + paginaci
  const pinfo = document.getElementById('pinfo-' + i);
  if (pinfo) {{
    const from = total === 0 ? 0 : s.page * PAGE_SIZE + 1;
    const to   = Math.min((s.page + 1) * PAGE_SIZE, total);
    pinfo.textContent = TR.shown.replace('{{from}}', from).replace('{{to}}', to).replace('{{total}}', total);
  }}

  const pagerEl = document.getElementById('pager-' + i);
  if (pagerEl) {{
    let p = '';
    p += `<button onclick="changePage(${{i}},${{s.page-1}})" ${{s.page===0?'disabled':''}}>${{esc(TR.prev)}}</button>`;
    const lo = Math.max(0, s.page-2), hi = Math.min(pages-1, s.page+2);
    if (lo>0) {{ p += `<button onclick="changePage(${{i}},0)">1</button>`; if (lo>1) p += '<span style="color:#556;padding:0 4px">…</span>'; }}
    for (let pg=lo; pg<=hi; pg++)
      p += `<button class="${{pg===s.page?'active':''}}" onclick="changePage(${{i}},${{pg}})">${{pg+1}}</button>`;
    if (hi<pages-1) {{ if (hi<pages-2) p += '<span style="color:#556;padding:0 4px">…</span>'; p += `<button onclick="changePage(${{i}},${{pages-1}})">${{pages}}</button>`; }}
    p += `<button onclick="changePage(${{i}},${{s.page+1}})" ${{s.page>=pages-1?'disabled':''}}>${{esc(TR.next)}}</button>`;
    pagerEl.innerHTML = p;
  }}
}}

function changePage(i, p) {{ getState(i).page = p; renderTable(i); }}
function filterTable(i, v) {{ const s=getState(i); s.filter=v; s.page=0; renderTable(i); }}
function sortTable(i, col) {{
  const s = getState(i);
  if (s.sortCol === col) s.sortDir *= -1; else {{ s.sortCol=col; s.sortDir=1; }}
  s.page = 0;
  renderTable(i);
}}

function toggleModule(i) {{
  const body = document.getElementById('mb-' + i);
  const chev = document.getElementById('chev-' + i);
  body.classList.toggle('open');
  chev.style.transform = body.classList.contains('open') ? 'rotate(180deg)' : '';
  if (body.classList.contains('open') && !body.innerHTML.trim()) initModule(i);
}}

// Build module list
(function() {{
  const container = document.getElementById('modules');
  MODULES.forEach((m, i) => {{
    const icon = ICONS[m.key] || '📁';
    const badge = m.success
      ? `<span class="mod-badge badge-ok">OK</span><span class="mod-count">${{m.count.toLocaleString()}} ${{esc(TR.records)}}</span>`
      : `<span class="mod-badge badge-fail">${{esc(TR.status_err)}}</span>`;
    container.innerHTML += `
    <div class="mod-card">
      <div class="mod-hdr" onclick="toggleModule(${{i}})">
        <div class="mod-hdr-left">
          <span class="mod-icon">${{icon}}</span>
          <span class="mod-name">${{esc(m.label)}}</span>
          ${{badge}}
        </div>
        <span class="chevron" id="chev-${{i}}">▼</span>
      </div>
      <div class="mod-body" id="mb-${{i}}"></div>
    </div>`;
  }});
  // Otevři první úspěšný modul
  const first = MODULES.findIndex(m => m.success && m.type !== 'none');
  if (first >= 0) toggleModule(first);

}})();
</script>
</body>
</html>"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)

    return report_path
