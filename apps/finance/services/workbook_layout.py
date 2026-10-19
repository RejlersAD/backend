"""Validate the two supported Finance layouts before mapping source cells.

The newer workbook inserts Business Unit at H; all legacy H:AE columns move
one place right. Duplicate COMPANY headers at E/F retain their distinct roles.
No other insertion or column reordering is inferred.
"""
from openpyxl.utils import column_index_from_string, get_column_letter


WORKBOOK_MAX_COLUMN = 32


def validated_column_indices(headers, expected_headers, *, header_row,
                             casefold=False, error_prefix='Unexpected header'):
    """Return physical zero-based indices for the legacy 31 logical columns."""
    def text(cell):
        value = '' if cell.value is None else str(cell.value).strip()
        return value.casefold() if casefold else value

    inserted = str(headers[7].value or '').strip().casefold() == 'business unit'
    indices = tuple(index + int(inserted and index >= 7) for index in range(31))
    for column, expected in expected_headers.items():
        index = indices[column_index_from_string(column) - 1]
        if text(headers[index]) != (expected.casefold() if casefold else expected):
            raise ValueError(f'{error_prefix} at {get_column_letter(index + 1)}{header_row}.')
    return indices
