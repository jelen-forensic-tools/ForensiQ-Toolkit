"""
core/contacts_xlsx.py — contacts as a simple Excel sheet: name + numbers only.
Extra numbers of the same contact go into the next columns.
"""
from __future__ import annotations

from core.i18n import t


def write_name_numbers_xlsx(grouped: list[tuple[str, list[str]]], path: str) -> str:
    """grouped = [(name, [number, ...]), ...]"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    max_n = max((len(n) for _, n in grouped), default=1) or 1
    wb = Workbook()
    ws = wb.active
    ws.title = t("s30.xlsx_sheet")[:31]
    head = [t("s30.xlsx_name")] + [f"{t('s30.xlsx_number')} {i}" for i in range(1, max_n + 1)]
    ws.append(head)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="16213E")
        cell.alignment = Alignment(vertical="center")
    for name, nums in grouped:
        ws.append([name] + list(nums))
    # numbers as text – keeps leading "+" / "00" and prevents Excel from turning them into floats
    for row in ws.iter_rows(min_row=2, min_col=2):
        for cell in row:
            cell.number_format = "@"
    ws.column_dimensions["A"].width = min(60, max([len(head[0])] + [len(n or "") for n, _ in grouped]) + 4)
    for i in range(2, max_n + 2):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 18
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    return path
