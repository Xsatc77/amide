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
