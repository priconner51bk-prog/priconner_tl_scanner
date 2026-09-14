"""Tests for the single-column writer used by every monitor stage."""

import gspread_utils


class FakeSheet:
    def __init__(self, column_values):
        self.column_values = list(column_values)
        self.cells = {}
        self.ranges = []

    def col_values(self, column):
        assert column == 1
        return list(self.column_values)

    def update_cell(self, row, column, value):
        self.cells[(row, column)] = value

    def update(self, values, range_name, value_input_option="USER_ENTERED"):
        self.ranges.append((values, range_name))


def test_write_fills_trailing_empty_rows():
    sheet = FakeSheet(["header", "a", "b"])
    rows = gspread_utils.writeToFirstEmptyCells(sheet, ["x", "y"], column=1)
    assert rows == [4, 5]
    assert sheet.ranges == [([["x"], ["y"]], "A4:A5")]


def test_write_uses_existing_empty_rows_then_trailing():
    sheet = FakeSheet(["header", "a", "", "c"])
    rows = gspread_utils.writeToFirstEmptyCells(sheet, ["x", "y", "z"], column=1)
    assert rows == [3, 5, 6]
    assert (3, 1) in sheet.cells and sheet.cells[(3, 1)] == "x"
    assert sheet.ranges == [([["y"], ["z"]], "A5:A6")]


def test_write_skips_falsy_values_and_returns_empty():
    sheet = FakeSheet(["header"])
    assert gspread_utils.writeToFirstEmptyCells(sheet, ["", None, ""], column=1) == []
    assert not sheet.ranges and not sheet.cells


def test_column_letter_single_and_double():
    assert gspread_utils._column_letter(1) == "A"
    assert gspread_utils._column_letter(26) == "Z"
    assert gspread_utils._column_letter(27) == "AA"
    assert gspread_utils._column_letter(702) == "ZZ"
    assert gspread_utils._column_letter(703) == "AAA"
