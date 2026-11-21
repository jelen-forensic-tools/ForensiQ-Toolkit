"""PDF report from DumpAnalyzer results, in the current language."""
import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

from . import __version__
from .analyzer import DIR_DRAFT, DIR_IN, DIR_OUT, SRC_CURRENT, normalize_number
from .export import file_status
from .i18n import t

TOOL_NAME = 'SC6531E Dump Analyzer'
DIR_ARROW = {DIR_IN: '← ', DIR_OUT: '→ ', DIR_DRAFT: '· '}

# (regular, bold) – first pair found wins; needs a font with Czech diacritics
FONT_CANDIDATES = [
    ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf'),
    ('arial.ttf', 'arialbd.ttf'),
    ('segoeui.ttf', 'segoeuib.ttf'),
    ('LiberationSans-Regular.ttf', 'LiberationSans-Bold.ttf'),
]
FONT_DIRS = [
    os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'),
    '/usr/share/fonts/truetype/dejavu',
    '/usr/share/fonts/truetype/liberation',
    '/Library/Fonts',
    os.path.expanduser('~/Library/Fonts'),
]


def _register_fonts():
    for reg, bold in FONT_CANDIDATES:
        for d in FONT_DIRS:
            pr, pb = os.path.join(d, reg), os.path.join(d, bold)
            if os.path.exists(pr) and os.path.exists(pb):
                pdfmetrics.registerFont(TTFont('RF', pr))
                pdfmetrics.registerFont(TTFont('RF-Bold', pb))
                return 'RF', 'RF-Bold'
    return 'Helvetica', 'Helvetica-Bold'   # no diacritics


def _th(key):
    """Table header from a (lower-case) column name."""
    s = t(key)
    return s[:1].upper() + s[1:]


def _p(text, style):
    return Paragraph(escape(str(text)).replace('\n', '<br/>'), style)


