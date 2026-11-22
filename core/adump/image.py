"""
core/adump/image.py — read-only access to Android physical dump images.

Supports:
  • raw images (full eMMC/UFS LUN with GPT, or a single partition image)
  • Android sparse images (fastboot / simg, magic 0xED26FF3A)
Detects file systems at partition starts (ext4, F2FS, EROFS, FAT) and flags
high-entropy partitions (likely full-disk encryption).
"""
from __future__ import annotations

import bisect
import math
import os
import struct
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

SPARSE_MAGIC = 0xED26FF3A
EXT4_MAGIC = 0xEF53
F2FS_MAGIC = 0xF2F52010
EROFS_MAGIC = 0xE0F5E1E2


# ── Readers ──────────────────────────────────────────────────────────────────

class RawImage:
    """Thread-safe random-access reader over a file (never writes)."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "rb")
        self._lock = threading.Lock()
        self.size = os.path.getsize(path)

    def read(self, off: int, n: int) -> bytes:
        if off < 0 or n <= 0 or off >= self.size:
            return b""
        with self._lock:
            self._f.seek(off)
            return self._f.read(min(n, self.size - off))

    def close(self):
        self._f.close()


class SparseImage:
    """Android sparse image presented as the expanded raw image."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "rb")
        self._lock = threading.Lock()
        hdr = self._f.read(28)
        (magic, _maj, _min, fhs, chs, bsz, total_blks, total_chunks, _crc) = struct.unpack("<IHHHHIIII", hdr)
        if magic != SPARSE_MAGIC:
            raise ValueError("not a sparse image")
        self.block_size = bsz
        self.size = total_blks * bsz
        self._starts: list[int] = []       # output byte offset of each chunk
        self._chunks: list[tuple] = []     # (kind, length_bytes, file_offset | fill_bytes)
        pos = fhs
        out = 0
        for _ in range(total_chunks):
            self._f.seek(pos)
            ctype, _r, csz, tsz = struct.unpack("<HHII", self._f.read(12))
            data_off = pos + chs
            length = csz * bsz
            if ctype == 0xCAC1:            # raw
                self._add(out, ("raw", length, data_off))
            elif ctype == 0xCAC2:          # fill (4-byte pattern)
                self._f.seek(data_off)
                self._add(out, ("fill", length, self._f.read(4)))
            elif ctype == 0xCAC3:          # don't care → zeros
                self._add(out, ("zero", length, None))
            elif ctype == 0xCAC4:          # crc32, no output data
                length = 0
            else:
                raise ValueError(f"bad sparse chunk type 0x{ctype:x}")
            out += length
            pos += tsz

    def _add(self, out, chunk):
        if chunk[1]:
            self._starts.append(out)
            self._chunks.append(chunk)

    def read(self, off: int, n: int) -> bytes:
        if off < 0 or n <= 0 or off >= self.size:
            return b""
        n = min(n, self.size - off)
        res = bytearray()
        i = bisect.bisect_right(self._starts, off) - 1
        with self._lock:
            while n > 0 and 0 <= i < len(self._chunks):
                kind, length, extra = self._chunks[i]
                start = self._starts[i]
                rel = off - start
                take = min(n, length - rel)
                if kind == "raw":
                    self._f.seek(extra + rel)
                    res += self._f.read(take)
                elif kind == "fill":
                    pat = extra * ((rel % 4 + take) // 4 + 2)
                    res += pat[rel % 4: rel % 4 + take]
                else:
                    res += bytes(take)
                off += take
                n -= take
                i += 1
        if n > 0:
            res += bytes(n)
        return bytes(res)

    def close(self):
        self._f.close()


class SubReader:
    """Window into a parent reader (one partition)."""

    def __init__(self, parent, offset: int, size: int):
        self.parent = parent
        self.offset = offset
        self.size = size

    def read(self, off: int, n: int) -> bytes:
        if off < 0 or n <= 0 or off >= self.size:
            return b""
        return self.parent.read(self.offset + off, min(n, self.size - off))


def open_image(path: str):
    with open(path, "rb") as f:
        head = f.read(4)
    if len(head) == 4 and struct.unpack("<I", head)[0] == SPARSE_MAGIC:
        return SparseImage(path)
    return RawImage(path)


# ── Partitions ───────────────────────────────────────────────────────────────

@dataclass
class Partition:
    index: int
    name: str
    offset: int
    size: int
    fs: str = "unknown"
    info: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        gb = self.size / 1_073_741_824
        sz = f"{gb:.1f} GB" if gb >= 1 else f"{self.size / 1_048_576:.0f} MB"
        return f"{self.name or '#' + str(self.index)}  [{self.fs}, {sz}]"


def _guid(b: bytes) -> str:
    return str(uuid.UUID(bytes_le=b))


def parse_gpt(img) -> tuple[list[Partition], int]:
    """Return (partitions, sector_size); empty list when no GPT is found."""
    for ss in (512, 4096):
        hdr = img.read(ss, 92)
        if hdr[:8] != b"EFI PART":
            continue
        entries_lba, n_entries, esz = struct.unpack_from("<QII", hdr, 0x48)
        if not (0 < n_entries <= 1024 and 128 <= esz <= 1024):
            continue
        raw = img.read(entries_lba * ss, n_entries * esz)
        parts = []
        for i in range(n_entries):
            e = raw[i * esz:(i + 1) * esz]
            if len(e) < 128 or e[:16] == bytes(16):
                continue
            first, last = struct.unpack_from("<QQ", e, 32)
            name = e[56:128].decode("utf-16le", errors="replace").split("\x00")[0]
            if last < first:
                continue
            p = Partition(len(parts) + 1, name, first * ss, (last - first + 1) * ss)
            p.info["type_guid"] = _guid(e[:16])
            parts.append(p)
        return parts, ss
    return [], 512


def parse_mbr(img) -> list[Partition]:
    mbr = img.read(0, 512)
    if len(mbr) < 512 or mbr[510:512] != b"\x55\xaa":
        return []
    parts = []
    for i in range(4):
        e = mbr[446 + i * 16:446 + (i + 1) * 16]
        ptype = e[4]
        lba, cnt = struct.unpack_from("<II", e, 8)
        if ptype in (0, 0xEE) or not cnt:
            continue
        parts.append(Partition(len(parts) + 1, f"mbr{i + 1}", lba * 512, cnt * 512,
                               info={"mbr_type": f"0x{ptype:02x}"}))
    return parts


def detect_fs(reader) -> tuple[str, dict]:
    """Identify the file system at the start of `reader`."""
    sb = reader.read(1024, 1024)
    info: dict = {}
    if len(sb) >= 0x80 and struct.unpack_from("<H", sb, 0x38)[0] == EXT4_MAGIC:
        info["volume"] = sb[0x78:0x88].split(b"\x00")[0].decode("utf-8", "replace")
        info["last_mounted"] = sb[0x88:0xC8].split(b"\x00")[0].decode("utf-8", "replace")
        return "ext4", info
    if len(sb) >= 4 and struct.unpack_from("<I", sb, 0)[0] == F2FS_MAGIC:
        return "f2fs", info
    if len(sb) >= 4 and struct.unpack_from("<I", sb, 0)[0] == EROFS_MAGIC:
        return "erofs", info
    boot = reader.read(0, 512)
    if len(boot) == 512 and boot[510:512] == b"\x55\xaa" and (b"FAT" in boot[0x36:0x3E] or b"FAT32" in boot[0x52:0x5A]):
        return "vfat", info
    if not reader.read(0, 4096).strip(b"\x00"):
        return "empty", info
    if entropy(reader.read(0, 65536)) > 7.9:
        return "encrypted?", info
    return "unknown", info


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    cnt = [0] * 256
    for x in b:
        cnt[x] += 1
    n = len(b)
    return -sum(c / n * math.log2(c / n) for c in cnt if c)


def scan_ext4(img, on_progress: Optional[Callable[[float], None]] = None,
              stop: Optional[Callable[[], bool]] = None) -> list[Partition]:
    """Fallback for dumps without a partition table: find ext4 (512-aligned) and
    F2FS (4 KiB-aligned) superblocks anywhere in the image."""
    found = []
    chunk = 64 << 20
    pos = 0
    pat = struct.pack("<H", EXT4_MAGIC)
    pat_f2fs = struct.pack("<I", F2FS_MAGIC)
    while pos < img.size:
        if stop and stop():
            break
        data = img.read(pos, chunk + 2048)
        i = data.find(pat)
        while i != -1 and i < chunk:
            start = pos + i - 0x438
            if start >= 0 and start % 512 == 0 and not any(start == p.offset for p in found):
                sb = img.read(start + 1024, 256)
                log_bs = struct.unpack_from("<I", sb, 0x18)[0]
                blocks = struct.unpack_from("<I", sb, 0x4)[0]
                ipg = struct.unpack_from("<I", sb, 0x28)[0]
                if log_bs <= 6 and blocks and ipg and struct.unpack_from("<I", sb, 0x4C)[0] <= 1:
                    size = min(blocks * (1024 << log_bs), img.size - start)
                    found.append(Partition(len(found) + 1, f"ext4@0x{start:x}", start, size, "ext4"))
            i = data.find(pat, i + 1)
        # F2FS superblock: magic at partition start + 1024, partitions are 4 KiB aligned
        j = data.find(pat_f2fs)
        while j != -1 and j < chunk:
            start = pos + j - 1024
            # skip the backup superblock (block 1 of a volume already found)
            if start >= 0 and start % 4096 == 0 and not any(start in (p.offset, p.offset + 4096) for p in found):
                sb = img.read(start + 1024, 0x60)
                log_bs, blocks = struct.unpack_from("<I", sb, 0x10)[0], struct.unpack_from("<Q", sb, 0x24)[0]
                if log_bs == 12 and blocks:
                    size = min(blocks * 4096, img.size - start)
                    found.append(Partition(len(found) + 1, f"f2fs@0x{start:x}", start, size, "f2fs"))
            j = data.find(pat_f2fs, j + 1)
        pos += chunk
        if on_progress:
            on_progress(min(1.0, pos / img.size))
    return found


def list_partitions(img, log: Callable[[str], None] = lambda m: None,
                    scan: bool = True, stop=None, on_progress=None) -> tuple[str, list[Partition]]:
    """Return (layout, partitions). layout: 'gpt' | 'mbr' | 'single' | 'scan' | 'none'."""
    parts, _ss = parse_gpt(img)
    layout = "gpt" if parts else ""
    if not parts:
        fs, info = detect_fs(img)
        if fs in ("ext4", "f2fs", "erofs", "vfat"):
            p = Partition(1, "image", 0, img.size, fs, info)
            return "single", [p]
        parts = parse_mbr(img)
        layout = "mbr" if parts else ""
    if not parts and scan:
        parts = scan_ext4(img, on_progress=on_progress, stop=stop)
        layout = "scan" if parts else "none"
    if not parts and img.size:
        # no table, no file system: still show the whole image (e.g. FDE-encrypted userdata)
        fs, info = detect_fs(img)
        return "none", [Partition(1, "image", 0, img.size, fs, info)]
    for p in parts:
        if p.fs == "unknown":
            p.fs, extra = detect_fs(SubReader(img, p.offset, p.size))
            p.info.update(extra)
    return layout or "none", parts


USERDATA_NAMES = ("userdata", "data", "user_data", "udata")


def pick_userdata(parts: list[Partition]) -> Optional[Partition]:
    for p in parts:
        if p.name.lower() in USERDATA_NAMES:
            return p
    cands = [p for p in parts if p.fs in ("ext4", "f2fs", "encrypted?")]
    return max(cands, key=lambda p: p.size) if cands else None
