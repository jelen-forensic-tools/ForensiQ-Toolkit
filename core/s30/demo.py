"""Synthetic SC6531E/MOCOR flash dump generator.

Creates a small dump with FICTIONAL data that uses the same structures as a real device:
NV log blocks with contacts, SMS (incl. multi-part UCS-2 and older/invalidated copies),
call log, and a FAT12 disk with photos, a voice recording and a deleted file.

Used by the test suite and for trying the tool without real (personal) data:

    python -m sc6531_analyzer.demo demo_dump.bin
"""
import datetime as dt
import io
import random
import struct
import sys

from .analyzer import EPOCH_1980, GSM7

NV_BLOCK = 0x8000
CONTACT_ID_OFFSET = 0x566          # NV item ID = slot index + offset (as on the real device)
FAT_BASE_ID = 0x13ec               # NV item ID of FAT sector 0
SMSC = '+420608005681'


# --------------------------------------------------------------------------- encoders

def enc_bcd(digits):
    digits = digits.replace('*', 'a').replace('#', 'b')
    if len(digits) % 2:
        digits += 'f'
    return bytes(int(digits[i + 1], 16) << 4 | int(digits[i], 16) for i in range(0, len(digits), 2))


def enc_addr(number):
    """TP address field: length in digits, TON/NPI, BCD."""
    intl = number.startswith('+')
    digits = number.lstrip('+')
    return bytes([len(digits), 0x91 if intl else 0x81]) + enc_bcd(digits)


def enc_alnum_addr(text):
    septets = (len(text) * 7 + 3) // 4            # address length in semi-octets
    return bytes([septets, 0xd0]) + pack_gsm7(text)


def pack_gsm7(text, start_bit=0):
    bits = 0
    for k, ch in enumerate(text):
        bits |= GSM7.index(ch) << (start_bit + 7 * k)
    n = (start_bit + 7 * len(text) + 7) // 8
    return bits.to_bytes(n, 'little')


