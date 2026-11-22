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


# ── Shared by the PDF and HTML report ─────────────────────────────────────────

MAX_THUMBS = 400          # photo previews per module (keeps report size reasonable)
_PHOTO_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
try:                      # HEIC/HEIF (iPhone photos) only if pillow-heif is installed
    import pillow_heif
    pillow_heif.register_heif_opener()
    _PHOTO_EXT |= {".heic", ".heif"}
except Exception:
    pass


def _collect_photos(r, shown: set) -> list:
    """Local image files of a result as (path, display name): the folder the module
    copied media into, or local paths stored in its records. Paths already in
    `shown` are skipped (and new ones added), so each photo is listed only once."""
    found = []
    fp = getattr(r, "file_path", "") or ""
    if fp and os.path.isdir(fp):
        for root, _dirs, names in os.walk(fp):
            for name in sorted(names):
                if os.path.splitext(name)[1].lower() in _PHOTO_EXT:
                    found.append((os.path.join(root, name), name))
    elif isinstance(r.data, list):
        for rec in r.data:
            if not isinstance(rec, dict):
                continue
            for k in ("local_path", "path", "_local"):
                p = rec.get(k)
                if (isinstance(p, str) and os.path.splitext(p)[1].lower() in _PHOTO_EXT
                        and os.path.isfile(p)):
                    found.append((p, rec.get("filename") or os.path.basename(p)))
                    break
    out = []
    for p, name in found:
        key = os.path.normcase(os.path.abspath(p))
        if key not in shown:
            shown.add(key)
            out.append((p, name))
    return out


def _thumb_image(path: str, px: int = 480):
    """Downscaled, orientation-corrected RGB copy of an image; None if undecodable."""
    try:
        from PIL import Image as PILImage, ImageOps
        with PILImage.open(path) as im:
            im.draft("RGB", (px, px))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((px, px))
            return im
    except Exception:
        return None


def _short_name(name: str, limit: int = 30) -> str:
    """Middle ellipsis that keeps the extension (fits one caption line)."""
    return name if len(name) <= limit else name[:limit - 11] + "…" + name[-10:]


def _case_info(case_meta: dict | None) -> dict:
    """Case details + report branding settings (examiner/organisation fall back to Settings)."""
    from . import settings as _settings
    cfg = _settings.load()
    cm = case_meta or {}
    return {
        "cfg":      cfg,
        "case_no":  cm.get("case_number") or "",
        "evidence": cm.get("evidence_id") or "",
        "examiner": cm.get("examiner") or cfg.get("report_examiner", ""),
        "org":      cm.get("organization") or cfg.get("report_organization", ""),
        "note":     cm.get("note") or "",
    }


