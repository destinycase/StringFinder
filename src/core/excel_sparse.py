"""Adapt native sparse XLSX decoding without changing precise comparison policy."""


def iter_sparse_cells(workbook, name, include_empty=False):
    cells, start, end = workbook.read_sheet(name)
    if not include_empty or start is None:
        yield from cells
        return
    # Preserve the legacy normalized-empty query contract without ever building
    # a sheet rectangle. Only one cell at a time is synthesized.
    current = iter(cells)
    cell = next(current, None)
    for row in range(end[0] + 1):
        for col in range(start[1], end[1] + 1):
            if cell is not None and cell[:2] == (row, col):
                yield cell
                cell = next(current, None)
            else:
                yield row, col, ""


def iter_legacy_cells(sheet):
    if hasattr(sheet, "start"):
        if sheet.start is None:
            return
    elif getattr(sheet, "total_height", None) == 0 and getattr(sheet, "total_width", None) == 0:
        return
    offset = sheet.start[1] if getattr(sheet, "start", None) else 0
    for row, values in enumerate(sheet.iter_rows()):
        for col, value in enumerate(values):
            yield row, col + offset, value
