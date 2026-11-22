"""
core/adump/f2fs.py — minimal read-only F2FS reader in pure Python.

Implements the same interface as core/adump/ext4.Ext4 (lookup, listdir, walk,
inode, iter_data, read_file, info, encrypt_feature) so the artifact parsers in
core/android_dump.py work unchanged on F2FS userdata partitions.

Covers: superblock (primary/backup), checkpoint pack selection, NAT (both copies
via the checkpoint version bitmap, incl. large NAT bitmaps and the NAT journal in
the hot-data summary), inodes with extra attributes and inline xattrs, direct /
indirect / double-indirect node blocks, inline data, inline dentries and regular
dentry blocks. Encrypted (FBE) files are detected, not decrypted; compressed files
are reported as unsupported.

On-disk layout follows include/linux/f2fs_fs.h (Linux kernel).
"""
from __future__ import annotations

import struct
from typing import Iterator, Optional

from .ext4 import DirEntry, Ext4Error, Inode, FL_ENCRYPT

F2FS_MAGIC = 0xF2F52010
SUPER_OFFSET = 1024
BLOCK = 4096

# superblock feature flags
FEAT_ENCRYPT = 0x0001
FEAT_EXTRA_ATTR = 0x0008
FEAT_FLEXIBLE_INLINE_XATTR = 0x0040
FEAT_INODE_CRTIME = 0x0100
FEAT_CASEFOLD = 0x1000
FEAT_COMPRESSION = 0x2000

# checkpoint flags
CP_COMPACT_SUM_FLAG = 0x00000004
CP_LARGE_NAT_BITMAP_FLAG = 0x00000400

# inode i_inline flags
INLINE_XATTR = 0x01
INLINE_DATA = 0x02
INLINE_DENTRY = 0x04
EXTRA_ATTR = 0x20

FADVISE_ENCRYPT = 0x04          # i_advise bit for encrypted inodes
F2FS_COMPR_FL = 0x00000004      # i_flags: compressed file

DEF_ADDRS_PER_INODE = 923
DEF_NIDS_PER_INODE = 5
ADDRS_PER_BLOCK = 1018
NIDS_PER_BLOCK = 1018
DEFAULT_INLINE_XATTR_ADDRS = 50
I_ADDR_OFF = 0x168              # offset of i_addr[] in an inode block
I_NID_OFF = I_ADDR_OFF + DEF_ADDRS_PER_INODE * 4

NULL_ADDR = 0
NEW_ADDR = 0xFFFFFFFF
COMPRESS_ADDR = 0xFFFFFFFE

NAT_ENTRY_SIZE = 9
NAT_ENTRY_PER_BLOCK = BLOCK // NAT_ENTRY_SIZE           # 455
SUM_ENTRY_SIZE = 7
ENTRIES_IN_SUM = 512
SUM_JOURNAL_SIZE = BLOCK - 5 - SUM_ENTRY_SIZE * ENTRIES_IN_SUM   # 507
NAT_JOURNAL_ENTRIES = (SUM_JOURNAL_SIZE - 2) // 13      # 38

# dentry block
DENTRY_IN_BLOCK = 214
DENTRY_BITMAP = (DENTRY_IN_BLOCK + 7) // 8               # 27
DIR_ENTRY_SIZE = 11
SLOT_LEN = 8


class F2FSError(Ext4Error):
    """Subclass of Ext4Error so callers' existing error handling covers F2FS too."""