def enc_scts(when, tz_quarters):
    def sw(v):
        return ((v % 10) << 4) | (v // 10)
    tz = sw(abs(tz_quarters)) | (0x08 if tz_quarters < 0 else 0)
    return bytes([sw(when.year % 100), sw(when.month), sw(when.day), sw(when.hour), sw(when.minute),
                  sw(when.second), tz])


def _user_data(text, ucs2, concat):
    """Returns (DCS, UDL, UD). concat = (ref, total, part) or None."""
    udh = bytes([5, 0, 3, *concat]) if concat else b''
    if ucs2:
        ud = udh + text.encode('utf-16be')
        return 0x08, len(ud), ud
    if udh:
        start = (len(udh) * 8 + 6) // 7 * 7
        body = pack_gsm7(text, start)
        ud = udh + body[len(udh):]
        return 0x00, start // 7 + len(text), ud
    return 0x00, len(text), pack_gsm7(text)


def tpdu_deliver(sender, text, when, tz_quarters=4, ucs2=False, concat=None):
    dcs, udl, ud = _user_data(text, ucs2, concat)
    fo = 0x04 | (0x40 if concat else 0)
    oa = enc_alnum_addr(sender) if not sender.lstrip('+').isdigit() else enc_addr(sender)
    return bytes([fo]) + oa + bytes([0, dcs]) + enc_scts(when, tz_quarters) + bytes([udl]) + ud


def tpdu_submit(dest, text, mr=1, ucs2=False, concat=None):
    dcs, udl, ud = _user_data(text, ucs2, concat)
    fo = 0x11 | (0x40 if concat else 0)
    return bytes([fo, mr]) + enc_addr(dest) + bytes([0, dcs, 0xff, udl]) + ud


def sms_record(tpdu, status, saved=None):
    r = bytearray(0xe8)
    r[4] = status
    smsc = enc_bcd(SMSC.lstrip('+'))
    r[6] = 1 + len(smsc)
    r[7] = 0x91
    r[8:8 + len(smsc)] = smsc
    assert 18 + len(tpdu) <= 0xdf, 'TPDU too long'
    r[18:18 + len(tpdu)] = tpdu
    if saved:
        r[0xdf:0xe5] = bytes([saved.year - 1980, saved.month, saved.day, saved.hour, saved.minute, saved.second])
    return bytes(r)


def contact_record(idx, name='', number='', typ=1):
    r = bytearray(0x38)
    struct.pack_into('<HH', r, 0, idx, 1)
    if number:
        b = enc_bcd(number.lstrip('+'))
        r[9], r[10], r[11] = typ, len(b), 0x11 if number.startswith('+') else 0x01
        r[12:12 + len(b)] = b
    if name:
        try:
            nb, enc = name.encode('ascii'), 0
        except UnicodeEncodeError:
            nb, enc = name.encode('utf-16le'), 1
        nb = nb[:20]
        r[0x20], r[0x21] = enc, len(nb)
        r[0x22:0x22 + len(nb)] = nb
    return bytes(r)


def call_entry(number, calls, typ):
    """calls = [(datetime, duration_s), ...] (max 10)."""
    x = bytearray(132)
    intl = number.startswith('+')
    b = enc_bcd(number.lstrip('+'))
    x[4], x[5] = 0x01, len(b)
    x[6:6 + len(b)] = b
    x[0x1c] = 1 if intl else 0
    x[0x20] = 1
    for k, (when, dur) in enumerate(calls[:10]):
        struct.pack_into('<I', x, 0x24 + 4 * k, int((when - EPOCH_1980).total_seconds()))
        struct.pack_into('<I', x, 0x4c + 4 * k, dur)
    struct.pack_into('<HH', x, 0x7a, len(calls[:10]), typ)
    return bytes(x)


def call_item(entries):
    data = b''.join(entries)
    return data + bytes(0x528 - len(data))


# --------------------------------------------------------------------------- FAT12

class Fat12:
    BPS, SPC, RES, NF, ROOTN, TOT, FSZ = 1024, 2, 1, 1, 512, 160, 1

    def __init__(self):
        self.img = bytearray(self.TOT * self.BPS)
        self.data_sec = self.RES + self.NF * self.FSZ + self.ROOTN * 32 // self.BPS
        self.csz = self.SPC * self.BPS
        self.next_free = 2
        self.fat = {0: 0xff8, 1: 0xfff}
        b = bytearray(self.BPS)
        b[0:3] = b'\xeb\x3c\x90'
        b[3:11] = b'SPRD    '
        struct.pack_into('<HBHBHHBH', b, 11, self.BPS, self.SPC, self.RES, self.NF, self.ROOTN, self.TOT, 0xf8,
                         self.FSZ)
        b[0x26] = 0x29
        b[0x2b:0x36] = b'NO NAME    '
        b[0x36:0x3e] = b'FAT12   '
        b[0x1fe:0x200] = b'\x55\xaa'
        self.img[0:self.BPS] = b
        self.root = bytearray()

    def coff(self, c):
        return (self.data_sec + (c - 2) * self.SPC) * self.BPS

    def alloc(self, data, link=True):
        n = max(1, (len(data) + self.csz - 1) // self.csz)
        first = self.next_free
        for i in range(n):
            c = first + i
            if link:
                self.fat[c] = c + 1 if i < n - 1 else 0xfff
            self.img[self.coff(c):self.coff(c) + self.csz] = data[i * self.csz:(i + 1) * self.csz].ljust(self.csz,
                                                                                                         b'\0')
        self.next_free += n
        return first

    @staticmethod
    def _dosdt(when):
        return ((when.hour << 11) | (when.minute << 5) | (when.second // 2),
                ((when.year - 1980) << 9) | (when.month << 5) | when.day)

    @staticmethod
    def entry(short11, attr, clus, size, when, deleted=False):
        e = bytearray(32)
        e[0:11] = short11
        e[11] = attr
        tm, dd = Fat12._dosdt(when)
        struct.pack_into('<HHHH', e, 14, tm, dd, dd, 0)
        struct.pack_into('<HHHI', e, 22, tm, dd, clus, size)
        if deleted:
            e[0] = 0xe5
        return bytes(e)

    @staticmethod
    def lfn(longname, short11, deleted=False):
        ck = 0
        for ch in short11:
            ck = (((ck & 1) << 7) + (ck >> 1) + ch) & 0xff
        units = [ord(c) for c in longname] + [0]
        units += [0xffff] * (-len(units) % 13)
        chunks = [units[i:i + 13] for i in range(0, len(units), 13)]
        out = []
        for i, ch in enumerate(chunks, 1):
            e = bytearray(32)
            e[0] = 0xe5 if deleted else (i | (0x40 if i == len(chunks) else 0))
            e[11], e[13] = 0x0f, ck
            raw = b''.join(struct.pack('<H', u) for u in ch)
            e[1:11], e[14:26], e[28:32] = raw[0:10], raw[10:22], raw[22:26]
            out.append(bytes(e))
        return b''.join(reversed(out))

    def write(self):
        fat = bytearray(self.FSZ * self.BPS)
        for c, v in self.fat.items():
            o = c * 3 // 2
            cur = struct.unpack_from('<H', fat, o)[0]
            cur = (cur & 0x000f) | (v << 4) if c & 1 else (cur & 0xf000) | v
            struct.pack_into('<H', fat, o, cur)
        self.img[self.RES * self.BPS:(self.RES + self.FSZ) * self.BPS] = fat
        ro = (self.RES + self.NF * self.FSZ) * self.BPS
        self.img[ro:ro + len(self.root)] = self.root
        return bytes(self.img)


def _jpeg(seed, label):
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    w, h = 240, 320
    c1 = [rnd.randint(30, 200) for _ in range(3)]
    c2 = [rnd.randint(30, 200) for _ in range(3)]
    im = Image.new('RGB', (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        f = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c1[i] * (1 - f) + c2[i] * f) for i in range(3)))
    for _ in range(6):
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(15, 60)
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255), width=3)
    d.rectangle([20, h - 70, w - 20, h - 25], fill=(0, 0, 0))
    d.text((32, h - 58), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=85)
    return buf.getvalue()


