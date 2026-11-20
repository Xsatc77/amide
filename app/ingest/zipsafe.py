"""Safety checks for a zip that came from outside (a spreadsheet, an export): entry count, compression ratio, and a
byte count taken while decompressing. The size a zip's directory claims is only a claim, so reading is always bounded
by what actually comes out."""

import re
import zipfile
import zlib

MAX_ENTRIES = 2000
MAX_RATIO = 200                    # uncompressed : compressed, for any entry big enough to matter
_RATIO_FLOOR = 64 * 1024           # tiny parts can compress extremely well without being a threat
_CHUNK = 1 << 16
_DAMAGED = (zipfile.BadZipFile, zlib.error, NotImplementedError, EOFError, RuntimeError, ValueError, OSError)   # what a damaged or odd entry raises while it is read
_XML_PART = re.compile(r".*\.(xml|rels)$", re.IGNORECASE)
_DOCTYPE = re.compile(rb"<!\s*(doctype|entity)", re.IGNORECASE)


class ZipUnsafe(ValueError):
    """The zip is too big, too crowded, too compressed or carries an entity declaration; the message is short and safe to show."""


def check(z: zipfile.ZipFile, *, max_total: int, max_entries: int = MAX_ENTRIES) -> list[zipfile.ZipInfo]:
    infos = z.infolist()
    if len(infos) > max_entries:
        raise ZipUnsafe("the file has too many parts")
    if sum(i.file_size for i in infos) > max_total:
        raise ZipUnsafe("the file is too large")
    for i in infos:
        if i.file_size > _RATIO_FLOOR and i.file_size > MAX_RATIO * max(i.compress_size, 1):
            raise ZipUnsafe("the file is compressed suspiciously well")
    return infos


def read_bounded(z: zipfile.ZipFile, info: zipfile.ZipInfo | str, limit: int) -> bytes:
    """One entry, decompressed at most `limit` bytes; more than that raises ZipUnsafe whatever the directory claims."""
    out = bytearray()
    try:
        with z.open(info) as f:
            while chunk := f.read(_CHUNK):
                out += chunk
                if len(out) > limit:
                    raise ZipUnsafe("a part of the file is too large")
    except ZipUnsafe:
        raise
    except _DAMAGED:
        raise ZipUnsafe("a part of the file is damaged") from None
    return bytes(out)


def refuse_entities(z: zipfile.ZipFile, infos: list[zipfile.ZipInfo], *, part_limit: int, total_limit: int) -> None:
    """Refuse any XML part with a DOCTYPE or ENTITY declaration (entity-expansion attacks), counting real decompressed bytes."""
    spent = 0
    for info in infos:
        if info.is_dir() or not _XML_PART.fullmatch(info.filename):
            continue
        size, tail, first = 0, b"", True
        try:
            with z.open(info) as f:
                while chunk := f.read(_CHUNK):
                    if first and chunk[:2] in (b"\xff\xfe", b"\xfe\xff"):
                        raise ZipUnsafe("the file uses an unsupported text encoding")
                    first = False
                    size += len(chunk)
                    spent += len(chunk)
                    if size > part_limit or spent > total_limit:
                        raise ZipUnsafe("a part of the file is too large")
                    if _DOCTYPE.search(tail + chunk):
                        raise ZipUnsafe("the file contains entity declarations")
                    tail = chunk[-16:]
        except ZipUnsafe:
            raise
        except _DAMAGED:
            raise ZipUnsafe("a part of the file is damaged") from None
