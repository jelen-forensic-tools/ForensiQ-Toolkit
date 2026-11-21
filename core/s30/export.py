"""Export of analysis results to CSV / vCard / extracted files, in the current language."""
import csv
import os

from .analyzer import normalize_number
from .i18n import t

CONTACT_COLS = ['index', 'name', 'number', 'type_code', 'ton', 'truncated', 'nv_id', 'offset']
SMS_COLS = ['time', 'tz', 'direction', 'number', 'contact', 'text', 'part', 'total', 'ref', 'source',
            'status_code', 'saved_time', 'smsc', 'nv_id', 'offset']
CALL_COLS = ['time', 'number', 'contact', 'duration_s', 'type_code', 'source', 'nv_id', 'offset']
FILE_COLS = ['path', 'size', 'date', 'deleted', 'status']


def yes_no(v):
    return t('val.yes') if v else t('val.no')


def file_status(f):
    if f['status'] == 'incomplete':
        return t('val.file.incomplete', got=f['got'], size=f['size'])
    return t('val.file.' + f['status'])


_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def safe_component(name):
    """Make one path component from the dump safe to create on Windows/Linux (no traversal, no invalid chars)."""
    s = ''.join('_' if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in name).rstrip(' .')
    if not s or set(s) <= {'.'}:
        s = '_'
    if s.split('.')[0].upper() in _RESERVED:
        s = '_' + s
    return s


def _unique(path, used):
    """Avoid overwriting a file already written in this run (e.g. two deleted files with the same name)."""
    base, ext = os.path.splitext(path)
    p, i = path, 1
    while p.lower() in used:
        i += 1
        p = f'{base}_{i}{ext}'
    used.add(p.lower())
    return p


def _write_csv(path, rows, cols):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow([t('col.' + c) for c in cols])
        for r in rows:
            w.writerow(['' if r.get(c) is None else r.get(c) for c in cols])


def _vcf_escape(s):
    return s.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')


def write_vcard(contacts, path):
    """Consecutive slots with the same name are one contact with several numbers."""
    with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
        prev = None
        for c in contacts:
            if prev and prev['name'] == c['name'] and c['index'] == prev['index'] + 1:
                if c['number']:
                    f.write(f"TEL:{c['number']}\n")
            else:
                if prev:
                    f.write('END:VCARD\n')
                f.write('BEGIN:VCARD\nVERSION:3.0\n')
                fn = c['name'] or c['number']             # FN must not be empty (vCard 3.0)
                f.write(f"FN:{_vcf_escape(fn)}\nN:{_vcf_escape(c['name'])};;;;\n")
                if c['number']:
                    f.write(f"TEL:{c['number']}\n")
            prev = c
        if prev:
            f.write('END:VCARD\n')


def export(an, outdir, contacts=True, sms=True, calls=True, files=True, disk_image=True, log=print):
    os.makedirs(outdir, exist_ok=True)
    lookup = an.contact_lookup()
    written = []

    if contacts and an.contacts:
        p = os.path.join(outdir, t('fn.contacts') + '.csv')
        _write_csv(p, [dict(c, truncated=yes_no(c['truncated'])) for c in an.contacts], CONTACT_COLS)
        written.append(p)
        p = os.path.join(outdir, t('fn.contacts') + '.vcf')
        write_vcard(an.contacts, p)
        written.append(p)

    if sms and an.sms:
        rows = [dict(x, contact=lookup.get(normalize_number(x['number']), ''),
                     direction=t('val.dir.' + x['direction']), source=t('val.src.sms.' + x['source']))
                for x in an.sms]
        p = os.path.join(outdir, t('fn.sms') + '.csv')
        _write_csv(p, rows, SMS_COLS)
        written.append(p)

    if calls and an.calls:
        rows = [dict(x, contact=lookup.get(normalize_number(x['number']), ''),
                     source=t('val.src.call.' + x['source'])) for x in an.calls]
        p = os.path.join(outdir, t('fn.calls') + '.csv')
        _write_csv(p, rows, CALL_COLS)
        written.append(p)

    if files and an.files:
        root_ok = os.path.join(outdir, t('fn.files'))
        root_del = os.path.join(outdir, t('fn.files_deleted'))
        used = set()
        for f in an.files:
            if not f['data']:
                continue
            parts = [safe_component(x) for x in f['path'].split('/') if x]
            fp = _unique(os.path.join(root_del if f['deleted'] else root_ok, *parts), used)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, 'wb') as h:
                h.write(f['data'])
        p = os.path.join(outdir, t('fn.files_list') + '.csv')
        _write_csv(p, [dict(f, deleted=yes_no(f['deleted']), status=file_status(f)) for f in an.files], FILE_COLS)
        written.append(p)

    if disk_image and an.disk_image:
        p = os.path.join(outdir, 'fat12_disk.img')
        with open(p, 'wb') as h:
            h.write(an.disk_image)
        written.append(p)

    for p in written:
        log(t('log.saved', file=os.path.basename(p)))
    return written