def _wav(seconds=1, rate=8000):
    import math
    samples = b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                       for i in range(seconds * rate))
    return (b'RIFF' + struct.pack('<I', 36 + len(samples)) + b'WAVEfmt ' +
            struct.pack('<IHHIIHH', 16, 1, 1, rate, rate * 2, 2, 16) + b'data' + struct.pack('<I', len(samples)) +
            samples)


def build_disk():
    """Returns (disk image bytes, dict of expected files {path: bytes}, deleted file path)."""
    fs = Fat12()
    d0 = dt.datetime(2026, 3, 1, 9, 0, 0)
    photo1, photo2, deleted_photo = _jpeg(1, 'DEMO photo 1'), _jpeg(2, 'DEMO photo 2'), _jpeg(3, 'deleted')
    wav = _wav()
    E = Fat12.entry

    pic = fs.next_free
    fs.alloc(b'')                                   # /Pictures
    cap = fs.next_free
    fs.alloc(b'')                                   # /Pictures/Captured photo
    rec = fs.next_free
    fs.alloc(b'')                                   # /Recordings
    c1 = fs.alloc(photo1)
    c2 = fs.alloc(photo2)
    cdel = fs.alloc(deleted_photo, link=False)      # deleted: data present, FAT chain freed
    cw = fs.alloc(wav)

    fs.root = bytearray(Fat12.lfn('Pictures', b'PICTURES   ') + E(b'PICTURES   ', 0x10, pic, 0, d0) +
                        Fat12.lfn('Recordings', b'RECORD~1   ') + E(b'RECORD~1   ', 0x10, rec, 0, d0))

    pic_dir = (E(b'.          ', 0x10, pic, 0, d0) + E(b'..         ', 0x10, 0, 0, d0) +
               Fat12.lfn('Captured photo', b'CAPTUR~1   ') + E(b'CAPTUR~1   ', 0x10, cap, 0, d0))
    t1, t2, t3 = d0.replace(hour=10), d0.replace(day=8, hour=14, minute=30), d0.replace(day=9, hour=8)
    cap_dir = (E(b'.          ', 0x10, cap, 0, d0) + E(b'..         ', 0x10, pic, 0, d0) +
               Fat12.lfn('photo0001.jpg', b'PHOTO0~1JPG') + E(b'PHOTO0~1JPG', 0x20, c1, len(photo1), t1) +
               Fat12.lfn('photo0002.jpg', b'PHOTO0~2JPG') + E(b'PHOTO0~2JPG', 0x20, c2, len(photo2), t2) +
               Fat12.lfn('photo0003.jpg', b'PHOTO0~3JPG', deleted=True) +
               E(b'PHOTO0~3JPG', 0x20, cdel, len(deleted_photo), t3, deleted=True))
    rec_dir = (E(b'.          ', 0x10, rec, 0, d0) + E(b'..         ', 0x10, 0, 0, d0) +
               Fat12.lfn('Voice_0000001.wav', b'VOICE_~1WAV') + E(b'VOICE_~1WAV', 0x20, cw, len(wav), t1))
    assert fs.next_free - 2 <= (Fat12.TOT - fs.data_sec) // Fat12.SPC, 'demo disk too small'
    for c, data in ((pic, pic_dir), (cap, cap_dir), (rec, rec_dir)):
        fs.img[fs.coff(c):fs.coff(c) + len(data)] = data

    files = {'/Pictures/Captured photo/photo0001.jpg': photo1,
             '/Pictures/Captured photo/photo0002.jpg': photo2,
             '/Recordings/Voice_0000001.wav': wav}
    # the deleted entry keeps its (deleted) long-name entries, so the analyzer reports the long name
    return fs.write(), files, ('/Pictures/Captured photo/photo0003.jpg', deleted_photo)