def generate_pdf_report(results: list, device_info: dict, output_dir: str,
                        case_meta: dict | None = None, file_name: str = "report.pdf",
                        subtitle: str | None = None) -> str:
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
    MAX_THUMBS_PDF = MAX_THUMBS
    from xml.sax.saxutils import escape as _xml_escape

    timestamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    pdf_path  = os.path.join(output_dir, file_name)

    manufacturer = device_info.get("manufacturer", "N/A")
    model        = device_info.get("model", "N/A")
    android      = device_info.get("android", "N/A")
    serial       = device_info.get("serial", "N/A")
    imei         = device_info.get("imei", "N/A")
    device_label = f"{manufacturer} {model}".strip() or "N/A"

    # Settings (report branding) – examiner/organisation are taken from the settings
    # if missing in case_meta; the company logo and block switches come from Settings.
    _ci = _case_info(case_meta)
    _cfg = _ci["cfg"]
    case_no, evidence = _ci["case_no"], _ci["evidence"]
    examiner, org, case_note = _ci["examiner"], _ci["org"], _ci["note"]
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
    story.append(Paragraph(_xml_escape(subtitle or t("rpt.cover_subtitle")),
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

    def _thumb(path):
        """Downscaled JPEG copy in the temp folder as a reportlab Image; None if undecodable."""
        im = _thumb_image(path, 480)
        if im is None:
            return None
        try:
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
            cap = _short_name(name)
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
        block.extend(_gallery(_collect_photos(r, shown_photos)))
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


def generate_report(results: list, device_info: dict, output_dir: str,
                    case_meta: dict | None = None, file_name: str = "report.html",
                    subtitle: str | None = None) -> str:
    """HTML report with the same structure and look as the PDF report: cover with logos
    and case details, table of contents, device information, extraction summary and
    extracted data with photo previews. Tables stay interactive (search, sort, paging);
    printing renders all rows. Self-contained single file (logos and thumbnails embedded)."""
    import base64
    import io
    import html as _html
    from .version import __version__

    timestamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    report_path = os.path.join(output_dir, file_name)

    manufacturer = device_info.get("manufacturer", "N/A")
    model        = device_info.get("model", "N/A")
    android      = device_info.get("android", "N/A")
    serial       = device_info.get("serial", "N/A")
    imei         = device_info.get("imei", "N/A")
    device_label = f"{manufacturer} {model}".strip() or "N/A"

    ci = _case_info(case_meta)
    cfg = ci["cfg"]
    no_case = t("rpt.no_case")

    ok_count   = sum(1 for r in results if r.success)
    fail_count = len(results) - ok_count
    total_rec  = sum(r.count for r in results if r.success)

    def esc(v) -> str:
        s = str(v) if v is not None else ""
        s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)
        return _html.escape(s)

    def data_uri(path: str, max_px: int) -> str:
        """Embed an image (logo) as a PNG data URI; '' if missing/undecodable."""
        if not path or not os.path.isfile(path):
            return ""
        try:
            from PIL import Image as PILImage
            with PILImage.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((max_px, max_px))
                buf = io.BytesIO()
                im.save(buf, "PNG", optimize=True)
            return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return ""

    # ── Header line (same as the PDF page header) ─────────────────────────────
    header_line = []
    if ci["case_no"]:
        header_line.append(f"{t('rpt.hdr_case')}: {ci['case_no']}")
    if ci["evidence"]:
        header_line.append(f"{t('rpt.hdr_evidence')}: {ci['evidence']}")
    header_line.append(device_label)
    header_text = "  ·  ".join(header_line)

    # ── Cover ─────────────────────────────────────────────────────────────────
    tool_logo = data_uri(_report_asset("forenziq_256.png"), 128) \
        if cfg.get("report_show_tool_logo", True) else ""
    company_logo = ""
    if cfg.get("report_show_company_logo", True):
        company_logo = data_uri(cfg.get("report_company_logo", "")
                                or _report_asset("company_logo.png"), 360)

    def kv_block(title, rows):
        trs = "".join(
            f'<tr><th>{esc(lbl)}</th><td>{esc(val) if val not in ("", None) else esc(no_case)}</td></tr>'
            for lbl, val in rows)
        return (f'<div class="cblock"><div class="cblock-h">{esc(title)}</div>'
                f'<table class="kv">{trs}</table></div>')

    cover = ['<section class="cover">',
             '<div class="brand-row"><div class="brand">']
    if tool_logo:
        cover.append(f'<img class="tool-logo" src="{tool_logo}" alt="ForenziQ">')
    cover.append('<div><div class="brand-name">ForenziQ <span>Toolkit</span></div>'
                 f'<div class="brand-sub">v{esc(__version__)}</div></div></div>')
    if company_logo:
        cover.append(f'<img class="company-logo" src="{company_logo}" alt="">')
    cover.append('</div>')
    cover.append(f'<h1 class="ctitle">{esc(t("rpt.title_upper"))}</h1>'
                 f'<div class="csub">{esc(subtitle or t("rpt.cover_subtitle"))}</div>'
                 '<div class="accent-bar"></div>')
    if cfg.get("report_show_case_details", True):
        cover.append(kv_block(t("rpt.case_details"), [
            (t("ev.f_case"), ci["case_no"]), (t("ev.f_evidence_id"), ci["evidence"]),
            (t("ev.f_examiner"), ci["examiner"]), (t("ev.f_org"), ci["org"]),
            (t("ev.f_note"), ci["note"]),
        ]))
    cover.append(kv_block(t("rpt.device_summary"), [
        (t("rpt.f_manufacturer"), manufacturer), ("Model", model),
        (t("rpt.f_android"), android), (t("rpt.f_serial"), serial), ("IMEI", imei),
    ]))
    cover.append(f'<div class="muted">{esc(t("rpt.generated").format(ts=timestamp))}</div>')
    cover.append('</section>')

    # ── Device information ────────────────────────────────────────────────────
    def stat(num, label, cls):
        return f'<div class="stat {cls}"><div class="sn">{esc(num)}</div><div class="sl">{esc(label)}</div></div>'

    dev_fields = [
        (t("rpt.f_manufacturer"), manufacturer), ("Model", model),
        (t("rpt.f_android"), android), ("SDK", device_info.get("sdk", "N/A")),
        (t("rpt.f_serial"), serial), ("IMEI", imei),
        (t("rpt.f_patch"), device_info.get("security_patch", "N/A")),
        (t("rpt.f_battery"), device_info.get("battery", "N/A")),
        ("Brand", device_info.get("brand", "N/A")),
        ("Build ID", device_info.get("build", "N/A")),
    ]
    dev_rows = ""
    for i in range(0, len(dev_fields), 2):
        l = dev_fields[i]
        r2 = dev_fields[i + 1] if i + 1 < len(dev_fields) else ("", "")
        dev_rows += (f"<tr><th>{esc(l[0])}</th><td>{esc(l[1])}</td>"
                     f"<th>{esc(r2[0])}</th><td>{esc(r2[1])}</td></tr>")

    device_sec = (
        f'<section id="sec-device"><h2>{esc(t("rpt.device_info"))}</h2>'
        '<div class="stats">'
        + stat(len(results), t("rpt.modules_total"), "blue")
        + stat(ok_count, t("rpt.ok"), "green")
        + stat(fail_count, t("rpt.failed"), "red")
        + stat(f"{total_rec:,}", t("rpt.records_total"), "blue")
        + f'</div><table class="dev">{dev_rows}</table></section>')

    # ── Extraction summary ────────────────────────────────────────────────────
    sum_rows = ""
    for i, r in enumerate(results):
        name = (f'<a href="#mod-{i}">{esc(r.label)}</a>' if r.success and r.data
                else esc(r.label))
        status = ('<span class="ok">OK</span>' if r.success
                  else f'<span class="err">{esc(t("rpt.status_err"))}</span>')
        sum_rows += (f"<tr><td>{name}</td><td>{status}</td>"
                     f"<td class='num'>{f'{r.count:,}' if r.success else ''}</td>"
                     f"<td>{'' if r.success else esc(r.error[:200])}</td></tr>")
    summary_sec = (
        f'<section id="sec-summary"><h2>{esc(t("rpt.summary"))}</h2>'
        '<table class="grid"><thead><tr>'
        f'<th>{esc(t("rpt.col_module"))}</th><th>{esc(t("rpt.col_status"))}</th>'
        f'<th>{esc(t("rpt.col_records"))}</th><th>{esc(t("rpt.col_note"))}</th>'
        f'</tr></thead><tbody>{sum_rows}</tbody></table></section>')

    # ── Extracted data (tables as JSON for the JS engine, KV and galleries as HTML) ──
    shown_photos: set = set()
    js_tables = {}
    mod_html = []
    toc_mods = []
    data_results = [(i, r) for i, r in enumerate(results) if r.success and r.data]

    def gallery(photos) -> str:
        total = len(photos)
        if not total:
            return ""
        figs = []
        for p, name in photos[:MAX_THUMBS]:
            im = _thumb_image(p, 360)
            rel = os.path.relpath(p, output_dir) if os.path.splitdrive(p)[0].lower() == \
                os.path.splitdrive(os.path.abspath(output_dir))[0].lower() else p
            href = esc(rel.replace(os.sep, "/"))
            if im is not None:
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=70)
                src = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
                pic = f'<img src="{src}" alt="{esc(name)}" loading="lazy">'
            else:
                pic = f'<div class="nopv">{esc(t("rpt.no_preview"))}</div>'
            figs.append(f'<figure><a href="{href}" target="_blank" title="{esc(name)}">{pic}</a>'
                        f'<figcaption>{esc(name)}</figcaption></figure>')
        out = (f'<h4>{esc(t("rpt.gallery").format(n=total))}</h4>'
               f'<div class="gallery">{"".join(figs)}</div>')
        if total > MAX_THUMBS:
            out += (f'<p class="muted">{esc(t("rpt.gallery_truncated").format(shown=MAX_THUMBS, total=total))}</p>')
        return out

    for i, r in data_results:
        toc_mods.append(f'<li><a href="#mod-{i}">{esc(r.label)}</a></li>')
        body = ""
        if isinstance(r.data, list) and r.data and isinstance(r.data[0], dict):
            keys = [k for k in r.data[0].keys() if not any(x in k.lower() for x in ("raw", "blob"))] \
                or list(r.data[0].keys())
            js_tables[i] = {
                "columns": [field_label(k) for k in keys],
                "rows": [[str(rec.get(k, "")) if isinstance(rec, dict) else "" for k in keys]
                         for rec in r.data],
            }
            body = f'<div class="dtable" data-mod="{i}"></div>'
        elif isinstance(r.data, list):
            js_tables[i] = {"columns": [t("res.value_col")],
                            "rows": [[str(v)] for v in r.data]}
            body = f'<div class="dtable" data-mod="{i}"></div>'
        elif isinstance(r.data, dict):
            trs = "".join(f"<tr><th>{esc(field_label(k))}</th><td>{esc(str(v)[:2000])}</td></tr>"
                          for k, v in r.data.items())
            body = f'<table class="kv">{trs}</table>'
        body += gallery(_collect_photos(r, shown_photos))
        mod_html.append(f'<section class="module" id="mod-{i}"><h3>{esc(r.label)}'
                        f'<span class="cnt">{r.count:,} {esc(t("rpt.js_records"))}</span></h3>'
                        f'{body}</section>')

    data_sec = ""
    if data_results:
        data_sec = (f'<section id="sec-data" class="pb"><h2>{esc(t("rpt.sec_data"))}</h2>'
                    + "".join(mod_html) + "</section>")

    # ── Table of contents ─────────────────────────────────────────────────────
    toc = (f'<nav class="toc pb"><h2>{esc(t("rpt.toc"))}</h2><ol>'
           f'<li><a href="#sec-device">{esc(t("rpt.device_info"))}</a></li>'
           f'<li><a href="#sec-summary">{esc(t("rpt.summary"))}</a></li>')
    if data_results:
        toc += (f'<li><a href="#sec-data">{esc(t("rpt.sec_data"))}</a>'
                f'<ol>{"".join(toc_mods)}</ol></li>')
    toc += "</ol></nav>"

    js_data = json.dumps(js_tables, ensure_ascii=False).replace("</", "<\\/")
    js_tr = json.dumps({
        "search": t("rpt.js_search"), "no_data": t("rpt.js_no_data"),
        "shown": t("rpt.js_shown"), "prev": t("rpt.js_prev"), "next": t("rpt.js_next"),
    }, ensure_ascii=False).replace("</", "<\\/")

    footer_left = f"ForenziQ Toolkit v{__version__}  ·  {timestamp}"

    css = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,'Segoe UI',sans-serif;background:#e9edf3;color:#1a2233;font-size:13px;line-height:1.45}
