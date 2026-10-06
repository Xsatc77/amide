import io
import zipfile

import pytest

from app.backup import container
from app.backup.archive import read_archive, write_archive
from app.backup.container import BackupError, seal, unseal

FAST = 2 ** 10     # scrypt cost for tests; the real default is much higher


def test_a_sealed_file_round_trips_with_the_right_passphrase():
    blob = seal(b"hello backup", "correct horse", n=FAST)
    assert blob.startswith(b"AMIDEBK1") and b"hello backup" not in blob
    assert unseal(blob, "correct horse") == b"hello backup"


def test_each_seal_is_different_even_for_the_same_input():
    assert seal(b"x", "passphrase1", n=FAST) != seal(b"x", "passphrase1", n=FAST)


@pytest.mark.parametrize("passphrase", ["", "short", "wrong passphrase!"])
def test_a_wrong_passphrase_is_refused(passphrase):
    blob = seal(b"secret", "correct horse", n=FAST)
    with pytest.raises(BackupError, match="Wrong passphrase"):
        unseal(blob, passphrase)


def test_a_passphrase_under_eight_characters_cannot_make_a_backup():
    with pytest.raises(BackupError, match="at least 8"):
        seal(b"x", "short", n=FAST)


def test_any_change_to_the_file_is_detected_wherever_it_lands():
    blob = bytearray(seal(b"secret data here", "correct horse", n=FAST))
    for position in (9, 20, 30, len(blob) // 2, len(blob) - 1):    # header fields, ciphertext, tag
        changed = bytearray(blob)
        changed[position] ^= 0x01
        with pytest.raises(BackupError):
            unseal(bytes(changed), "correct horse")


def test_a_truncated_or_foreign_file_is_refused():
    blob = seal(b"secret", "correct horse", n=FAST)
    for bad in (blob[:20], blob[:-3], b"", b"PK\x03\x04 not a backup at all, long enough to pass the length check"):
        with pytest.raises(BackupError, match="not an Amide backup|Wrong passphrase|damaged"):
            unseal(bad, "correct horse")


def test_a_header_asking_for_an_absurd_scrypt_cost_is_refused_without_computing_it():
    blob = bytearray(seal(b"x", "correct horse", n=FAST))
    blob[8:12] = (2 ** 30).to_bytes(4, "big")
    with pytest.raises(BackupError, match="damaged"):
        unseal(bytes(blob), "correct horse")


def test_the_default_cost_is_the_real_one():
    assert container.SCRYPT_N >= 2 ** 15


# ---------------------------------------------------------------- the archive inside

def test_an_archive_round_trips_and_lists_hashes():
    zipped = write_archive({"kind": "backup"}, {"sections/a.json": b'{"x": 1}', "files/coa/f.pdf": b"%PDF"})
    archive = read_archive(zipped, max_bytes=10_000)
    assert archive.manifest["kind"] == "backup" and archive.manifest["format"] == 1
    assert archive.names() == ["files/coa/f.pdf", "sections/a.json"]
    assert archive.json("sections/a.json") == {"x": 1} and archive.read("files/coa/f.pdf") == b"%PDF"


def test_an_entry_changed_after_writing_fails_its_checksum():
    zipped = write_archive({}, {"sections/a.json": b'{"x": 1}'})
    source = zipfile.ZipFile(io.BytesIO(zipped))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for info in source.infolist():
            z.writestr(info.filename, b'{"x": 2}' if info.filename == "sections/a.json" else source.read(info.filename))
    with pytest.raises(BackupError, match="checksum"):
        read_archive(out.getvalue(), max_bytes=10_000)


def test_an_unlisted_extra_entry_is_refused():
    zipped = write_archive({}, {"sections/a.json": b"{}"})
    out = io.BytesIO(zipped)
    with zipfile.ZipFile(out, "a") as z:
        z.writestr("sections/smuggled.json", b"{}")
    with pytest.raises(BackupError, match="damaged"):
        read_archive(out.getvalue(), max_bytes=10_000)


@pytest.mark.parametrize("name", ["../evil", "/abs", "a/../b", "back\\slash", "", "manifest.json"])
def test_unsafe_entry_names_cannot_be_written(name):
    with pytest.raises(BackupError, match="Invalid entry name"):
        write_archive({}, {name: b"x"})


def test_an_oversized_archive_is_refused_before_it_is_read():
    zipped = write_archive({}, {"sections/big.json": b"0" * 50_000})
    with pytest.raises(BackupError, match="larger than"):
        read_archive(zipped, max_bytes=10_000)


def test_garbage_is_not_an_archive():
    with pytest.raises(BackupError, match="damaged"):
        read_archive(b"this is not a zip", max_bytes=10_000)


def test_any_passphrase_works_including_spaces_and_non_latin_characters():
    for passphrase in ("  spaces  around  ", "pass\u00e9\u00e8\u00ea word", "\u30d1\u30b9\u30ef\u30fc\u30c9\u30d1\u30b9\u30ef\u30fc\u30c9", "p" * 500):
        assert unseal(seal(b"data", passphrase, n=FAST), passphrase) == b"data"