# --------------------------------------------------------------------------- NV log + dump

class NVLog:
    def __init__(self):
        self.items = []

    def add(self, item_id, data, valid=True):
        self.items.append((item_id, bytes(data), valid))

    def blocks(self, rnd):
        items = self.items[:]
        rnd.shuffle(items)
        out, cur = [], bytearray()
        for item_id, data, valid in items:
            rec = struct.pack('<HHHH', item_id if valid else 0, sum(data) & 0xffff, item_id, len(data)) + data
            if len(rec) % 2:
                rec += b'\xff'
            if len(cur) + len(rec) > NV_BLOCK - 16:
                out.append(bytes(cur.ljust(NV_BLOCK, b'\xff')))
                cur = bytearray()
            cur += rec
        out.append(bytes(cur.ljust(NV_BLOCK, b'\xff')))
        return out


CONTACTS = [
    # (slot, name, number, type)
    (1, 'Petr Svoboda', '777000101', 1),
    (2, 'Jana Nováková', '+420777000202', 1),          # UCS-2, longer than 10 chars -> truncated
    (3, 'Autoservis Novak', '777000303', 5),
    (4, 'Autoservis Novak', '+420311000303', 0xff),    # second number of the same contact
    (5, 'Zdravotni stredisko Lipova', '777000404', 5),  # > 20 chars -> truncated
    (6, 'Pizzerie U Mlyna', '777000505', 1),
    (8, 'Zachranka', '155', 1),
    (9, 'Info linka', '*77', 1),
    (10, 'Hotel Tatry', '+421903000606', 1),
    (12, 'Eva Malá', '777000707', 2),
    (13, 'Bez cisla', '', 1),
]
CONTACT_SLOTS = 16


