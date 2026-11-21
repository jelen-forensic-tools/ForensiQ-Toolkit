"""Parser for NOR flash dumps of Spreadtrum SC6531E phones (MOCOR firmware).

Extracts contacts, SMS, call log and files from the internal FAT12 disk.
All record structures are documented in docs/MANUAL.md ("How it works").

Internal values are language-neutral codes; translation happens only on output
(see export.py, report.py and i18n.py).
"""
import collections
import datetime as dt
import hashlib
import os
import re
import struct

from .i18n import t

EPOCH_1980 = dt.datetime(1980, 1, 1)
BCD_DIGITS = '0123456789*#abc'

SZ_CONTACT = 0x38
SZ_SMS = 0xe8
SZ_CALLS = 0x528
SZ_SECTOR = 0x400
SIZES = (SZ_CONTACT, SZ_SMS, SZ_CALLS, SZ_SECTOR)

GSM7 = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = {0x0a: '\f', 0x14: '^', 0x28: '{', 0x29: '}', 0x2f: '\\', 0x3c: '[',
            0x3d: '~', 0x3e: ']', 0x40: '|', 0x65: '€'}

# value codes
DIR_IN, DIR_OUT, DIR_DRAFT = 'in', 'out', 'draft'
# MOCOR SMS status byte (MN_SMS_STATUS_E) for outgoing records: 3 = MO to be sent, 4 and above = sent.
# Outgoing records with 3 were never sent: drafts / outbox (many have no recipient or no text).
SMS_STATUS_UNSENT = 0x03
SRC_CURRENT, SRC_RECOVERED, SRC_DUPLICATE = 'current', 'recovered', 'duplicate'
FILE_OK, FILE_INCOMPLETE, FILE_DELETED = 'ok', 'incomplete', 'deleted'


# --------------------------------------------------------------------------- helpers

def bcd(b):
    """Decode swapped-nibble BCD (GSM 04.08), terminated by 0xF."""
    s = ''
    for x in b:
        for n in (x & 15, x >> 4):
            if n == 15:
                return s
            s += BCD_DIGITS[n]
    return s


def gsm7(b, count, skipbits=0):
    """Decode `count` GSM 03.38 7-bit septets from packed bytes, skipping `skipbits` leading bits."""
    out = []
    esc = False
    bits = int.from_bytes(b, 'little')
    for k in range(count):
        c = (bits >> (skipbits + 7 * k)) & 0x7f
        if esc:
            out.append(GSM7_EXT.get(c, '?'))
            esc = False
        elif c == 0x1b:
            esc = True
        else:
            out.append(GSM7[c])
    return ''.join(out)


def normalize_number(n):
    """Strip the Czech country prefix so that national and international forms match."""
    n = (n or '').strip().replace(' ', '')
    for p in ('+420', '00420'):
        if n.startswith(p) and len(n) > len(p) + 6:
            return n[len(p):]
    return n


class Item:
    """NV log item: header [id][checksum][id][length] followed by data."""
    __slots__ = ('offset', 'id', 'checksum', 'valid', 'data')

    def __init__(self, offset, id_, checksum, valid, data):
        self.offset = offset
        self.id = id_
        self.checksum = checksum
        self.valid = valid      # False = invalidated (older) copy, first ID zeroed
        self.data = data


# --------------------------------------------------------------------------- SMS PDU

