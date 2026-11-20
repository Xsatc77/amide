"""Text going into a spreadsheet or CSV file: a cell that starts with = + - @ (or a tab or carriage return) is read by Excel, Sheets and
LibreOffice as a formula, so a name someone typed could run one when the file is opened. A leading apostrophe makes it plain text."""

_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def cell(value):
    """The value, with a leading apostrophe when it is text a spreadsheet would read as a formula. Numbers, dates and None pass through."""
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def row(values) -> list:
    return [cell(v) for v in values]