def build_nv():
    """Returns (NVLog, expected dict)."""
    nv = NVLog()
    filled = {c[0]: c for c in CONTACTS}
    for slot in range(1, CONTACT_SLOTS + 1):
        if slot in filled:
            _, name, number, typ = filled[slot]
            nv.add(slot + CONTACT_ID_OFFSET, contact_record(slot, name, number, typ))
        else:
            nv.add(slot + CONTACT_ID_OFFSET, contact_record(slot))

    D = dt.datetime
    sms_id = 0x190
    long_text = 'Připomínáme termín servisu Vašeho vozidla ve čtvrtek v 8:30. Prosíme o potvrzení. Autoservis Novák'
    p1, p2 = long_text[:40], long_text[40:]
    sms = [
        (tpdu_deliver('+420777000101', 'Ahoj, dorazim v 18:00.', D(2026, 3, 2, 17, 45, 10)), 1, None, True),
        (tpdu_submit('777000101', 'Dobre, budu cekat.', mr=7), 4, D(2026, 3, 2, 17, 47, 0), True),
        (tpdu_deliver('+420777000202', p1, D(2026, 3, 5, 9, 12, 0), ucs2=True, concat=(0x35, 2, 1)), 1, None, True),
        (tpdu_deliver('+420777000202', p2, D(2026, 3, 5, 9, 12, 3), ucs2=True, concat=(0x35, 2, 2)), 1, None, True),
        (tpdu_deliver('INFO', 'Vas kredit je 250 Kc.', D(2026, 3, 6, 8, 0, 0)), 0, None, True),
        (tpdu_submit('+420777000505', 'Objednavam 2x pizzu na 19:00, dekuji.', mr=8), 4,
         D(2026, 3, 7, 18, 2, 30), True),
        # status 3 = not sent: a draft without recipient and an unsent message
        (tpdu_submit('', 'Nezapomenout koupit chleba', mr=0), 3, D(2026, 3, 8, 9, 30, 0), True),
        (tpdu_submit('+420777000202', 'Uvidime se zitra?', mr=0), 3, D(2026, 3, 8, 21, 15, 0), True),
    ]
    for tp, status, saved, valid in sms:
        nv.add(sms_id, sms_record(tp, status, saved), valid)
        sms_id += 1
    # invalidated copies: one unique (=recovered), one duplicate of a current message (=ignored)
    nv.add(sms_id, sms_record(tpdu_deliver('+420777000707', 'Tato zprava byla smazana.',
                                           D(2026, 2, 20, 12, 0, 0)), 1), valid=False)
    nv.add(0x190, sms_record(*sms[0][:2]), valid=False)
    for k in range(3):                                # empty SMS slots
        nv.add(sms_id + 1 + k, bytes(0xe8))

    calls_cur = call_item([
        call_entry('+420777000101', [(D(2026, 3, 2, 17, 50, 0), 95), (D(2026, 3, 3, 8, 15, 0), 0)], 1),
        call_entry('777000303', [(D(2026, 3, 4, 10, 0, 0), 312)], 2),
    ])
    calls_old = call_item([
        call_entry('+420777000101', [(D(2026, 3, 2, 17, 50, 0), 95)], 1),
        call_entry('+420777000999', [(D(2026, 2, 28, 20, 5, 0), 41)], 2),      # only in the old copy
    ])
    nv.add(0x537, calls_cur)
    nv.add(0x537, calls_old, valid=False)
    nv.add(0x538, call_item([call_entry('155', [(D(2026, 3, 8, 22, 10, 0), 64)], 1)]))

    disk, files, deleted = build_disk()
    for s in range(len(disk) // 1024):
        sec = disk[s * 1024:(s + 1) * 1024]
        if any(sec):
            nv.add(FAT_BASE_ID + s, sec)
    nv.add(FAT_BASE_ID + 1, bytes(1024), valid=False)          # stale copy of the FAT sector

    rnd = random.Random(7)
    for k in range(40):                                        # unrelated NV items
        nv.add(0x2000 + k, bytes(rnd.randrange(256) for _ in range(rnd.choice((0x20, 0x2a, 0x100, 0x202)))))

    expected = dict(contacts=CONTACTS, contact_slots=CONTACT_SLOTS, files=files, deleted=deleted,
                    disk=disk, sms_records=len(sms), sms_messages=len(sms) - 1, sms_recovered=1,
                    long_text=long_text, calls=5, calls_recovered=1)
    return nv, expected


def build_dump(size=0x100000, nv_offset=0x80000, seed=1):
    """Returns (dump bytes, expected dict)."""
    rnd = random.Random(seed)
    dump = bytearray(b'\xff' * size)
    fw = bytearray(rnd.getrandbits(8) for _ in range(0x40000))
    dump[:0x40000] = fw
    strings = (b'Platform Version: MOCOR_20D_6531E_DEMO\0MMI Version:       SC6531EFM_DEMO\0'
               b'HW Version:        SC6531EFM_DEMO\0')
    dump[0x1000:0x1000 + len(strings)] = strings
    # decoy: a valid-looking contact item inside the firmware (must NOT be reported)
    decoy = struct.pack('<HHHH', 0x570, 0x1234, 0x570, 0x38) + contact_record(10, 'DECOY FIRMWARE', '123456789')
    dump[0x2345:0x2345 + len(decoy)] = decoy
    nv, expected = build_nv()
    o = nv_offset
    for blk in nv.blocks(rnd):
        dump[o:o + NV_BLOCK] = blk
        o += NV_BLOCK
    assert o <= size
    return bytes(dump), expected


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else 'demo_dump.bin'
    data, _ = build_dump()
    with open(path, 'wb') as f:
        f.write(data)
    print(f'Demo dump written: {path} ({len(data):,} bytes, fictional data)')


if __name__ == '__main__':
    main()