def _addr(b, i):
    n = b[i]
    ton = b[i + 1]
    if n > 20 or not ton & 0x80:
        raise ValueError('bad address')
    nb = (n + 1) // 2
    raw = b[i + 2:i + 2 + nb]
    if len(raw) < nb:
        raise ValueError('short address')
    if (ton & 0x70) == 0x50:              # alphanumeric sender
        s = gsm7(raw, (n * 4) // 7)
    else:
        s = bcd(raw)[:n]
        if not re.fullmatch(r'[0-9*#]*', s):
            raise ValueError('bad digits')
        if (ton & 0x70) == 0x10:
            s = '+' + s
    return s, i + 2 + nb


def _scts(b):
    s = [(x & 15) * 10 + (x >> 4) for x in b[:6]]
    if not (1 <= s[1] <= 12 and 1 <= s[2] <= 31 and s[3] < 24 and s[4] < 60 and s[5] < 60):
        raise ValueError('bad timestamp')
    z = b[6]
    q = (z & 7) * 10 + (z >> 4)
    sign = '-' if z & 8 else '+'
    return (f"20{s[0]:02d}-{s[1]:02d}-{s[2]:02d} {s[3]:02d}:{s[4]:02d}:{s[5]:02d}",
            f"{sign}{q * 15 // 60:02d}:{q * 15 % 60:02d}")


def decode_tpdu(b):
    """Decode SMS-DELIVER / SMS-SUBMIT TPDU (3GPP TS 23.040)."""
    fo = b[0]
    mti = fo & 3
    i = 1
    r = {'time': '', 'tz': ''}
    if mti == 0:
        r['direction'] = DIR_IN
        r['number'], i = _addr(b, i)
        dcs = b[i + 1]
        i += 2
        r['time'], r['tz'] = _scts(b[i:i + 7])
        i += 7
    elif mti == 1:
        r['direction'] = DIR_OUT
        i += 1                               # TP-MR
        r['number'], i = _addr(b, i)
        dcs = b[i + 1]
        i += 2
        i += {0: 0, 2: 1, 1: 7, 3: 7}[(fo >> 3) & 3]
    else:
        raise ValueError('unsupported MTI %d' % mti)
    udl = b[i]
    i += 1
    ud = b[i:]
    if (dcs & 0x80) == 0:                    # 00xx general / 01xx auto-deletion (3GPP TS 23.038)
        alphabet = (dcs >> 2) & 3
        if dcs & 0x20:                       # compressed: show raw bytes instead of losing the message
            alphabet = 1
    elif (dcs & 0xf0) == 0xe0:               # message waiting, UCS-2
        alphabet = 2
    elif (dcs & 0xf0) == 0xf0:
        alphabet = (dcs >> 2) & 1
    else:
        alphabet = 0
    hl = 0
    udh = b''
    if fo & 0x40:
        hl = ud[0] + 1
        udh = ud[1:hl]
    ref = part = total = None
    j = 0
    while j + 1 < len(udh):
        iei, ln = udh[j], udh[j + 1]
        v = udh[j + 2:j + 2 + ln]
        if iei == 0 and ln == 3:
            ref, total, part = v[0], v[1], v[2]
        elif iei == 8 and ln == 4:
            ref, total, part = v[0] << 8 | v[1], v[2], v[3]
        j += 2 + ln
    if alphabet == 2:
        text = ud[hl:udl].decode('utf-16be', errors='replace')
    elif alphabet == 1:
        text = '[binary] ' + ud[hl:udl].hex()
    else:
        if udl > 160:
            raise ValueError('bad UDL')
        sk = (hl * 8 + 6) // 7 * 7 if hl else 0
        text = gsm7(ud[:(udl * 7 + 7) // 8], udl - sk // 7, sk)
    r.update(text=text, ref=ref, part=part, total=total)
    return r


# --------------------------------------------------------------------------- analyzer

class DumpAnalyzer:
    def __init__(self, path, log=print, progress=None):
        self.path = path
        self.log = log
        self.progress = progress or (lambda pct: None)
        with open(path, 'rb') as f:
            self.d = f.read()
        self.warnings = []           # list of (i18n key, kwargs)
        self.items = {s: [] for s in SIZES}
        self.info = {}
        self.contacts = []
        self.contact_slots = 0
        self.contact_empty = 0
        self.sms = []
        self.sms_empty = 0
        self.calls = []
        self.files = []
        self.fat = None
        self.disk_image = None

    def warn(self, key, **kw):
        if (key, kw) not in self.warnings:
            self.warnings.append((key, kw))

    def warning_texts(self):
        return [t(k, **kw) for k, kw in self.warnings]

    # ---------------------------------------------------------------- pipeline
    def run(self, contacts=True, sms=True, calls=True, files=True):
        self.log(t('log.loading', name=os.path.basename(self.path), size=f'{len(self.d):,}'))
        self.compute_hashes()
        self.progress(5)
        self.detect_device()
        self.progress(10)
        self.scan_items()
        self.progress(40)
        if contacts:
            self.parse_contacts()
        self.progress(50)
        if sms:
            self.parse_sms()
        self.progress(60)
        if calls:
            self.parse_calls()
        self.progress(70)
        if files:
            self.parse_filesystem()
        self.progress(80)

    def compute_hashes(self):
        self.info['file'] = os.path.abspath(self.path)
        self.info['size'] = len(self.d)
        self.info['md5'] = hashlib.md5(self.d).hexdigest()
        self.info['sha256'] = hashlib.sha256(self.d).hexdigest()
        self.info['analysis_time'] = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.log(f"SHA-256: {self.info['sha256']}")

    def detect_device(self):
        fields = {
            'platform': rb'Platform Version:\s*([\x20-\x7e]+)',
            'mmi': rb'MMI Version:\s*([\x20-\x7e]+)',
            'hw': rb'HW Version:\s*([\x20-\x7e]+)',
        }
        for k, p in fields.items():
            m = re.search(p, self.d)
            if m:
                self.info[k] = m.group(1).decode('ascii').strip()
        ok = b'6531E' in self.d
        self.info['supported'] = ok
        if ok:
            self.log(t('log.device', device=self.info.get('platform', 'Spreadtrum SC6531E')))
        else:
            self.warn('warn.unsupported')
            self.log(t('log.unsupported'))

    # ---------------------------------------------------------------- NV log
    def _walk_blocks(self, bsize):
        """Walk the NV log block by block: items follow each other (offset + 8 + length, 2-byte aligned).
        A block is accepted only if its chain ends cleanly (free space, exact end, or tail < 16 bytes)."""
        d = self.d
        found = []
        blocks = []
        for bs in range(0, len(d) - bsize + 1, bsize):
            be = bs + bsize
            o = bs
            chain = []
            clean = False
            while o + 8 <= be:
                a, ck, a2, sz = struct.unpack_from('<HHHH', d, o)
                if a == 0xffff and a2 == 0xffff and sz == 0xffff:
                    clean = True
                    break
                if a2 in (0, 0xffff) or a not in (a2, 0) or sz == 0 or o + 8 + sz > be:
                    clean = be - o < 16 and len(chain) > 0
                    break
                chain.append((o, a, ck, a2, sz))
                o = (o + 8 + sz + 1) & ~1
            else:
                clean = len(chain) > 0
            if clean and chain:
                blocks.append(bs)
                found += chain
        return blocks, found

    def scan_items(self):
        best = None
        for bsize in (0x8000, 0x10000, 0x4000, 0x20000):
            blocks, found = self._walk_blocks(bsize)
            if not best or len(found) > len(best[2]):
                best = (bsize, blocks, found)
        bsize, blocks, found = best
        if len(found) >= 50:
            d = self.d
            for o, a, ck, a2, sz in found:
                if sz in self.items:
                    self.items[sz].append(Item(o, a2, ck, a == a2, d[o + 8:o + 8 + sz]))
            self.info['nv_blocks'] = len(blocks)
            self.info['nv_block_kb'] = bsize // 1024
            self.info['nv_range'] = f'0x{min(blocks):x}–0x{max(blocks) + bsize:x}'
            self.log(t('log.nv_found', blocks=len(blocks), kb=bsize // 1024, items=len(found)))
        else:
            self.warn('warn.nv_fallback')
            self.log(t('log.nv_fallback'))
            self._scan_items_pattern()
        self.log(t('log.items', items=', '.join(f'{len(v)}× 0x{k:x}' for k, v in self.items.items())))

    def _scan_items_pattern(self):
        """Fallback: find item headers anywhere in the dump (higher false-positive risk)."""
        d = self.d
        pat = re.compile(rb'(?s)(?=(?:\x38\x00|\xe8\x00|\x28\x05|\x00\x04))')
        n = len(d)
        for m in pat.finditer(d, 6):
            o = m.start() - 6
            a, ck, a2, sz = struct.unpack_from('<HHHH', d, o)
            if sz not in self.items or a2 in (0, 0xffff) or o + 8 + sz > n:
                continue
            if a == a2:
                valid = True
            elif a == 0:
                valid = False
            else:
                continue
            self.items[sz].append(Item(o, a2, ck, valid, d[o + 8:o + 8 + sz]))

    # ---------------------------------------------------------------- contacts
    def parse_contacts(self):
        cands = []
        for it in self.items[SZ_CONTACT]:
            if not it.valid:
                continue
            r = it.data
            idx, stor = struct.unpack_from('<HH', r, 0)
            if stor != 1 or not 1 <= idx <= 10000:
                continue
            if r[0x20] not in (0, 1) or r[0x21] > 20 or r[10] > 20 or any(r[0x36:0x38]):
                continue
            cands.append((it, idx))
        if not cands:
            self.warn('warn.no_contacts')
            self.log(t('log.contacts_none'))
            return
        # item ID = slot index + constant offset (firmware specific) -> take the most common offset
        off = collections.Counter(it.id - idx for it, idx in cands).most_common(1)[0][0]
        by_id = {}
        for it, idx in cands:
            if it.id - idx != off:
                continue
            if it.id in by_id and by_id[it.id][0].data != it.data:
                self.warn('warn.contact_versions', id=f'0x{it.id:x}', offset=f'0x{it.offset:x}')
            by_id[it.id] = (it, idx)
        self.contact_slots = len(by_id)
        for it, idx in sorted(by_id.values(), key=lambda x: x[1]):
            r = it.data
            typ, ln, ton = r[9], r[10], r[11]
            num = bcd(r[12:12 + ln]) if ln else ''
            if num and (ton & 0x70) == 0x10:
                num = '+' + num
            enc, nl = r[0x20], r[0x21]
            nb = r[0x22:0x22 + nl]
            name = nb.decode('utf-16le', errors='replace') if enc == 1 else nb.decode('latin-1')
            if not num and not name:
                self.contact_empty += 1
                continue
            self.contacts.append(dict(index=idx, name=name, number=num, type_code=f'0x{typ:x}',
                                      ton=f'0x{ton:x}', truncated=(nl == 20),
                                      nv_id=f'0x{it.id:x}', offset=f'0x{it.offset:x}'))
        self.log(t('log.contacts', n=len(self.contacts), empty=self.contact_empty, slots=self.contact_slots))

    # ---------------------------------------------------------------- SMS
    def parse_sms(self):
        rows = []
        for it in self.items[SZ_SMS]:
            r = it.data
            if not any(r[6:40]):
                if it.valid:
                    self.sms_empty += 1
                continue
            smsc_len = r[6]
            if r[4] > 0x0f or smsc_len > 11 or (smsc_len and r[7] not in (0x81, 0x91, 0xa1, 0x80, 0x90)):
                continue
            try:
                x = decode_tpdu(r[18:])
            except Exception:
                continue
            tt = r[0xdf:0xe5]
            saved = ''
            if tt[0] and 1 <= tt[1] <= 12 and 1 <= tt[2] <= 31:
                saved = f"{1980 + tt[0]}-{tt[1]:02d}-{tt[2]:02d} {tt[3]:02d}:{tt[4]:02d}:{tt[5]:02d}"
            x['saved_time'] = saved
            if not x['time']:
                x['time'] = saved
            x['smsc'] = ('+' + bcd(r[8:7 + smsc_len])) if smsc_len else ''
            x['status_code'] = f'0x{r[4]:x}'
            if x['direction'] == DIR_OUT and r[4] == SMS_STATUS_UNSENT:
                x['direction'] = DIR_DRAFT
            x['nv_id'] = f'0x{it.id:x}'
            x['offset'] = f'0x{it.offset:x}'
            x['_valid'] = it.valid
            rows.append(x)
        # an older copy is only interesting if the same message is not present in a valid copy
        valid_keys = {(x['direction'], x['number'], x['time'], x['text']) for x in rows if x['_valid']}
        seen_old = set()
        for x in rows:
            k = (x['direction'], x['number'], x['time'], x['text'])
            if x['_valid']:
                x['source'] = SRC_CURRENT
            elif k in valid_keys or k in seen_old:
                x['source'] = SRC_DUPLICATE
            else:
                seen_old.add(k)
                x['source'] = SRC_RECOVERED
        self.sms = sorted([x for x in rows if x['source'] != SRC_DUPLICATE],
                          key=lambda x: (x['time'] or '9999', x['nv_id']))
        for x in self.sms:
            del x['_valid']
        rec = sum(1 for x in self.sms if x['source'] == SRC_RECOVERED)
        self.log(t('log.sms', n=len(self.sms), recovered=rec))

    # ---------------------------------------------------------------- calls
    def parse_calls(self):
        calls = {}
        for it in self.items[SZ_CALLS]:
            r = it.data
            entries = []
            ok = True
            for e in range(10):
                x = r[e * 132:(e + 1) * 132]
                ln = x[5]
                if not ln:
                    continue
                cnt, typ = struct.unpack_from('<HH', x, 0x7a)
                if ln > 20 or cnt > 10:
                    ok = False
                    break
                entries.append((x, ln, cnt, typ))
            if not ok or not entries:
                continue
            for x, ln, cnt, typ in entries:
                num = bcd(x[6:6 + ln])
                if ((x[4] & 0x70) == 0x10 or x[0x1c] == 1) and not num.startswith('00'):
                    num = '+' + num
                tss = struct.unpack_from('<10I', x, 0x24)
                durs = struct.unpack_from('<10I', x, 0x4c)
                for k in range(cnt):
                    if not tss[k]:
                        continue
                    when = EPOCH_1980 + dt.timedelta(seconds=tss[k])
                    if not 2000 <= when.year <= 2100:
                        continue
                    key = (num, tss[k])
                    c = calls.setdefault(key, dict(time=when.strftime('%Y-%m-%d %H:%M:%S'), number=num,
                                                   duration_s=durs[k], type_code=typ, nv_id=f'0x{it.id:x}',
                                                   source=SRC_RECOVERED, offset=f'0x{it.offset:x}'))
                    if it.valid:
                        c['source'] = SRC_CURRENT
                        c['offset'] = f'0x{it.offset:x}'
        self.calls = sorted(calls.values(), key=lambda x: x['time'])
        cur = sum(1 for c in self.calls if c['source'] == SRC_CURRENT)
        self.log(t('log.calls', n=len(self.calls), current=cur, recovered=len(self.calls) - cur))

    # ---------------------------------------------------------------- FAT12
    def parse_filesystem(self):
        sect = self.items[SZ_SECTOR]
        boot = None
        for it in sect:
            b = it.data
            if (it.valid and b[0] == 0xeb and b[2] == 0x90 and b[0x1fe:0x200] == b'\x55\xaa'
                    and b'FAT1' in b[0x36:0x3e]):
                boot = it
        if not boot:
            self.warn('warn.no_fat')
            self.log(t('log.fat_none'))
            return
        base = boot.id
        bps, spc, res, nf, rootn, tot, md, fsz = struct.unpack_from('<HBHBHHBH', boot.data, 11)
        if bps != SZ_SECTOR or not spc or not nf or not fsz or not tot:
            self.warn('warn.bad_sector_size', bps=bps)
            return
        by_sec = {}
        for it in sect:
            if it.valid and base <= it.id < base + tot:
                s = it.id - base
                if s in by_sec and by_sec[s].data != it.data:
                    self.warn('warn.sector_versions', sector=s)
                by_sec[s] = it
        img = bytearray(tot * bps)
        for s, it in by_sec.items():
            img[s * bps:(s + 1) * bps] = it.data
        self.disk_image = bytes(img)
        self.fat = dict(label=boot.data[3:11].decode('latin-1').strip(), bps=bps, spc=spc, sectors=tot,
                        present=len(by_sec), base_id=base)
        self.log(t('log.fat_found', sectors=tot, bps=bps, present=len(by_sec)))

        fat = img[res * bps:(res + fsz) * bps]
        root_off = (res + nf * fsz) * bps
        data_sec = res + nf * fsz + (rootn * 32 + bps - 1) // bps
        csz = spc * bps

        def nxt(c):
            o = c * 3 // 2
            if o + 2 > len(fat):                 # damaged / truncated FAT: end of chain
                return 0xfff
            v = struct.unpack_from('<H', fat, o)[0]
            return v >> 4 if c & 1 else v & 0xfff

        def coff(c):
            return (data_sec + (c - 2) * spc) * bps

        def chain(c, limit):
            out, seen = [], set()
            while 2 <= c < 0xff0 and c not in seen and len(out) * csz < limit + csz:
                seen.add(c)
                out.append(c)
                c = nxt(c)
            return out

        def entries(buf):
            lfn = []
            for i in range(0, len(buf) - 31, 32):
                e = buf[i:i + 32]
                if e[0] == 0:
                    break
                if e[11] == 0x0f:
                    ch = e[1:11] + e[14:26] + e[28:32]
                    lfn.insert(0, ch.decode('utf-16le', errors='replace').split('\x00')[0].replace('￿', ''))
                    continue
                long_ = ''.join(lfn)
                lfn = []
                if e[11] & 0x08:
                    continue
                deleted = e[0] == 0xe5
                name = e[0:8].decode('latin-1').rstrip()
                ext = e[8:11].decode('latin-1').rstrip()
                if deleted:
                    name = '_' + name[1:]
                short = name + ('.' + ext if ext else '')
                tm, dd = struct.unpack_from('<HH', e, 22)
                ts = (f"{1980 + (dd >> 9)}-{(dd >> 5) & 15:02d}-{dd & 31:02d} "
                      f"{tm >> 11:02d}:{(tm >> 5) & 63:02d}:{(tm & 31) * 2:02d}")
                yield dict(name=(long_ or short), short=short, attr=e[11],
                           clus=struct.unpack_from('<H', e, 26)[0],
                           size=struct.unpack_from('<I', e, 28)[0], ts=ts, deleted=deleted)

        def walk(buf, path, depth):
            for e in entries(buf):
                if e['short'] in ('.', '..'):
                    continue
                p = path + '/' + e['name'].replace('/', '_').replace('\\', '_')
                if e['attr'] & 0x10:
                    if e['deleted'] or depth > 8 or e['clus'] < 2:
                        continue
                    sub = b''.join(img[coff(c):coff(c) + csz] for c in chain(e['clus'], 1 << 20))
                    walk(sub, p, depth + 1)
                    continue
                got = None
                if e['deleted']:
                    # FAT chain is gone: assume contiguous clusters (content may be overwritten)
                    n = (e['size'] + csz - 1) // csz
                    data = (bytes(img[coff(e['clus']):coff(e['clus']) + n * csz])[:e['size']]
                            if e['clus'] >= 2 else b'')
                    status = FILE_DELETED
                else:
                    data = b''.join(img[coff(c):coff(c) + csz] for c in chain(e['clus'], e['size']))[:e['size']]
                    status = FILE_OK if len(data) == e['size'] else FILE_INCOMPLETE
                    got = len(data)
                self.files.append(dict(path=p, size=e['size'], date=e['ts'], deleted=e['deleted'],
                                       status=status, got=got, data=data))

        walk(bytes(img[root_off:root_off + rootn * 32]), '', 0)
        ok = sum(1 for f in self.files if not f['deleted'])
        self.log(t('log.files', active=ok, deleted=len(self.files) - ok))

    # ---------------------------------------------------------------- helpers for output
    def contact_lookup(self):
        m = {}
        for c in self.contacts:
            if c['number']:
                m.setdefault(normalize_number(c['number']), c['name'])
        return m

    def sms_messages(self):
        """Join multi-part SMS into single messages. Adds 'parts' ("2/3") and 'missing' (list of part numbers).

        Parts of one message may come from current and recovered (older) copies; they are joined together.
        The joined message is 'recovered' if any of its parts is. The 8/16-bit reference repeats over time,
        so a part number that is already present starts a new message."""
        groups = collections.OrderedDict()
        out = []
        for x in self.sms:
            if x['ref'] is not None and x['total'] and x['total'] > 1:
                k = (x['direction'], x['number'], x['ref'], x['total'])
                bucket = groups.setdefault(k, [[]])
                if any(p['part'] == x['part'] for p in bucket[-1]):
                    bucket.append([])
                bucket[-1].append(x)
            else:
                out.append(dict(x, parts='', missing=[]))
        for k, buckets in groups.items():
            total = k[3]
            for parts in buckets:
                parts.sort(key=lambda p: p['part'] or 0)
                have = {p['part'] for p in parts}
                first = dict(min(parts, key=lambda p: p['time'] or '9999'))
                first['text'] = ''.join(p['text'] for p in parts)
                first['parts'] = f"{len(have)}/{total}"
                first['missing'] = [i for i in range(1, total + 1) if i not in have]
                if any(p['source'] != SRC_CURRENT for p in parts):
                    first['source'] = SRC_RECOVERED
                out.append(first)
        out.sort(key=lambda x: (x['time'] or '9999', x['nv_id']))
        return out