class F2FS:
    def __init__(self, reader):
        self.r = reader
        sb = None
        for off in (SUPER_OFFSET, BLOCK + SUPER_OFFSET):        # primary, then backup
            cand = reader.read(off, 3072)
            if len(cand) >= 0x900 and struct.unpack_from("<I", cand, 0)[0] == F2FS_MAGIC:
                sb = cand
                break
        if sb is None:
            raise F2FSError("F2FS superblock not found")
        self.sb = sb
        u32 = lambda o: struct.unpack_from("<I", sb, o)[0]
        if u32(0x10) != 12:
            raise F2FSError(f"unsupported F2FS block size (log {u32(0x10)})")
        self.log_blocks_per_seg = u32(0x14)
        self.blocks_per_seg = 1 << self.log_blocks_per_seg
        self.block_count = struct.unpack_from("<Q", sb, 0x24)[0]
        self.segment_count_nat = u32(0x3C)
        self.cp_blkaddr = u32(0x4C)
        self.nat_blkaddr = u32(0x54)
        self.main_blkaddr = u32(0x5C)
        self.root_ino = u32(0x60)
        self.cp_payload = u32(0x680)
        self.features = u32(0x884)
        if not self.root_ino or not self.nat_blkaddr or not self.cp_blkaddr:
            raise F2FSError("invalid F2FS superblock")
        self._load_checkpoint()
        self._inode_cache: dict[int, tuple] = {}
        self._node_cache: dict[int, bytes] = {}

    # ── checkpoint / NAT ────────────────────────────────────────────────

    def _block(self, addr: int) -> bytes:
        b = self.r.read(addr * BLOCK, BLOCK)
        if len(b) < BLOCK:
            raise F2FSError(f"block {addr} outside the image")
        return b

    def _read_cp_pack(self, start: int):
        """Return (version, header_block, start) for a valid checkpoint pack, else None."""
        try:
            hdr = self._block(start)
        except F2FSError:
            return None
        ver = struct.unpack_from("<Q", hdr, 0)[0]
        total = struct.unpack_from("<I", hdr, 0x88)[0]
        if not 2 <= total <= self.blocks_per_seg:
            return None
        try:
            foot = self._block(start + total - 1)
        except F2FSError:
            return None
        if struct.unpack_from("<Q", foot, 0)[0] != ver:
            return None                                  # torn checkpoint
        return ver, hdr, start

    def _load_checkpoint(self):
        packs = [p for p in (self._read_cp_pack(self.cp_blkaddr),
                             self._read_cp_pack(self.cp_blkaddr + self.blocks_per_seg)) if p]
        if not packs:
            raise F2FSError("no valid F2FS checkpoint")
        self.cp_ver, cp, self.cp_start = max(packs, key=lambda p: p[0])
        self.cp = cp
        flags = struct.unpack_from("<I", cp, 0x84)[0]
        self.cp_flags = flags
        sit_bytes = struct.unpack_from("<I", cp, 0x9C)[0]
        nat_bytes = struct.unpack_from("<I", cp, 0xA0)[0]
        bm_off = 0xC0                                    # sit_nat_version_bitmap
        if flags & CP_LARGE_NAT_BITMAP_FLAG:
            self.nat_bitmap = cp[bm_off + 4:bm_off + 4 + nat_bytes]
        elif self.cp_payload > 0:
            self.nat_bitmap = cp[bm_off:bm_off + nat_bytes]
        else:
            self.nat_bitmap = cp[bm_off + sit_bytes:bm_off + sit_bytes + nat_bytes]
        self.nat_journal = self._read_nat_journal(flags)

    def _read_nat_journal(self, flags) -> dict[int, int]:
        """Recent NAT entries kept in the hot-data summary journal (override NAT blocks)."""
        start_sum = struct.unpack_from("<I", self.cp, 0x8C)[0]
        try:
            blk = self._block(self.cp_start + start_sum)
        except F2FSError:
            return {}
        off = 0 if flags & CP_COMPACT_SUM_FLAG else SUM_ENTRY_SIZE * ENTRIES_IN_SUM
        n = struct.unpack_from("<H", blk, off)[0]
        out = {}
        for i in range(min(n, NAT_JOURNAL_ENTRIES)):
            o = off + 2 + i * 13
            nid, _ver, _ino, addr = struct.unpack_from("<IBII", blk, o)
            out[nid] = addr
        return out

    def _nat_lookup(self, nid: int) -> int:
        if nid in self.nat_journal:
            return self.nat_journal[nid]
        blk_off = nid // NAT_ENTRY_PER_BLOCK
        seg_off = blk_off >> self.log_blocks_per_seg
        addr = (self.nat_blkaddr + (seg_off << self.log_blocks_per_seg << 1)
                + (blk_off & (self.blocks_per_seg - 1)))
        byte = blk_off >> 3
        if byte < len(self.nat_bitmap) and self.nat_bitmap[byte] & (0x80 >> (blk_off & 7)):
            addr += self.blocks_per_seg                  # the second NAT copy is current
        b = self._block(addr)
        _ver, _ino, baddr = struct.unpack_from("<BII", b, (nid % NAT_ENTRY_PER_BLOCK) * NAT_ENTRY_SIZE)
        return baddr

    def _node(self, nid: int) -> bytes:
        if nid in self._node_cache:
            return self._node_cache[nid]
        addr = self._nat_lookup(nid)
        if addr in (NULL_ADDR, NEW_ADDR) or addr >= self.block_count:
            raise F2FSError(f"node {nid} has no block")
        b = self._block(addr)
        if struct.unpack_from("<I", b, BLOCK - 24)[0] != nid:
            raise F2FSError(f"node {nid}: footer mismatch")
        if len(self._node_cache) > 4096:
            self._node_cache.clear()
        self._node_cache[nid] = b
        return b

    # ── metadata ────────────────────────────────────────────────────────

    @property
    def encrypt_feature(self) -> bool:
        return bool(self.features & FEAT_ENCRYPT)

    def info(self) -> dict:
        sb = self.sb
        uid = sb[0x6C:0x7C].hex()
        name = sb[0x7C:0x7C + 1024].decode("utf-16le", "replace").split("\x00")[0]
        return {
            "volume_name": name,
            "last_mounted_dir": "",
            "uuid": f"{uid[:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:]}",
            "block_size": BLOCK,
            "size_bytes": self.block_count * BLOCK,
            "last_mount_time": "",
            "last_write_time": "",
            "created": "",
            "mount_count": "",
            "fbe_feature": self.encrypt_feature,
            "fs_type": "f2fs",
            "f2fs_version": f"{struct.unpack_from('<H', sb, 4)[0]}.{struct.unpack_from('<H', sb, 6)[0]}",
            "f2fs_kernel": sb[0x684:0x784].split(b"\x00")[0].decode("utf-8", "replace").strip(),
            "checkpoint_version": self.cp_ver,
            "casefold": bool(self.features & FEAT_CASEFOLD),
            "compression_feature": bool(self.features & FEAT_COMPRESSION),
        }

    def _layout(self, raw: bytes) -> tuple[int, int, int]:
        """(first address word index, number of data addresses, inline xattr words)."""
        inline = raw[3]
        extra = struct.unpack_from("<H", raw, I_ADDR_OFF)[0] // 4 if inline & EXTRA_ATTR else 0
        # Same rule as the kernel's do_read_inode(): with flexible inline xattrs the size
        # comes from the inode; otherwise inline xattr *or* inline dentry reserves 50 words.
        if inline & EXTRA_ATTR and self.features & FEAT_FLEXIBLE_INLINE_XATTR:
            xattr = struct.unpack_from("<H", raw, I_ADDR_OFF + 2)[0]
        elif inline & (INLINE_XATTR | INLINE_DENTRY):
            xattr = DEFAULT_INLINE_XATTR_ADDRS
        else:
            xattr = 0
        n = DEF_ADDRS_PER_INODE - extra - xattr
        if not 0 < n <= DEF_ADDRS_PER_INODE:
            raise F2FSError("bad inode layout")
        return extra, n, xattr

    def inode(self, ino: int) -> Inode:
        hit = self._inode_cache.get(ino)
        if hit:
            return hit[0]
        raw = self._node(ino)
        if struct.unpack_from("<I", raw, BLOCK - 20)[0] != ino:   # footer.ino == nid for inodes
            raise F2FSError(f"node {ino} is not an inode")
        u32 = lambda o: struct.unpack_from("<I", raw, o)[0]
        u64 = lambda o: struct.unpack_from("<Q", raw, o)[0]
        mode = struct.unpack_from("<H", raw, 0)[0]
        advise, inline = raw[2], raw[3]
        i_flags = u32(0x50)
        flags = (FL_ENCRYPT if advise & FADVISE_ENCRYPT else 0)
        crtime = 0
        if inline & EXTRA_ATTR and self.features & FEAT_INODE_CRTIME:
            if struct.unpack_from("<H", raw, I_ADDR_OFF)[0] >= 20:
                crtime = u64(I_ADDR_OFF + 12)
        obj = Inode(ino, mode, u64(0x10), flags, u32(0x0C), u64(0x20), u64(0x28), u64(0x30),
                    crtime, 0, b"")
        if len(self._inode_cache) > 20000:
            self._inode_cache.clear()
        self._inode_cache[ino] = (obj, inline, i_flags)
        return obj

    # ── data mapping ────────────────────────────────────────────────────

    def _addrs(self, ino: Inode) -> Iterator[tuple[int, int]]:
        """Yield (logical_block, block_address) for every mapped block of the file."""
        raw = self._node(ino.ino)
        extra, n, _x = self._layout(raw)
        lblk = 0
        for i in range(n):
            yield lblk, struct.unpack_from("<I", raw, I_ADDR_OFF + (extra + i) * 4)[0]
            lblk += 1
        nids = struct.unpack_from("<5I", raw, I_NID_OFF)
        spans = [(0, nids[0]), (0, nids[1]), (1, nids[2]), (1, nids[3]), (2, nids[4])]
        for level, nid in spans:
            yield from self._walk_node(nid, level, lblk)
            lblk += ADDRS_PER_BLOCK * NIDS_PER_BLOCK ** level

    def _walk_node(self, nid: int, level: int, lblk: int):
        if not nid:
            return
        try:
            b = self._node(nid)
        except F2FSError:
            return                                       # hole / unreadable node → zeros
        if level == 0:
            for i in range(ADDRS_PER_BLOCK):
                yield lblk + i, struct.unpack_from("<I", b, i * 4)[0]
            return
        span = ADDRS_PER_BLOCK * NIDS_PER_BLOCK ** (level - 1)
        for i in range(NIDS_PER_BLOCK):
            child = struct.unpack_from("<I", b, i * 4)[0]
            yield from self._walk_node(child, level - 1, lblk + i * span)

    def _inline_area(self, ino: Inode) -> bytes:
        raw = self._node(ino.ino)
        extra, n, _x = self._layout(raw)
        start = I_ADDR_OFF + (extra + 1) * 4             # 1 reserved word
        return raw[start:start + (n - 1) * 4]

    def iter_data(self, ino: Inode, chunk: int = 4 << 20) -> Iterator[bytes]:
        """Stream file content (holes and unwritten blocks → zeros)."""
        self.inode(ino.ino)
        _obj, inline, i_flags = self._inode_cache[ino.ino]
        size = ino.size
        if inline & INLINE_DATA:
            yield self._inline_area(ino)[:size]
            return
        if i_flags & F2FS_COMPR_FL:
            raise F2FSError("F2FS compressed file – not supported")
        nblocks = (size + BLOCK - 1) // BLOCK
        pos = 0
        buf = bytearray()
        for lblk, addr in self._addrs(ino):
            if lblk >= nblocks:
                break
            want = min(BLOCK, size - lblk * BLOCK)
            if addr in (NULL_ADDR, NEW_ADDR) or addr >= self.block_count:
                buf += bytes(want)
            else:
                buf += self.r.read(addr * BLOCK, want).ljust(want, b"\x00")
            pos += want
            if len(buf) >= chunk:
                yield bytes(buf)
                buf.clear()
        if pos < size:                                   # trailing hole
            buf += bytes(size - pos)
        if buf:
            yield bytes(buf)

    def read_file(self, ino: Inode, limit: int = 512 << 20) -> bytes:
        if ino.size > limit:
            raise F2FSError(f"file too large ({ino.size} B)")
        return b"".join(self.iter_data(ino))

    # ── directories / paths ─────────────────────────────────────────────

    @staticmethod
    def _parse_dentries(bitmap: bytes, dentries: bytes, names: bytes, nr: int, out: list[DirEntry]):
        i = 0
        while i < nr:
            if not bitmap[i >> 3] & (1 << (i & 7)):     # little-endian bit order
                i += 1
                continue
            _hash, ino, name_len, ftype = struct.unpack_from("<IIHB", dentries, i * DIR_ENTRY_SIZE)
            slots = max(1, (name_len + SLOT_LEN - 1) // SLOT_LEN)
            raw = names[i * SLOT_LEN:i * SLOT_LEN + name_len]
            if ino and name_len and len(raw) == name_len:
                name = raw.decode("utf-8", errors="surrogateescape")
                if name not in (".", ".."):
                    out.append(DirEntry(name, ino, ftype))
            i += slots

    def listdir(self, ino: Inode) -> list[DirEntry]:
        if not ino.is_dir:
            raise F2FSError("not a directory")
        self.inode(ino.ino)
        _obj, inline, _f = self._inode_cache[ino.ino]
        out: list[DirEntry] = []
        if inline & INLINE_DENTRY:
            area = self._inline_area(ino)
            max_inline = len(area)
            nr = max_inline * 8 // ((DIR_ENTRY_SIZE + SLOT_LEN) * 8 + 1)
            bm = (nr + 7) // 8
            reserved = max_inline - ((DIR_ENTRY_SIZE + SLOT_LEN) * nr + bm)
            d0 = bm + reserved
            n0 = d0 + nr * DIR_ENTRY_SIZE
            self._parse_dentries(area[:bm], area[d0:n0], area[n0:n0 + nr * SLOT_LEN], nr, out)
            return out
        nblocks = (ino.size + BLOCK - 1) // BLOCK
        if nblocks > 65536:
            raise F2FSError("directory too large")
        d0 = DENTRY_BITMAP + 3
        n0 = d0 + DENTRY_IN_BLOCK * DIR_ENTRY_SIZE
        for lblk, addr in self._addrs(ino):
            if lblk >= nblocks:
                break
            if addr in (NULL_ADDR, NEW_ADDR) or addr >= self.block_count:
                continue
            b = self._block(addr)
            self._parse_dentries(b[:DENTRY_BITMAP], b[d0:n0], b[n0:], DENTRY_IN_BLOCK, out)
        return out

    def lookup(self, path: str) -> Optional[Inode]:
        """Resolve an absolute path inside the file system; None if missing."""
        try:
            cur = self.inode(self.root_ino)
        except F2FSError:
            return None
        for comp in [c for c in path.strip("/").split("/") if c]:
            if not cur.is_dir:
                return None
            try:
                entries = self.listdir(cur)
            except F2FSError:
                return None
            nxt = next((e for e in entries if e.name == comp), None)
            if nxt is None:                              # case-folded dirs
                nxt = next((e for e in entries if e.name.lower() == comp.lower()), None)
            if nxt is None:
                return None
            try:
                cur = self.inode(nxt.ino)
            except F2FSError:
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
            except F2FSError:
                continue
            for e in entries:
                try:
                    child = self.inode(e.ino)
                except F2FSError:
                    continue
                full = f"{base}/{e.name}"
                yield full, e, child
                if child.is_dir and child.ino not in seen and depth < max_depth:
                    seen.add(child.ino)
                    stack.append((full, child, depth + 1))