def build_pdf(an, path, case=None, sections=None, photo_thumbs=True, log=print):
    case = case or {}
    sections = sections or {'contacts': True, 'sms': True, 'calls': True, 'files': True}
    font, bold = _register_fonts()
    st = {
        'h1': ParagraphStyle('h1', fontName=bold, fontSize=18, leading=22, spaceAfter=8),
        'h2': ParagraphStyle('h2', fontName=bold, fontSize=13, leading=16, spaceBefore=10, spaceAfter=6),
        'n': ParagraphStyle('n', fontName=font, fontSize=9, leading=12),
        'k': ParagraphStyle('k', fontName=bold, fontSize=9, leading=12),
        'small': ParagraphStyle('s', fontName=font, fontSize=7.5, leading=9.2),
        'th': ParagraphStyle('th', fontName=bold, fontSize=7.5, leading=9.2, textColor=colors.white),
        'cap': ParagraphStyle('cap', fontName=font, fontSize=6.5, leading=8, alignment=TA_CENTER),
    }
    lookup = an.contact_lookup()
    W = A4[0] - 30 * mm
    dash = '—'

    def table(header, rows, widths, wrap=()):
        """Only columns in `wrap` become Paragraphs (word wrapping); the rest are plain strings, which
        reportlab lays out many times faster. Plain cells too wide for their column are wrapped anyway."""
        pad = 6
        sw = pdfmetrics.stringWidth

        def cell(c, i):
            s = '' if c is None else str(c)
            if i in wrap or any(sw(line, font, 7.5) > widths[i] - pad for line in s.split('\n')):
                return _p(s, st['small'])
            return s
        data = [[_p(h, st['th']) for h in header]] + [[cell(c, i) for i, c in enumerate(r)] for r in rows]
        tb = Table(data, colWidths=widths, repeatRows=1)
        tb.setStyle(TableStyle([
            ('FONTNAME', (0, 1), (-1, -1), font), ('FONTSIZE', (0, 1), (-1, -1), 7.5),
            ('LEADING', (0, 1), (-1, -1), 9.2),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#33475b')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f1f4f7')]),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#c5ccd3')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        ]))
        return tb

    def kv(rows):
        tb = Table([[_p(k, st['k']), _p(v, st['n'])] for k, v in rows], colWidths=[48 * mm, W - 48 * mm])
        tb.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                ('LINEBELOW', (0, 0), (-1, -1), 0.25, colors.HexColor('#dde2e7'))]))
        return tb

    info = an.info
    story = [_p(t('rep.title'), st['h1'])]

    story.append(_p(t('rep.case'), st['h2']))
    story.append(kv([
        (t('rep.case_id'), case.get('case_id') or dash),
        (t('rep.examiner'), case.get('examiner') or dash),
        (t('rep.note'), case.get('note') or dash),
        (t('rep.analysis_date'), info.get('analysis_time', '')),
        (t('rep.tool'), f'{TOOL_NAME} {__version__}'),
    ]))

    story.append(_p(t('rep.source'), st['h2']))
    story.append(kv([
        (t('rep.file'), info.get('file', '')),
        (t('rep.size'), t('rep.bytes', n=f"{info.get('size', 0):,}".replace(',', ' '))),
        ('MD5', info.get('md5', '')),
        ('SHA-256', info.get('sha256', '')),
    ]))

    story.append(_p(t('rep.device'), st['h2']))
    nv = (t('rep.nv_value', blocks=info['nv_blocks'], kb=info['nv_block_kb'], range=info['nv_range'])
          if 'nv_blocks' in info else dash)
    disk = (t('rep.disk_value', label=an.fat['label'], sectors=an.fat['sectors'], bps=an.fat['bps'],
              present=an.fat['present']) if an.fat else t('rep.not_found'))
    story.append(kv([
        (t('rep.chipset'), 'Spreadtrum SC6531E (MOCOR)' if info.get('supported') else t('rep.unrecognized')),
        (t('rep.platform'), info.get('platform', dash)),
        (t('rep.mmi_hw'), f"{info.get('mmi', dash)} / {info.get('hw', dash)}"),
        (t('rep.nv_area'), nv),
        (t('rep.disk'), disk),
    ]))

    # ---- summary
    story.append(_p(t('rep.summary'), st['h2']))
    summ = []
    if sections.get('contacts'):
        tr = sum(1 for c in an.contacts if c['truncated'])
        summ.append((t('sec.contacts'), t('rep.sum_contacts', n=len(an.contacts),
                                          names=len({c['name'] for c in an.contacts}), empty=an.contact_empty,
                                          slots=an.contact_slots, truncated=tr)))
    if sections.get('sms'):
        rec = sum(1 for x in an.sms if x['source'] != SRC_CURRENT)
        dates = [x['time'] for x in an.sms if x['time']]
        rng = t('rep.period', a=min(dates)[:10], b=max(dates)[:10]) if dates else ''
        summ.append(('SMS', t('rep.sum_sms', n=len(an.sms), inc=sum(x['direction'] == DIR_IN for x in an.sms),
                                out=sum(x['direction'] == DIR_OUT for x in an.sms),
                                draft=sum(x['direction'] == DIR_DRAFT for x in an.sms), recovered=rec) + rng))
    if sections.get('calls'):
        rec = sum(1 for x in an.calls if x['source'] != SRC_CURRENT)
        rng = t('rep.period', a=an.calls[0]['time'][:10], b=an.calls[-1]['time'][:10]) if an.calls else ''
        summ.append((t('sec.calls'), t('rep.sum_calls', n=len(an.calls), current=len(an.calls) - rec,
                                       recovered=rec) + rng))
    if sections.get('files'):
        act = [f for f in an.files if not f['deleted']]
        photos = [f for f in act if f['path'].lower().endswith('.jpg')]
        summ.append((t('sec.files'), t('rep.sum_files', active=len(act), photos=len(photos),
                                       deleted=len(an.files) - len(act))))
    story.append(kv(summ))

    # ---- notes
    story.append(_p(t('rep.limitations'), st['h2']))
    for n in an.warning_texts() + [t(f'rep.lim{i}') for i in range(1, 7)]:
        story.append(_p('• ' + n, st['n']))

    # ---- contacts
    if sections.get('contacts') and an.contacts:
        story += [PageBreak(), _p(t('rep.h_contacts', n=len(an.contacts)), st['h2'])]
        rows = [(c['index'], c['name'] + (' …' if c['truncated'] else ''), c['number'], c['type_code'], c['nv_id'])
                for c in an.contacts]
        story.append(table(['#', _th('col.name'), _th('col.number'), t('rep.th_type'), 'NV ID'], rows,
                           [12 * mm, 75 * mm, 45 * mm, 18 * mm, W - 150 * mm], wrap={1}))
        log(t('log.pdf_section', section=t('sec.contacts')))

    # ---- SMS
    if sections.get('sms') and an.sms:
        msgs = an.sms_messages()
        story += [PageBreak(), _p(t('rep.h_sms', n=len(msgs), records=len(an.sms)), st['h2'])]
        rows = []
        for x in msgs:
            who = lookup.get(normalize_number(x['number']), '')
            text = x['text']
            if x['missing']:
                text += ' ' + t('rep.missing_parts', parts=', '.join(map(str, x['missing'])),
                                total=x['parts'].split('/')[1])
            note = []
            if x['parts']:
                note.append(t('rep.parts', parts=x['parts']))
            if x['source'] != SRC_CURRENT:
                note.append(t('rep.recovered_sms'))
            rows.append((x['time'], DIR_ARROW[x['direction']] + t('val.dir.' + x['direction']),
                         x['number'] + (f'\n{who}' if who else ''), text, ' '.join(note)))
        story.append(table([_th('col.time'), t('rep.th_dir'), t('rep.th_number_contact'), _th('col.text'),
                            t('rep.th_note')], rows, [27 * mm, 20 * mm, 32 * mm, W - 99 * mm, 20 * mm], wrap={3}))
        log(t('log.pdf_section', section='SMS'))

    # ---- calls
    if sections.get('calls') and an.calls:
        story += [PageBreak(), _p(t('rep.h_calls', n=len(an.calls)), st['h2'])]
        rows = []
        for c in an.calls:
            m, s = divmod(int(c['duration_s']), 60)
            rows.append((c['time'], c['number'], lookup.get(normalize_number(c['number']), ''), f'{m}:{s:02d}',
                         c['type_code'], '' if c['source'] == SRC_CURRENT else t('rep.recovered_call')))
        story.append(table([_th('col.time'), _th('col.number'), _th('col.contact'), t('rep.th_duration'),
                            t('rep.th_type'), t('rep.th_note')], rows,
                           [32 * mm, 32 * mm, W - 116 * mm, 15 * mm, 12 * mm, 25 * mm]))
        log(t('log.pdf_section', section=t('sec.calls')))

    # ---- files
    if sections.get('files') and an.files:
        story += [PageBreak(), _p(t('rep.h_files', n=len(an.files)), st['h2'])]
        rows = [(f['path'], f"{f['size']:,}".replace(',', ' '), f['date'], file_status(f)) for f in an.files]
        story.append(table([_th('col.path'), t('rep.th_size'), _th('col.date'), _th('col.status')], rows,
                           [W - 97 * mm, 20 * mm, 32 * mm, 45 * mm], wrap={0}))
        if photo_thumbs:
            photos = [f for f in an.files if not f['deleted'] and f['path'].lower().endswith('.jpg')
                      and f['data'][:2] == b'\xff\xd8']
            if photos:
                story += [PageBreak(), _p(t('rep.h_photos', n=len(photos)), st['h2'])]
                story += _thumb_grid(photos, st, W, log)
        log(t('log.pdf_section', section=t('sec.files')))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.grey)
        txt = (f"{case.get('case_id') or ''}  {os.path.basename(info.get('file', ''))}  "
               f"SHA-256 {info.get('sha256', '')[:16]}…")
        canvas.drawString(15 * mm, 8 * mm, txt.strip())
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, t('rep.page', n=doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=t('rep.title'), author=case.get('examiner') or '',
                            creator=f'{TOOL_NAME} {__version__}')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    log(t('log.pdf_saved', file=os.path.basename(path)))


