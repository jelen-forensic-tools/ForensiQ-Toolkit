"""
core/adump/ext4.py — minimal read-only ext2/3/4 reader in pure Python.

Covers what Android userdata images use: extents (incl. multi-level trees),
legacy block maps, 64-bit group descriptors, flex_bg, meta_bg, htree
directories (read linearly), inline data (i_block part) and the
file-based-encryption flag (detected, not decrypted).
"""
from __future__ import annotations

import datetime as dt
import stat
import struct
from dataclasses import dataclass
from typing import Iterator, Optional

INCOMPAT_FILETYPE = 0x2
INCOMPAT_META_BG = 0x10
INCOMPAT_EXTENTS = 0x40
INCOMPAT_64BIT = 0x80
INCOMPAT_INLINE_DATA = 0x8000
INCOMPAT_ENCRYPT = 0x10000
INCOMPAT_CASEFOLD = 0x20000
RO_COMPAT_SPARSE_SUPER = 0x1

FL_ENCRYPT = 0x800
FL_EXTENTS = 0x80000
FL_INLINE_DATA = 0x10000000

ROOT_INO = 2


class Ext4Error(Exception):
    pass


@dataclass
class Inode:
    ino: int
    mode: int
    size: int
    flags: int
    links: int
    atime: int
    ctime: int
    mtime: int
    crtime: int
    dtime: int
    block: bytes

    @property
    def is_dir(self) -> bool:
        return stat.S_ISDIR(self.mode)

    @property
    def is_file(self) -> bool:
        return stat.S_ISREG(self.mode)

    @property
    def is_link(self) -> bool:
        return stat.S_ISLNK(self.mode)

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & FL_ENCRYPT)


@dataclass
class DirEntry:
    name: str
    ino: int
    ftype: int


def fmt_ts(ts: int) -> str:
    if not ts:
        return ""
    try:
        return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


