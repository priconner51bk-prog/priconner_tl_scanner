"""End-to-end test for the WorryChefs orchestration flow."""

from types import SimpleNamespace
from unittest.mock import patch

import worrychefs


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.inserted = []

    def get_all_values(self):
        return self.rows

    def col_values(self, column):
        return [row[column - 1] for row in self.rows]

    def insert_rows(self, values, **_kwargs):
        self.inserted.extend(values)

    def sort(self, *_args, **_kwargs):
        pass


def _fake_response(html):
    class FakeResponse:
        def __init__(self, text):
            self.text = text
            self.encoding = ""

        def raise_for_status(self):
            return None

    return FakeResponse(html)


def test_worrychefs_records_new_tl_and_notifies():
    html = (
        "<table>"
        "<tr><th>code</th><th>damage</th><th>style</th></tr>"
        "<tr><td>D101</td><td>1234m</td><td>Auto</td></tr>"
        "<tr><td>D102</td><td>2m</td><td>Manual</td></tr>"
        "</table>"
    )

    tl_sheet = FakeSheet([["h"] * 2])
    source_sheet = FakeSheet([["h", "h"], ["gsheetid", "123"]])
    spreadsheet = SimpleNamespace(
        worksheet=lambda name: {
            "WorryChefs TL": tl_sheet,
            "WorryChefs": source_sheet,
        }[name]
    )

    notified = []
    with patch.object(worrychefs, "write_arrival", return_value=None):
        worrychefs.checkNewArrivalsForWorryChefs(
            spreadsheet=spreadsheet,
            http_get=lambda url, timeout=10: _fake_response(html),
            notify=notified.append,
        )

    assert len(tl_sheet.inserted) == 2
    assert any("D101" in row[0] for row in tl_sheet.inserted)
    assert len(notified) == 1


def test_worrychefs_skips_when_no_valid_source():
    tl_sheet = FakeSheet([["h"] * 2])
    source_sheet = FakeSheet([["h", "h"], ["", ""]])
    spreadsheet = SimpleNamespace(
        worksheet=lambda name: {
            "WorryChefs TL": tl_sheet,
            "WorryChefs": source_sheet,
        }[name]
    )

    with patch.object(worrychefs, "write_arrival", return_value=None):
        worrychefs.checkNewArrivalsForWorryChefs(
            spreadsheet=spreadsheet,
            http_get=lambda url, timeout=10: None,
            notify=lambda *_args: None,
        )

    assert tl_sheet.inserted == []