def _thumb_grid(photos, st, W, log):
    try:
        from PIL import Image as PILImage
    except ImportError:
        log(t('log.no_pillow'))
        return []
    cols = 4
    cw = W / cols
    cells = []
    for f in photos:
        try:
            im = PILImage.open(io.BytesIO(f['data']))
            im.thumbnail((360, 360))
            im = im.convert('RGB')
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=80)
            buf.seek(0)
            ratio = im.height / im.width
            w = cw - 6 * mm
            h = w * ratio
            if h > 55 * mm:
                h = 55 * mm
                w = h / ratio
            img = Image(buf, width=w, height=h)
        except Exception:
            img = _p(t('rep.cannot_show'), st['cap'])
        cells.append([img, _p(f"{f['path'].rsplit('/', 1)[-1]}\n{f['date']}", st['cap'])])
    rows = []
    for i in range(0, len(cells), cols):
        chunk = cells[i:i + cols] + [['', '']] * (cols - len(cells[i:i + cols]))
        rows.append([c[0] for c in chunk])
        rows.append([c[1] for c in chunk])
    out = []
    for i in range(0, len(rows), 2):
        tb = Table(rows[i:i + 2], colWidths=[cw] * cols)
        tb.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, 0), 'BOTTOM'),
                                ('BOTTOMPADDING', (0, 1), (-1, 1), 8)]))
        out.append(KeepTogether(tb))
    return out