class Ext4:
    def __init__(self, reader):
        self.r = reader
        sb = reader.read(1024, 1024)
        if len(sb) < 0x100 or struct.unpack_from("<H", sb, 0x38)[0] != 0xEF53:
            raise Ext4Error("ext4 superblock not found")
        self.sb = sb
        u32 = lambda o: struct.unpack_from("<I", sb, o)[0]
        self.block_size = 1024 << u32(0x18)
        self.blocks_per_group = u32(0x20)
        self.inodes_per_group = u32(0x28)
        self.inodes_count = u32(0x0)
        self.first_data_block = u32(0x14)
        rev = u32(0x4C)
        self.inode_size = struct.unpack_from("<H", sb, 0x58)[0] if rev >= 1 else 128
        self.feat_compat = u32(0x5C)
        self.feat_incompat = u32(0x60)
        self.feat_ro = u32(0x64)
        blocks = u32(0x4)
        self.is64 = bool(self.feat_incompat & INCOMPAT_64BIT)
        if self.is64:
            blocks |= u32(0x150) << 32
        self.blocks_count = blocks
        ds = struct.unpack_from("<H", sb, 0xFE)[0]
        self.desc_size = ds if self.is64 and ds >= 64 else 32
        self.first_meta_bg = u32(0x104)
        self.group_count = (blocks - self.first_data_block + self.blocks_per_group - 1) // self.blocks_per_group
        if not self.block_size or not self.inodes_per_group or self.block_size > 65536:
            raise Ext4Error("invalid ext4 superblock")
        self._gdt_cache: dict[int, bytes] = {}
        self._inode_cache: dict[int, Inode] = {}

    # ── metadata ────────────────────────────────────────────────────────

    @property
    def encrypt_feature(self) -> bool:
        return bool(self.feat_incompat & INCOMPAT_ENCRYPT)

    def info(self) -> dict:
        sb = self.sb
        u32 = lambda o: struct.unpack_from("<I", sb, o)[0]
        uid = sb[0x68:0x78].hex()
        return {
            "volume_name": sb[0x78:0x88].split(b"\x00")[0].decode("utf-8", "replace"),
            "last_mounted_dir": sb[0x88:0xC8].split(b"\x00")[0].decode("utf-8", "replace"),
            "uuid": f"{uid[:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:]}",
            "block_size": self.block_size,
            "size_bytes": self.blocks_count * self.block_size,
            "last_mount_time": fmt_ts(u32(0x2C)),
            "last_write_time": fmt_ts(u32(0x30)),
            "created": fmt_ts(u32(0x108)),
            "mount_count": struct.unpack_from("<H", sb, 0x34)[0],
            "fbe_feature": self.encrypt_feature,
        }

    def _has_super(self, group: int) -> bool:
        if not self.feat_ro & RO_COMPAT_SPARSE_SUPER or group in (0, 1):
            return True
        for base in (3, 5, 7):
            n = base
            while n < group:
                n *= base
            if n == group:
                return True
        return False

    def _desc(self, group: int) -> bytes:
        per_block = self.block_size // self.desc_size
        if self.feat_incompat & INCOMPAT_META_BG and group // per_block >= self.first_meta_bg:
            mg = group // per_block
            first_group = mg * per_block
            blk = self.first_data_block + first_group * self.blocks_per_group + (1 if self._has_super(first_group) else 0)
            idx = group % per_block
        else:
            blk = self.first_data_block + 1 + group // per_block
            idx = group % per_block
        if blk not in self._gdt_cache:
            self._gdt_cache[blk] = self.r.read(blk * self.block_size, self.block_size)
        b = self._gdt_cache[blk]
        return b[idx * self.desc_size:(idx + 1) * self.desc_size]

    def inode(self, ino: int) -> Inode:
        if ino in self._inode_cache:
            return self._inode_cache[ino]
        if not 1 <= ino <= self.inodes_count:
            raise Ext4Error(f"bad inode {ino}")
        g, idx = divmod(ino - 1, self.inodes_per_group)
        d = self._desc(g)
        table = struct.unpack_from("<I", d, 0x8)[0]
        if self.desc_size >= 64:
            table |= struct.unpack_from("<I", d, 0x28)[0] << 32
        raw = self.r.read(table * self.block_size + idx * self.inode_size, self.inode_size)
        if len(raw) < 128:
            raise Ext4Error(f"inode {ino} out of image")
        u16 = lambda o: struct.unpack_from("<H", raw, o)[0]
        u32 = lambda o: struct.unpack_from("<I", raw, o)[0]
        mode = u16(0)
        size = u32(0x4) | (u32(0x6C) << 32)
        crtime = 0
        if self.inode_size > 128 and len(raw) >= 0x94:
            extra = u16(0x80)
            if extra >= 0x18:
                crtime = u32(0x90)
        ino_obj = Inode(ino, mode, size, u32(0x20), u16(0x1A), u32(0x8), u32(0xC), u32(0x10),
                        crtime, u32(0x14), raw[0x28:0x28 + 60])
        if len(self._inode_cache) > 20000:
            self._inode_cache.clear()
        self._inode_cache[ino] = ino_obj
        return ino_obj

    # ── data mapping ────────────────────────────────────────────────────

    def _extent_runs(self, node: bytes, depth_guard: int = 0) -> Iterator[tuple[int, int, int, bool]]:
        """Yield (logical_block, physical_block, count, uninitialized)."""
        magic, entries, _max, depth = struct.unpack_from("<HHHH", node, 0)
        if magic != 0xF30A or depth_guard > 8:
            raise Ext4Error("bad extent header")
        for i in range(entries):
            o = 12 + i * 12
            if o + 12 > len(node):
                break
            if depth == 0:
                lblk, ln, hi, lo = struct.unpack_from("<IHHI", node, o)
                uninit = ln > 32768
                if uninit:
                    ln -= 32768
                yield lblk, (hi << 32) | lo, ln, uninit
            else:
                _lblk, lo, hi = struct.unpack_from("<IIH", node, o)
                child = self.r.read(((hi << 32) | lo) * self.block_size, self.block_size)
                yield from self._extent_runs(child, depth_guard + 1)

    def _blockmap_runs(self, block: bytes) -> Iterator[tuple[int, int, int, bool]]:
        ptrs = struct.unpack("<15I", block)
        per = self.block_size // 4
        lblk = 0

        def walk(ptr, level):
            nonlocal lblk
            if level == 0:
                if ptr:
                    yield lblk, ptr, 1, False
                lblk += 1
                return
            span = per ** level
            if not ptr:
                lblk += span
                return
            children = struct.unpack(f"<{per}I", self.r.read(ptr * self.block_size, self.block_size).ljust(self.block_size, b"\x00"))
            for c in children:
                yield from walk(c, level - 1)

        for p in ptrs[:12]:
            yield from walk(p, 0)
        for lvl, p in ((1, ptrs[12]), (2, ptrs[13]), (3, ptrs[14])):
            yield from walk(p, lvl)

    def runs(self, ino: Inode):
        if ino.flags & FL_EXTENTS:
            return self._extent_runs(ino.block)
        return self._blockmap_runs(ino.block)

    def iter_data(self, ino: Inode, chunk: int = 4 << 20) -> Iterator[bytes]:
        """Stream file content (holes and uninitialized extents → zeros)."""
        size = ino.size
        if ino.flags & FL_INLINE_DATA:
            yield ino.block[:min(size, 60)]
            return
        if ino.is_link and size < 60 and not ino.flags & FL_EXTENTS:
            yield ino.block[:size]
            return
        bs = self.block_size
        pos = 0
        runs = sorted(self.runs(ino), key=lambda r: r[0])
        for lblk, pblk, cnt, uninit in runs:
            start = lblk * bs
            if start >= size:
                break
            if start > pos:                       # hole
                gap = start - pos
                while gap:
                    n = min(gap, chunk)
                    yield bytes(n)
                    gap -= n
                pos = start
            remain = min(cnt * bs, size - start)
            off = 0
            while remain > 0:
                n = min(remain, chunk)
                yield bytes(n) if uninit else self.r.read(pblk * bs + off, n).ljust(n, b"\x00")
                off += n
                remain -= n
                pos += n
        if pos < size:
            tail = size - pos
            while tail:
                n = min(tail, chunk)
                yield bytes(n)
                tail -= n

    def read_file(self, ino: Inode, limit: int = 512 << 20) -> bytes:
        if ino.size > limit:
            raise Ext4Error(f"file too large ({ino.size} B)")
        return b"".join(self.iter_data(ino))

    # ── directories / paths ─────────────────────────────────────────────

    def listdir(self, ino: Inode) -> list[DirEntry]:
        if not ino.is_dir:
            raise Ext4Error("not a directory")
        out: list[DirEntry] = []
        if ino.flags & FL_INLINE_DATA:
            data = ino.block
            parent = struct.unpack_from("<I", data, 0)[0]
            out.append(DirEntry("..", parent, 2))
            self._parse_dirents(data[4:], out)
            return out
        bs = self.block_size
        data = self.read_file(ino, limit=256 << 20)
        for o in range(0, len(data), bs):
            self._parse_dirents(data[o:o + bs], out)
        return out

    def _parse_dirents(self, blk: bytes, out: list[DirEntry]):
        o = 0
        n = len(blk)
        has_ftype = bool(self.feat_incompat & INCOMPAT_FILETYPE)
        while o + 8 <= n:
            ino, rec_len, name_len, ftype = struct.unpack_from("<IHBB", blk, o)
            if rec_len < 8 or o + rec_len > n:
                break
            if not has_ftype:
                name_len |= ftype << 8
                ftype = 0
            if ino and name_len and o + 8 + name_len <= n:
                raw = blk[o + 8:o + 8 + name_len]
                name = raw.decode("utf-8", errors="surrogateescape")
                if name not in (".", ".."):
                    out.append(DirEntry(name, ino, ftype))
            o += rec_len

    def lookup(self, path: str) -> Optional[Inode]:
        """Resolve an absolute path inside the file system; None if missing."""
        cur = self.inode(ROOT_INO)
        for comp in [c for c in path.strip("/").split("/") if c]:
            if not cur.is_dir:
                return None
            try:
                entries = self.listdir(cur)
            except Ext4Error:
                return None
            nxt = next((e for e in entries if e.name == comp), None)
            if nxt is None:                            # case-folded dirs
                nxt = next((e for e in entries if e.name.lower() == comp.lower()), None)
            if nxt is None:
                return None
            try:
                cur = self.inode(nxt.ino)
            except Ext4Error:
                return None
        return cur

    def walk(self, path: str, max_depth: int = 32) -> Iterator[tuple[str, DirEntry, Inode]]:
        """Yield (full_path, entry, inode) for everything below `path` (no symlink following)."""
        start = self.lookup(path)
        if start is None or not start.is_dir:
            return
        stack = [(path.rstrip("/"), start, 0)]
        seen = {start.ino}
        while stack:
            base, ino, depth = stack.pop()
            try:
                entries = self.listdir(ino)
            except Ext4Error:
                continue
            for e in entries:
                if e.name == "..":
                    continue
                try:
                    child = self.inode(e.ino)
                except Ext4Error:
                    continue
                full = f"{base}/{e.name}"
                yield full, e, child
                if child.is_dir and child.ino not in seen and depth < max_depth:
                    seen.add(child.ino)
                    stack.append((full, child, depth + 1))