a{color:#1f5fb0;text-decoration:none}a:hover{text-decoration:underline}
.topbar{position:sticky;top:0;z-index:10;background:#0d2149;color:#cdd8ea;font-size:12px;
  display:flex;align-items:center;gap:16px;padding:8px 24px}
.topbar .hl{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.topbar button{background:#00a3cc;border:0;color:#fff;font-weight:700;border-radius:4px;padding:5px 12px;cursor:pointer}
.page{max-width:1120px;margin:24px auto;background:#fff;box-shadow:0 2px 14px rgba(13,33,73,.12);padding:44px 52px}
section,nav{margin-bottom:34px}
h2{font-size:19px;color:#0d2149;margin-bottom:12px}
h3{font-size:15px;color:#1f5fb0;margin:22px 0 8px;display:flex;align-items:baseline;gap:10px}
h3 .cnt{font-size:11px;color:#5a6b82;font-weight:400}
h4{font-size:12px;color:#0d2149;margin:14px 0 6px}
.muted{color:#5a6b82;font-size:11px;margin-top:6px}
/* cover */
.cover{min-height:900px}
.brand-row{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #cdd8ea;padding-bottom:12px}
.brand{display:flex;align-items:center;gap:10px}
.tool-logo{width:52px;height:52px}
.company-logo{max-width:200px;max-height:62px}
.brand-name{font-size:20px;font-weight:700;color:#0d2149}.brand-name span{color:#00a3cc}
.brand-sub{font-size:10px;color:#5a6b82}
.ctitle{font-size:40px;color:#0d2149;margin-top:120px;letter-spacing:.5px}
.csub{font-size:17px;color:#1f5fb0;margin-top:4px}
.accent-bar{width:150px;height:4px;background:#00a3cc;margin:14px 0 46px}
.cblock{margin-bottom:24px}
.cblock-h{background:#0d2149;color:#fff;font-weight:700;padding:6px 12px}
/* tables */
table{border-collapse:collapse;width:100%}
table.kv th,table.dev th{background:#e9eefa;color:#0d2149;text-align:left;width:220px;font-weight:700}
table.dev th{background:#2a6fc4;color:#fff;width:160px}
table.kv th,table.kv td,table.dev th,table.dev td,table.grid th,table.grid td{border:1px solid #cdd8ea;padding:6px 10px;vertical-align:top;overflow-wrap:anywhere}
table.kv tr:nth-child(even) td,table.dev tr:nth-child(odd) td{background:#eef3fb}
table.grid thead th{background:#2a6fc4;color:#fff;text-align:left;white-space:nowrap}
table.grid tbody tr:nth-child(even) td{background:#eef3fb}
table.grid td.num{text-align:right}
.ok{color:#1b7a3d;font-weight:700}.err{color:#b3261e;font-weight:700}
.stats{display:grid;grid-template-columns:repeat(4,1fr);margin-bottom:18px;border:1px solid #cdd8ea}
.stat{text-align:center;padding:14px 6px;border-right:1px solid #cdd8ea;background:#e9eefa}
.stat:last-child{border-right:0}.stat.green{background:#e6f4ea}.stat.red{background:#fdecea}
.stat .sn{font-size:26px;font-weight:700;color:#1f5fb0}.stat.green .sn{color:#1b7a3d}.stat.red .sn{color:#b3261e}
.stat .sl{font-size:11px;color:#5a6b82}
/* toc */
.toc ol{list-style:none;counter-reset:a}
.toc>ol>li{font-weight:700;padding:5px 0;border-bottom:1px dotted #cdd8ea}
.toc ol ol{font-weight:400;margin:4px 0 0 22px}
.toc ol ol li{padding:2px 0}
/* interactive tables */
.tbar{display:flex;align-items:center;gap:10px;margin:4px 0 6px;flex-wrap:wrap}
.tbar input{flex:0 1 300px;border:1px solid #cdd8ea;border-radius:4px;padding:5px 9px;font-size:12px}
.tbar input:focus{outline:2px solid #00a3cc;border-color:#00a3cc}
.tbar .pinfo{margin-left:auto;color:#5a6b82;font-size:11px}
.pager button{border:1px solid #cdd8ea;background:#fff;color:#1f5fb0;border-radius:4px;padding:3px 8px;margin-left:2px;cursor:pointer;font-size:11px}
.pager button.active{background:#1f5fb0;color:#fff;border-color:#1f5fb0}
.pager button:disabled{opacity:.4;cursor:default}
.twrap{overflow-x:auto}
table.grid th.sortable{cursor:pointer;user-select:none}
table.grid th.asc::after{content:' ▲'}table.grid th.desc::after{content:' ▼'}
mark{background:#fff1a8;padding:0 1px}
/* gallery */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
figure{border:1px solid #cdd8ea;padding:6px;text-align:center;display:flex;flex-direction:column;justify-content:flex-end}
figure img{max-width:100%;max-height:130px;object-fit:contain;display:block;margin:0 auto}
figcaption{font-size:10px;color:#5a6b82;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nopv{font-size:10px;color:#5a6b82;padding:40px 0}
footer{display:flex;justify-content:space-between;color:#5a6b82;font-size:11px;border-top:1px solid #cdd8ea;padding-top:10px;margin-top:30px}
@media print{
  body{background:#fff}.topbar,.tbar{display:none}
  .page{box-shadow:none;margin:0;max-width:none;padding:0}
  .cover{min-height:0;page-break-after:always}.pb{page-break-before:always}
  h2,h3,h4{page-break-after:avoid}figure,tr{page-break-inside:avoid}
  thead{display:table-header-group}a{color:inherit}
}
"""

    js = """
const TABLES = %s, TR = %s, PAGE = 50;
const st = {};
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function hl(s,q){if(!q)return esc(s);const re=new RegExp('('+q.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&')+')','gi');return esc(s).replace(re,'<mark>$1</mark>');}
function init(el){
  const i=el.dataset.mod, m=TABLES[i]; st[i]={page:0,q:'',col:-1,dir:1,all:false};
  const th=m.columns.map((c,ci)=>`<th class="sortable" id="th-${i}-${ci}" onclick="sortT(${i},${ci})">${esc(c)}</th>`).join('');
  el.innerHTML=`<div class="tbar"><input placeholder="${esc(TR.search)}" oninput="filt(${i},this.value)">
    <span class="pinfo" id="pi-${i}"></span><span class="pager" id="pg-${i}"></span></div>
    <div class="twrap"><table class="grid"><thead><tr>${th}</tr></thead><tbody id="tb-${i}"></tbody></table></div>`;
  render(i);
}
function render(i){
  const m=TABLES[i], s=st[i]; let rows=m.rows;
  if(s.q){const q=s.q.toLowerCase(); rows=rows.filter(r=>r.some(c=>c.toLowerCase().includes(q)));}
  if(s.col>=0){rows=[...rows].sort((a,b)=>{const x=a[s.col],y=b[s.col];const nx=parseFloat(x),ny=parseFloat(y);
    if(!isNaN(nx)&&!isNaN(ny)&&String(nx)===x.trim()&&String(ny)===y.trim())return (nx-ny)*s.dir;
    return x.localeCompare(y)*s.dir;});}
  const total=rows.length, size=s.all?Math.max(total,1):PAGE, pages=Math.max(1,Math.ceil(total/size));
  s.page=Math.min(s.page,pages-1);
  const slice=rows.slice(s.page*size,(s.page+1)*size);
  m.columns.forEach((_,ci)=>{const h=document.getElementById(`th-${i}-${ci}`);h.classList.remove('asc','desc');if(s.col===ci)h.classList.add(s.dir===1?'asc':'desc');});
  document.getElementById('tb-'+i).innerHTML = slice.length
    ? slice.map(r=>'<tr>'+r.map(c=>`<td>${hl(c,s.q)}</td>`).join('')+'</tr>').join('')
    : `<tr><td colspan="${m.columns.length}">${esc(TR.no_data)}</td></tr>`;
  const from=total?s.page*size+1:0, to=Math.min((s.page+1)*size,total);
  document.getElementById('pi-'+i).textContent=TR.shown.replace('{from}',from).replace('{to}',to).replace('{total}',total);
  let p=`<button onclick="go(${i},${s.page-1})" ${s.page===0?'disabled':''}>${esc(TR.prev)}</button>`;
  const lo=Math.max(0,s.page-2), hi=Math.min(pages-1,s.page+2);
  if(lo>0)p+=`<button onclick="go(${i},0)">1</button>${lo>1?'…':''}`;
  for(let k=lo;k<=hi;k++)p+=`<button class="${k===s.page?'active':''}" onclick="go(${i},${k})">${k+1}</button>`;
  if(hi<pages-1)p+=`${hi<pages-2?'…':''}<button onclick="go(${i},${pages-1})">${pages}</button>`;
  p+=`<button onclick="go(${i},${s.page+1})" ${s.page>=pages-1?'disabled':''}>${esc(TR.next)}</button>`;
  document.getElementById('pg-'+i).innerHTML=p;
}
function go(i,p){st[i].page=p;render(i);}
function filt(i,v){st[i].q=v;st[i].page=0;render(i);}
function sortT(i,c){const s=st[i];if(s.col===c)s.dir*=-1;else{s.col=c;s.dir=1;}s.page=0;render(i);}
document.querySelectorAll('.dtable').forEach(init);
// Printing / "Save as PDF" from the browser → all rows, not just the current page
window.addEventListener('beforeprint',()=>{for(const i in st){st[i].all=true;st[i].page=0;render(i);}});
window.addEventListener('afterprint',()=>{for(const i in st){st[i].all=false;render(i);}});
""" % (js_data, js_tr)

    html = (
        f'<!DOCTYPE html>\n<html lang="{get_lang()}">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<meta name="generator" content="ForenziQ Toolkit v{esc(__version__)}">\n'
        f'<title>{esc(t("rpt.title"))} — {esc(device_label)}</title>\n'
        f'<style>{css}</style>\n</head>\n<body>\n'
        f'<div class="topbar"><span class="hl">{esc(header_text)}</span>'
        f'<span>ForenziQ Toolkit v{esc(__version__)}</span>'
        f'<button onclick="window.print()">🖨 {esc(t("rpt.print"))}</button></div>\n'
        '<div class="page">\n'
        + "".join(cover) + toc + device_sec + summary_sec + data_sec
        + f'<footer><span>{esc(footer_left)}</span><span>{esc(header_text)}</span></footer>\n'
        f'</div>\n<script>{js}</script>\n</body>\n</html>\n'
    )

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    return report_path


# ── Report of tagged items ────────────────────────────────────────────────────

class _TaggedGroup:
    """Result-like group of tagged records from one source (file + section)."""

    def __init__(self, module: str, label: str, data: list):
        self.module = module
        self.label = label
        self.data = data
        self.count = len(data)
        self.success = True
        self.error = ""
        self.file_path = ""


def generate_tagged_reports(output_dir: str, tag: str | None = None,
                            device_info: dict | None = None,
                            case_meta: dict | None = None) -> tuple[str, str, int]:
    """PDF + HTML report containing only tagged records (optionally one tag).

    Records are grouped by source; each row starts with its tags, notes, the examiner
    who tagged it and the SHA-256 of the source file at tagging time.
    Returns (pdf_path, html_path, item_count).
    """
    from .case_tags import CaseTags, audit, load_case_context
    ctx_dev, ctx_case = load_case_context(output_dir)
    device_info = device_info or ctx_dev
    case_meta = case_meta or ctx_case
    with CaseTags(output_dir) as ct:
        items = ct.tagged_items(tag)
    if not items:
        raise ValueError(t("tag.none_tagged"))

    def _label(fname: str, section: str) -> str:
        stem = os.path.splitext(fname)[0]
        key = {"browser_history": "mod.browser", "wifi_networks": "mod.wifi",
               "media_files": "mod.media", "apps_installed": "mod.apps"}.get(stem, f"mod.{stem}")
        base = t(key) if has(key) else stem.replace("_", " ").capitalize()
        return f"{base} – {section}" if section else base

    groups: dict[tuple, list] = {}
    for it in items:
        rec = it["record"] if isinstance(it["record"], dict) else {"value": it["record"]}
        row = {t("tag.col_tags"): ", ".join(it["tags"]),
               t("tag.col_note"): " | ".join(it["notes"])}
        for k, v in rec.items():
            row[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
        row[t("tag.col_examiner")] = f'{it["examiner"]} · {(it["tagged_at"] or "").replace("T", " ")}'
        row[t("tag.col_source")] = f'{it["file"]} · SHA-256 {(it["source_sha256"] or "-")[:16]}…'
        groups.setdefault((it["file"], it["section"]), []).append(row)

    results = []
    for (fname, section), rows in groups.items():
        # every row gets all columns of its group (records of one source can differ)
        cols = list(dict.fromkeys(k for r in rows for k in r))
        data = [{c: r.get(c, "") for c in cols} for r in rows]
        results.append(_TaggedGroup(os.path.splitext(fname)[0], _label(fname, section), data))

    subtitle = t("tag.report_subtitle") + (f" – {tag}" if tag else "")
    pdf = generate_pdf_report(results, device_info, output_dir, case_meta=case_meta,
                              file_name="report_tagged.pdf", subtitle=subtitle)
    html = generate_report(results, device_info, output_dir, case_meta=case_meta,
                           file_name="report_tagged.html", subtitle=subtitle)
    audit(output_dir, "tagged_report", tag=tag or "*", items=len(items),
          files=["report_tagged.pdf", "report_tagged.html"])
    return pdf, html, len(items)
