def test_library_load_sheets_reads_named_files_and_reports(tmp_path, capsys, monkeypatch):
    from app import library_load_sheets

    sample = tmp_path / "test-compound-9.txt"
    sample.write_text(
        "Test-Compound-9\n\nWhat Is Test-Compound-9?\n\nA made-up narrative for testing.\n",
        encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["library_load_sheets", str(sample)])
    library_load_sheets.main()
    output = capsys.readouterr().out
    assert "created" in output.lower() or "Test-Compound-9" in output


def test_library_load_sheets_skips_files_that_do_not_look_like_a_sheet(tmp_path, capsys, monkeypatch):
    from sqlalchemy import select

    from app import library_load_sheets
    from app.db import SessionLocal
    from app.models import Peptide

    valid = tmp_path / "valid.txt"
    valid.write_text(
        "Test-Compound-9\n\nWhat Is Test-Compound-9?\n\nA made-up narrative for testing.\n",
        encoding="utf-8")
    invalid = tmp_path / "not-a-sheet.txt"
    invalid.write_text("Random unrelated text\nwith no headers.\n", encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["library_load_sheets", str(valid), str(invalid)])
    library_load_sheets.main()
    output = capsys.readouterr().out

    assert "skip" in output.lower()
    assert str(invalid) in output or invalid.name in output

    with SessionLocal() as s:
        assert s.scalar(select(Peptide).where(Peptide.name == "Test-Compound-9")) is not None
        assert s.scalar(select(Peptide).where(Peptide.name == "Random unrelated text")) is None
