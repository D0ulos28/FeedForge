import os
import zlib
from hashlib import md5
from io import BytesIO
from pathlib import Path

from construct import (
    Adapter,
    Bytes,
    BytesInteger,
    Const,
    Construct,
    GreedyRange,
    Int16ub,
    Int32ub,
    Struct,
    this,
)

from collections.abc import Mapping
from .crypto import decrypt_bom, encrypt_bom, decrypt_psarc, encrypt_psarc, decrypt_sng, MAC_KEY, WIN_KEY

ENTRY = Struct(
    "md5" / Bytes(16),
    "zindex" / Int32ub,
    "length" / BytesInteger(5),
    "offset" / BytesInteger(5),
)


class BOMAdapter(Adapter):
    def _encode(self, obj, context, path):
        data = Struct(
            "entries" / ENTRY[context.n_entries], "zlength" / GreedyRange(Int16ub)
        ).build(obj)
        return encrypt_bom(data)

    def _decode(self, obj, context, path):
        data = decrypt_bom(obj)
        return Struct(
            "entries" / ENTRY[context.n_entries], "zlength" / GreedyRange(Int16ub)
        ).parse(data)


VERSION = 65540
ENTRY_SIZE = ENTRY.sizeof()
BLOCK_SIZE = 2 ** 16
ARCHIVE_FLAGS = 4

HEADER = Struct(
    "MAGIC" / Const(b"PSAR"),
    "VERSION" / Const(Int32ub.build(VERSION)),
    "COMPRESSION" / Const(b"zlib"),
    "header_size" / Int32ub,
    "ENTRY_SIZE" / Const(Int32ub.build(ENTRY_SIZE)),
    "n_entries" / Int32ub,
    "BLOCK_SIZE" / Const(Int32ub.build(BLOCK_SIZE)),
    "ARCHIVE_FLAGS" / Const(Int32ub.build(ARCHIVE_FLAGS)),
    "bom" / BOMAdapter(Bytes(this.header_size - 32)),
)


def read_entry(stream, n, bom, end_offset=None):
    entry = bom.entries[n]
    stream.seek(entry.offset)
    zlength = bom.zlength[entry.zindex :]

    data = BytesIO()
    length = 0
    for z in zlength:
        if length == entry.length:
            break

        read_size = BLOCK_SIZE if z == 0 else z
        if end_offset is not None:
            read_size = min(read_size, max(0, end_offset - stream.tell()))
        chunk = stream.read(read_size)
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            pass

        data.write(chunk)
        length += len(chunk)

    data = data.getvalue()
    assert len(data) == entry.length
    return data


def create_entry(name, data):
    zlength = []
    output = BytesIO()

    for i in range(0, len(data), BLOCK_SIZE):
        raw = data[i : i + BLOCK_SIZE]
        compressed = zlib.compress(raw, zlib.Z_BEST_COMPRESSION)
        if len(compressed) < len(raw):
            output.write(compressed)
            zlength.append(len(compressed))
        else:
            output.write(raw)
            zlength.append(len(raw) % BLOCK_SIZE)

    return {
        "md5": md5(name.encode()).digest() if name != "" else bytes(16),
        "zlength": zlength,
        "length": len(data),
        "data": output.getvalue(),
    }


def create_bom(entries):
    offset, zindex, zlength = 0, 0, []
    for entry in entries:
        entry["offset"] = offset
        entry["zindex"] = zindex
        offset += len(entry["data"])
        zindex += len(entry["zlength"])
        zlength += entry["zlength"]

    header_size = 32 + ENTRY_SIZE * len(entries) + 2 * len(zlength)
    for entry in entries:
        entry["offset"] += header_size

    return {"entries": entries, "zlength": zlength, "header_size": header_size}


class LazyPSARCContent(Mapping):
    def __init__(self, filepath, header, end_offsets, crypto):
        self.filepath = Path(filepath)
        self.header = header
        self.end_offsets = end_offsets
        self.crypto = crypto
        self._cache = {}

        # Parse listing entry (BOM index 0)
        with self.filepath.open("rb") as f:
            listing_data = read_entry(f, 0, self.header.bom, self.end_offsets[0])
        self.listing = listing_data.decode().splitlines()
        self._mapping = {name: i + 1 for i, name in enumerate(self.listing)}

    def __len__(self):
        return len(self._mapping)

    def __iter__(self):
        return iter(self._mapping)

    def __getitem__(self, key):
        if key not in self._mapping:
            raise KeyError(key)
        if key in self._cache:
            return self._cache[key]

        index = self._mapping[key]
        with self.filepath.open("rb") as f:
            data = read_entry(f, index, self.header.bom, self.end_offsets[index])
        if self.crypto:
            normalized = key.replace("\\", "/").lower()
            if "songs/bin/macos/" in normalized:
                data = decrypt_sng(data, MAC_KEY)
            elif "songs/bin/generic/" in normalized:
                data = decrypt_sng(data, WIN_KEY)

        self._cache[key] = data
        return data

    def copy(self):
        return dict(self.items())


class PSARC(Construct):
    def __init__(self, crypto=True):
        self.crypto = crypto
        super().__init__()

    def _parse(self, stream, context, path):
        header = HEADER.parse_stream(stream)
        stream.seek(0, 2)
        file_size = stream.tell()
        offsets = [entry.offset for entry in header.bom.entries]
        end_offsets = offsets[1:] + [file_size]

        # Check if it's a file stream on disk to support lazy loading
        filepath = getattr(stream, "name", None)
        if filepath and os.path.exists(filepath):
            return LazyPSARCContent(filepath, header, end_offsets, self.crypto)
        else:
            # Fallback to non-lazy dict if it's an in-memory BytesIO
            listing_data = read_entry(stream, 0, header.bom, end_offsets[0])
            listing = listing_data.decode().splitlines()
            entries = [
                read_entry(stream, i + 1, header.bom, end_offsets[i + 1])
                for i in range(header.n_entries - 1)
            ]
            content = dict(zip(listing, entries))
            if self.crypto:
                content = decrypt_psarc(content)
            return content

    def _build(self, content, stream, context, path):
        if self.crypto:
            content = encrypt_psarc(content)

        names = list(sorted(content.keys(), reverse=True))
        data = ["\n".join(names).encode()] + [content[k] for k in names]

        entries = [create_entry(n, e) for n, e in zip([""] + names, data)]
        bom = create_bom(entries)

        header = HEADER.build(
            {"header_size": bom["header_size"], "n_entries": len(entries), "bom": bom}
        )

        stream.write(header)
        for e in entries:
            stream.write(e["data"])
