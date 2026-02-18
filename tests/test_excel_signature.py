from core.search_engine import is_valid_excel_signature, search_in_excel_special


def test_excel_signature_check(tmp_path):
    # 1. Valid XLSX (ZIP header)
    valid_xlsx = tmp_path / "valid.xlsx"
    with open(valid_xlsx, "wb") as f:
        f.write(b"PK\x03\x04" + b"\x00" * 10)
    assert is_valid_excel_signature(str(valid_xlsx))

    # 2. Valid XLS (OLE2 header)
    valid_xls = tmp_path / "valid.xls"
    with open(valid_xls, "wb") as f:
        f.write(b"\xd0\xcf\x11\xe0" + b"\x00" * 10)
    assert is_valid_excel_signature(str(valid_xls))

    # 3. Invalid file (Text file renamed)
    invalid_file = tmp_path / "fake.xlsx"
    with open(invalid_file, "w") as f:
        f.write("This is just a text file")
    assert not is_valid_excel_signature(str(invalid_file))

    # 4. Search should skip invalid file
    # We mock Calamine usage by just checking if it returns SKIPPED before loading
    res = search_in_excel_special(str(invalid_file), "test", exact_match=False)
    assert res is not None
    assert res[0] == "SKIPPED"
    assert "Different" in res[1] or "header" in res[1] or "Invalid" in res[1] or "유효하지 않은" in res[1]  # type: ignore
