import os
import re
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


def _report_asset(*parts) -> str:
    """Absolute path to a file in assets/ (works from source and from the .exe)."""
    import sys
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base, "assets", *parts)


def generate_pdf_report(results: list, device_info: dict, output_dir: str,
                        case_meta: dict | None = None) -> str:
    """Professional forensic PDF report: cover page with logos and case details,
    clickable table of contents, PDF outline (bookmarks), header and footer on every page.
    The text is real → full-text search (Ctrl+F) works."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.platypus import (
        BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
        Image, PageBreak, KeepTogether, FrameBreak, NextPageTemplate,
    )
    from reportlab.platypus.tableofcontents import TableOfContents
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as _canvas

    case_meta = case_meta or {}

    # ── Fonty ─────────────────────────────────────────────────────────────────
    FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    _fonts = {"R": "arial.ttf", "B": "arialbd.ttf", "I": "ariali.ttf"}
    _reg = {}
    for alias, fname in _fonts.items():
        fpath = os.path.join(FONT_DIR, fname)
        fallback = "Helvetica-Bold" if alias == "B" else ("Helvetica-Oblique" if alias == "I" else "Helvetica")
        if os.path.exists(fpath):
            try:
                pdfmetrics.registerFont(TTFont(f"AR{alias}", fpath))
                _reg[alias] = f"AR{alias}"
            except Exception:
                _reg[alias] = fallback
        else:
            _reg[alias] = fallback
    F, FB, FI = _reg["R"], _reg["B"], _reg["I"]

    # ── Colour palette (professional, dark blue) ──────────────────────────────
    C_NAVY    = colors.HexColor("#0d2149")   # cover background, section headers
    C_BLUE    = colors.HexColor("#1f5fb0")   # akcent
    C_BLUE_L  = colors.HexColor("#2a6fc4")   # table headers
    C_ACCENT  = colors.HexColor("#00a3cc")   # cyan accent bar (as in the app)
    C_GREEN   = colors.HexColor("#1b7a3d")
    C_GREEN_L = colors.HexColor("#e6f4ea")
    C_RED     = colors.HexColor("#b3261e")
    C_RED_L   = colors.HexColor("#fdecea")
    C_MUTED   = colors.HexColor("#5a6b82")
    C_ROW_ALT = colors.HexColor("#eef3fb")
    C_ROW_W   = colors.white
    C_BORDER  = colors.HexColor("#cdd8ea")
    C_LABEL   = colors.HexColor("#e9eefa")
    C_TEXT    = colors.HexColor("#1a2233")
    C_WHITE   = colors.white

    MARGIN   = 1.8 * cm
    PAGE_W, PAGE_H = A4
    CONTENT_W = PAGE_W - 2 * MARGIN
    HEADER_H = 1.25 * cm    # band for the header on content pages
    FOOTER_H = 1.2 * cm
    MAX_ROWS_PDF = 500
    MAX_THUMBS_PDF = 400          # photo previews per module (keeps PDF size reasonable)
    PDF_IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
    try:                          # HEIC/HEIF (iPhone photos) only if pillow-heif is installed
        import pillow_heif
        pillow_heif.register_heif_opener()
        PDF_IMG_EXT |= {".heic", ".heif"}
    except Exception:
        pass
    from xml.sax.saxutils import escape as _xml_escape

    timestamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    pdf_path  = os.path.join(output_dir, "report.pdf")

    manufacturer = device_info.get("manufacturer", "N/A")
    model        = device_info.get("model", "N/A")
    android      = device_info.get("android", "N/A")
    serial       = device_info.get("serial", "N/A")
    imei         = device_info.get("imei", "N/A")
    device_label = f"{manufacturer} {model}".strip() or "N/A"

    # Settings (report branding) – examiner/organisation are taken from the settings
    # if missing in case_meta; the company logo and block switches come from Settings.
    from . import settings as _settings
    _cfg = _settings.load()

    case_no   = case_meta.get("case_number") or ""
    evidence  = case_meta.get("evidence_id") or ""
    examiner  = case_meta.get("examiner") or _cfg.get("report_examiner", "")
    org       = case_meta.get("organization") or _cfg.get("report_organization", "")
    case_note = case_meta.get("note") or ""
    no_case   = t("rpt.no_case")

    ok_count   = sum(1 for r in results if r.success)
    fail_count = len(results) - ok_count
    total_rec  = sum(r.count for r in results if r.success)

    # ── Styly ─────────────────────────────────────────────────────────────────
    def _st(name, font=F, size=9, color=C_TEXT, align=TA_LEFT, before=0, after=4,
            leading=None):
        return ParagraphStyle(name, fontName=font, fontSize=size, textColor=color,
                              alignment=align, spaceBefore=before, spaceAfter=after,
                              leading=leading or size * 1.32)

    s_body   = _st("bd", size=8.5, color=C_MUTED, after=3)
    s_cell   = _st("cl", size=8, color=C_TEXT, after=0, leading=11)
    s_hdr    = _st("ch", font=FB, size=8, color=C_WHITE, after=0, leading=11)
    s_ok     = _st("co", font=FB, size=8, color=C_GREEN, after=0, leading=11)
    s_err    = _st("ce", font=FB, size=8, color=C_RED, after=0, leading=11)
    # Section headings – afterFlowable puts them into the TOC/outline by style name.
    # keepWithNext keeps a heading with the following table (no KeepTogether, which
    # breaks pagination and the TOC for large tables at a page boundary).
    s_sec0   = _st("SectionH0", font=FB, size=13, color=C_NAVY, before=2, after=8, leading=16)
    s_sec1   = _st("SectionH1", font=FB, size=11, color=C_BLUE, before=2, after=6, leading=14)
    s_sec0.keepWithNext = True
    s_sec1.keepWithNext = True

    # ── Document: page templates + TOC/outline ────────────────────────────────
    _bm = {"n": 0}

    _total = {"n": 0}

    class _ReportDoc(BaseDocTemplate):
        def beforeDocument(self):
            # Reset the counter at the start of every multiBuild pass → section
            # keys are stable and the table of contents (TOC) converges.
            _bm["n"] = 0

        def afterFlowable(self, flowable):
            if not isinstance(flowable, Paragraph):
                return
            sn = flowable.style.name
            if sn not in ("SectionH0", "SectionH1"):
                return
            level = 0 if sn == "SectionH0" else 1
            text = flowable.getPlainText()
            _bm["n"] += 1
            key = f"sec{_bm['n']}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=(level == 0))
            self.notify("TOCEntry", (level, text, self.page, key))

    cover_frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN,
                        id="cover", leftPadding=0, rightPadding=0,
                        topPadding=0, bottomPadding=0)
    content_frame = Frame(MARGIN, MARGIN + FOOTER_H - 0.4 * cm, CONTENT_W,
                          PAGE_H - 2 * MARGIN - HEADER_H - FOOTER_H + 0.8 * cm,
                          id="content", leftPadding=0, rightPadding=0,
                          topPadding=0, bottomPadding=0)

    # ── Header + footer (inline via onPage → keeps bookmarks/links) ───────────
    header_line = []
    if case_no:
        header_line.append(f"{t('rpt.hdr_case')}: {case_no}")
    if evidence:
        header_line.append(f"{t('rpt.hdr_evidence')}: {evidence}")
    header_line.append(device_label)
    header_text = "    ·    ".join(header_line)
    from .version import __version__
    footer_left = f"ForenziQ Toolkit v{__version__}  ·  {timestamp}"

    def _decorate(canvas, docu):
        page_num = canvas.getPageNumber()
        # Total page count = maximum across multiBuild passes. Once the TOC
        # converges the count is stable, so every page of the final pass gets
        # the correct "N" (filled in from the previous pass).
        _total["n"] = max(_total["n"], page_num)
        if page_num == 1:
            return   # cover page without header/footer
        canvas.saveState()
        canvas.setFont(F, 7.5)
        canvas.setFillColor(C_MUTED)
        canvas.drawString(MARGIN, PAGE_H - MARGIN + 0.25 * cm, header_text[:150])
        canvas.setStrokeColor(C_BORDER)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, PAGE_H - MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN)
        yl = MARGIN - 0.1 * cm
        canvas.line(MARGIN, yl + 0.35 * cm, PAGE_W - MARGIN, yl + 0.35 * cm)
        canvas.setFont(F, 7.5)
        canvas.setFillColor(C_MUTED)
        canvas.drawString(MARGIN, yl, footer_left)
        total = _total["n"] or page_num
        canvas.drawRightString(PAGE_W - MARGIN, yl,
                               t("rpt.page_of").format(n=page_num - 1, total=total - 1))
        canvas.restoreState()

    doc = _ReportDoc(pdf_path, pagesize=A4,
                     leftMargin=MARGIN, rightMargin=MARGIN,
                     topMargin=MARGIN, bottomMargin=MARGIN,
                     title=t("rpt.doc_title"), author="ForenziQ Toolkit")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame]),
        PageTemplate(id="content", frames=[content_frame], onPage=_decorate),
    ])

    # ══════════════════════════════════════════════════════════════════════════
    # COVER PAGE
    # ══════════════════════════════════════════════════════════════════════════
    story = []

    def _logo(path, max_w, max_h):
        if not os.path.exists(path):
            return None
        try:
            img = Image(path)
            iw, ih = img.imageWidth, img.imageHeight
            sc = min(max_w / iw, max_h / ih)
            img.drawWidth, img.drawHeight = iw * sc, ih * sc
            return img
        except Exception:
            return None

    sw_logo = None
    if _cfg.get("report_show_tool_logo", True):
        sw_logo = _logo(_report_asset("forenziq_256.png"), 1.5 * cm, 1.5 * cm)

    company_logo = None
    if _cfg.get("report_show_company_logo", True):
        _cpath = _cfg.get("report_company_logo", "") or _report_asset("company_logo.png")
        company_logo = _logo(_cpath, 4.5 * cm, 1.6 * cm)

    s_brand = _st("brand", font=FB, size=15, color=C_NAVY, after=0, leading=17)
    s_brandsub = _st("brandsub", size=7.5, color=C_MUTED, after=0, leading=10)

    brand_cell = [[sw_logo or Paragraph("", s_body),
                   Paragraph("ForenziQ <font color='#00a3cc'>Toolkit</font><br/>"
                             "<font size=7 color='#5a6b82'>Android Forensic Suite</font>",
                             s_brand)]]
    brand_tbl = Table(brand_cell, colWidths=[1.7 * cm, 6 * cm])
    brand_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 6),
    ]))
    header_row = Table(
        [[brand_tbl, company_logo or Paragraph("", s_brandsub)]],
        colWidths=[CONTENT_W - 4.8 * cm, 4.8 * cm],
    )
    header_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 1, C_BORDER),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(header_row)
    story.append(Spacer(1, 3.2 * cm))

    # Large title
    story.append(Paragraph(t("rpt.title_upper"),
                           _st("ctitle", font=FB, size=30, color=C_NAVY, after=4, leading=34)))
    story.append(Paragraph(t("rpt.cover_subtitle"),
                           _st("csub", size=12.5, color=C_BLUE, after=0, leading=16)))
    # cyan separator
    sep = Table([[""]], colWidths=[4 * cm], rowHeights=[3])
    sep.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), C_ACCENT)]))
    story.append(Spacer(1, 10))
    story.append(sep)
    story.append(Spacer(1, 1.4 * cm))

    def _cover_block(title, rows):
        head = Paragraph(title, _st("cbh", font=FB, size=10, color=C_WHITE, after=0, leading=14))
        head_tbl = Table([[head]], colWidths=[CONTENT_W])
        head_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_NAVY),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        body_rows = []
        for lbl, val in rows:
            body_rows.append([
                Paragraph(lbl, _st("cbl", font=FB, size=8.5, color=C_NAVY, after=0, leading=12)),
                Paragraph(str(val) if val not in ("", None) else no_case,
                          _st("cbv", size=8.5, color=C_TEXT, after=0, leading=12)),
            ])
        body_tbl = Table(body_rows, colWidths=[4.5 * cm, CONTENT_W - 4.5 * cm])
        body_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), C_LABEL),
            ("ROWBACKGROUNDS", (1, 0), (1, -1), [C_ROW_W, C_ROW_ALT]),
            ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ]))
        return KeepTogether([head_tbl, body_tbl, Spacer(1, 0.7 * cm)])

    if _cfg.get("report_show_case_details", True):
        story.append(_cover_block(t("rpt.case_details"), [
            (t("ev.f_case"), case_no),
            (t("ev.f_evidence_id"), evidence),
            (t("ev.f_examiner"), examiner),
            (t("ev.f_org"), org),
            (t("ev.f_note"), case_note),
        ]))
    story.append(_cover_block(t("rpt.device_summary"), [
        (t("rpt.f_manufacturer"), manufacturer),
        ("Model", model),
        (t("rpt.f_android"), android),
        (t("rpt.f_serial"), serial),
        ("IMEI", imei),
    ]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(t("rpt.generated").format(ts=timestamp),
                           _st("cgen", size=8, color=C_MUTED, after=0)))

    story.append(NextPageTemplate("content"))
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # OBSAH
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph(t("rpt.toc"),
                           _st("toch", font=FB, size=16, color=C_NAVY, after=12, leading=20)))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("toc0", fontName=FB, fontSize=10, textColor=C_NAVY,
                       leading=18, leftIndent=4, firstLineIndent=0),
        ParagraphStyle("toc1", fontName=F, fontSize=9, textColor=C_TEXT,
                       leading=15, leftIndent=22, firstLineIndent=0),
    ]
    story.append(toc)
    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Device information
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph(t("rpt.device_info"), s_sec0))

    stat_w = CONTENT_W / 4

    def _stat(num, label, num_color):
        return Table([[Paragraph(str(num), _st("sn", font=FB, size=19, color=num_color,
                                                align=TA_CENTER, after=2, leading=24))],
                      [Paragraph(label, _st("sl", size=7.5, color=C_MUTED, align=TA_CENTER,
                                            after=0, leading=10))]],
                     colWidths=[stat_w])

    stats = Table([[_stat(len(results), t("rpt.modules_total"), C_BLUE),
                    _stat(ok_count, t("rpt.ok"), C_GREEN),
                    _stat(fail_count, t("rpt.failed"), C_RED),
                    _stat(f"{total_rec:,}", t("rpt.records_total"), C_BLUE)]],
                   colWidths=[stat_w] * 4)
    stats.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), C_LABEL),
        ("BACKGROUND", (1, 0), (1, 0), C_GREEN_L),
        ("BACKGROUND", (2, 0), (2, 0), C_RED_L),
        ("BACKGROUND", (3, 0), (3, 0), C_LABEL),
        ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(stats)
    story.append(Spacer(1, 0.5 * cm))

    dev_fields = [
        (t("rpt.f_manufacturer"), manufacturer), ("Model", model),
        (t("rpt.f_android"), android), ("SDK", device_info.get("sdk", "N/A")),
        (t("rpt.f_serial"), serial), ("IMEI", imei),
        (t("rpt.f_patch"), device_info.get("security_patch", "N/A")),
        (t("rpt.f_battery"), device_info.get("battery", "N/A")),
        ("Brand", device_info.get("brand", "N/A")),
        ("Build ID", device_info.get("build", "N/A")),
    ]
    dev_rows = []
    for i in range(0, len(dev_fields), 2):
        l = dev_fields[i]
        r2 = dev_fields[i + 1] if i + 1 < len(dev_fields) else ("", "")
        dev_rows.append([Paragraph(l[0], s_hdr), Paragraph(str(l[1]), s_cell),
                         Paragraph(r2[0], s_hdr), Paragraph(str(r2[1]), s_cell)])
    dev_tbl = Table(dev_rows, colWidths=[3.6 * cm, CONTENT_W / 2 - 3.6 * cm,
                                         3.6 * cm, CONTENT_W / 2 - 3.6 * cm], hAlign="LEFT")
    dev_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), C_BLUE_L), ("BACKGROUND", (2, 0), (2, -1), C_BLUE_L),
        ("ROWBACKGROUNDS", (1, 0), (1, -1), [C_LABEL, C_ROW_W]),
        ("ROWBACKGROUNDS", (3, 0), (3, -1), [C_LABEL, C_ROW_W]),
        ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(dev_tbl)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Extraction summary
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph(t("rpt.summary"), s_sec0))
    sum_rows = [[Paragraph(t("rpt.col_module"), s_hdr), Paragraph(t("rpt.col_status"), s_hdr),
                 Paragraph(t("rpt.col_records"), s_hdr), Paragraph(t("rpt.col_note"), s_hdr)]]
    for r in results:
        sum_rows.append([
            Paragraph(r.label, s_cell),
            Paragraph("OK" if r.success else t("rpt.status_err"), s_ok if r.success else s_err),
            Paragraph(f"{r.count:,}" if r.success else "", s_cell),
            Paragraph("" if r.success else r.error[:80], s_cell),
        ])
    sum_tbl = Table(sum_rows, colWidths=[6.5 * cm, 2 * cm, 2.5 * cm, CONTENT_W - 11 * cm],
                    hAlign="LEFT", repeatRows=1)
    sum_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_BLUE_L),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_ROW_W, C_ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(sum_tbl)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION: Extracted data (parent) + individual modules (children)
    # ══════════════════════════════════════════════════════════════════════════
    def _data_table(data_list):
        if not data_list:
            return None
        sample = data_list[0]
        keys = [k for k in sample.keys() if not any(x in k.lower() for x in ("raw", "blob"))]
        if not keys:
            keys = list(sample.keys())
        n = len(keys)
        col_w = [max(1.8 * cm, min(CONTENT_W / n, 8 * cm))] * n
        rows = [[Paragraph(field_label(k), s_hdr) for k in keys]]
        for rec in data_list[:MAX_ROWS_PDF]:
            rows.append([Paragraph(str(rec.get(k, ""))[:180], s_cell) for k in keys])
        tbl = Table(rows, colWidths=col_w, repeatRows=1, hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_BLUE_L),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_ROW_W, C_ROW_ALT]),
            ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return tbl

    # ── Photo previews (thumbnail grid) ───────────────────────────────────────
    # Thumbnails are downscaled JPEGs written to a temp folder (keeps the PDF small
    # and lets multiBuild re-read them on every pass); the folder is removed after
    # the build. Each photo is shown only once even if several modules list it.
    import tempfile, shutil
    thumb_dir = tempfile.mkdtemp(prefix="forenziq_thumbs_")
    shown_photos: set = set()
    THUMB_COLS = 4
    THUMB_W = CONTENT_W / THUMB_COLS
    THUMB_BOX_W, THUMB_BOX_H = THUMB_W - 0.4 * cm, 3.3 * cm
    s_thumb = _st("thc", size=6.5, color=C_MUTED, align=TA_CENTER, after=0, leading=8)
    s_gal = _st("gal", font=FB, size=9, color=C_NAVY, before=8, after=4, leading=12)
    s_gal.keepWithNext = True

    def _collect_photos(r):
        """Local image files belonging to a result: the folder the module copied
        media into, or local paths stored in its records."""
        found = []
        fp = getattr(r, "file_path", "") or ""
        if fp and os.path.isdir(fp):
            for root, _dirs, names in os.walk(fp):
                for name in sorted(names):
                    if os.path.splitext(name)[1].lower() in PDF_IMG_EXT:
                        found.append((os.path.join(root, name), name))
        elif isinstance(r.data, list):
            for rec in r.data:
                if not isinstance(rec, dict):
                    continue
                for k in ("local_path", "path", "_local"):
                    p = rec.get(k)
                    if (isinstance(p, str) and os.path.splitext(p)[1].lower() in PDF_IMG_EXT
                            and os.path.isfile(p)):
                        found.append((p, rec.get("filename") or os.path.basename(p)))
                        break
        out = []
        for p, name in found:
            key = os.path.normcase(os.path.abspath(p))
            if key not in shown_photos:
                shown_photos.add(key)
                out.append((p, name))
        return out

    def _thumb(path):
        """Downscaled, orientation-corrected JPEG copy; None if it cannot be decoded."""
        try:
            from PIL import Image as PILImage, ImageOps
            with PILImage.open(path) as im:
                im.draft("RGB", (480, 480))
                im = ImageOps.exif_transpose(im)
                im = im.convert("RGB")
                im.thumbnail((480, 480))
                out = os.path.join(thumb_dir, f"t{len(os.listdir(thumb_dir))}.jpg")
                im.save(out, "JPEG", quality=72)
                w, h = im.size
            sc = min(THUMB_BOX_W / w, THUMB_BOX_H / h)
            return Image(out, width=w * sc, height=h * sc)
        except Exception:
            return None

    def _gallery(photos):
        total = len(photos)
        cells = []
        for p, name in photos[:MAX_THUMBS_PDF]:
            img = _thumb(p) or Paragraph(t("rpt.no_preview"), s_thumb)
            # middle ellipsis keeps the extension and fits one line under the thumbnail
            cap = name if len(name) <= 30 else name[:19] + "…" + name[-10:]
            cells.append([img, Spacer(1, 2), Paragraph(_xml_escape(cap), s_thumb)])
        if not cells:
            return []
        while len(cells) % THUMB_COLS:
            cells.append("")
        rows = [cells[i:i + THUMB_COLS] for i in range(0, len(cells), THUMB_COLS)]
        tbl = Table(rows, colWidths=[THUMB_W] * THUMB_COLS, hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        out = [Paragraph(t("rpt.gallery").format(n=total), s_gal), tbl]
        if total > MAX_THUMBS_PDF:
            out.append(Paragraph(t("rpt.gallery_truncated").format(
                shown=MAX_THUMBS_PDF, total=total), s_body))
        return out

    data_results = [r for r in results if r.success and r.data]
    if data_results:
        story.append(PageBreak())
        story.append(Paragraph(t("rpt.sec_data"), s_sec0))

    for r in data_results:
        head = Paragraph(r.label, s_sec1)
        block = [head]
        if isinstance(r.data, list) and r.data:
            tbl = _data_table(r.data)
            if tbl:
                block.append(tbl)
            if len(r.data) > MAX_ROWS_PDF:
                block.append(Paragraph(
                    t("rpt.truncated").format(shown=MAX_ROWS_PDF, total=len(r.data)), s_body))
        elif isinstance(r.data, dict):
            kv = [[Paragraph(field_label(k), s_hdr), Paragraph(str(v)[:300], s_cell)]
                  for k, v in r.data.items()]
            if kv:
                kv_tbl = Table(kv, colWidths=[5 * cm, CONTENT_W - 5 * cm], hAlign="LEFT")
                kv_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (0, -1), C_BLUE_L),
                    ("ROWBACKGROUNDS", (1, 0), (1, -1), [C_ROW_W, C_ROW_ALT]),
                    ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]))
                block.append(kv_tbl)
        block.extend(_gallery(_collect_photos(r)))
        # keepWithNext keeps the module heading with its table (no KeepTogether → stable
        # pagination and TOC).
        story.extend(block)
        story.append(Spacer(1, 10))

    try:
        doc.multiBuild(story)
    finally:
        shutil.rmtree(thumb_dir, ignore_errors=True)
    return pdf_path
def generate_excel_report(results: list, device_info: dict, output_dir: str) -> str:
    """Generate an Excel workbook (.xlsx) from the extraction results."""
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
        """Remove characters that are illegal in XML 1.0 (openpyxl/xlsx requires clean text)."""
        import re
        s = str(v) if v is not None else ""
        # Characters illegal in XML 1.0: U+0000–U+0008, U+000B, U+000C, U+000E–U+001F, U+FFFE, U+FFFF
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

    # ── Sheet 1: Device information ──────────────────────────────────────────
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

    # ── Sheet 2: Summary ────────────────────────────────────────────────────────
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
        # Colour the status
        stav_cell = ws_sum.cell(row=ri, column=2)
        if r.success:
            stav_cell.font = FONT_OK
            stav_cell.fill = FILL_GREEN
        else:
            stav_cell.font = FONT_ERR
            stav_cell.fill = FILL_RED
            ws_sum.cell(row=ri, column=3).value = _sanitize(r.error)

    _auto_width(ws_sum)

    # ── One sheet per module ──────────────────────────────────────────────────
    for r in results:
        if not r.success or not r.data:
            continue

        # Excel: max. 31 characters, no []:*?/\ and names must be unique
        base = re.sub(r"[\[\]:*?/\\]", "-", r.label).strip("' ")[:31] or r.module[:31]
        sheet_name, n = base, 2
        while sheet_name in wb.sheetnames:
            suffix = f" ({n})"
            sheet_name = base[:31 - len(suffix)] + suffix
            n += 1
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
    """Generate a professional HTML report with pagination and search."""
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

    # Prepare JSON data for the JS engine (all data, no limit)
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

    # Summary for the dashboard
    ok_count   = sum(1 for r in results if r.success)
    fail_count = len(results) - ok_count
    total_rec  = sum(r.count for r in results if r.success)

    # Device info rows
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
