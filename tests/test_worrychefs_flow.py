"""End-to-end test for the WorryChefs orchestration flow."""

import json
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
        "<tr><td>D503</td><td>1234m</td><td>Auto</td></tr>"
        "<tr><td>D504</td><td>2m</td><td>Manual</td></tr>"
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
    assert any("D503" in row[0] for row in tl_sheet.inserted)
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


def test_configured_sources_do_not_fall_back_to_removed_spreadsheet_id(tmp_path):
    manifest = tmp_path / "worrychefs_sources.json"
    manifest.write_text(json.dumps({
        "spreadsheet_id": "old-published-sheet-id",
        "sources": [{"name": "simple", "gid": "171060579"}],
    }), encoding="utf-8")
    source_sheet = FakeSheet([[
        "スプレッドシートID", "gid"], ["", "171060579"]
    ])
    spreadsheet = SimpleNamespace(worksheet=lambda _name: source_sheet)

    assert worrychefs.load_worrychefs_sources(
        path=manifest, spreadsheet=spreadsheet
    ) == []


def test_configured_sources_use_only_spreadsheet_ids_in_live_allowlist(tmp_path):
    manifest = tmp_path / "worrychefs_sources.json"
    manifest.write_text(json.dumps({
        "spreadsheet_id": "old-published-sheet-id",
        "sources": [{"name": "simple", "gid": "171060579"}],
    }), encoding="utf-8")
    source_sheet = FakeSheet([[
        "スプレッドシートID", "gid"], ["new-published-sheet-id", "171060579"]
    ])
    spreadsheet = SimpleNamespace(worksheet=lambda _name: source_sheet)

    assert worrychefs.load_worrychefs_sources(
        path=manifest, spreadsheet=spreadsheet
    ) == [{
        "name": "simple",
        "gid": "171060579",
        "spreadsheet_id": "new-published-sheet-id",
    }]


def test_configured_summary_source_expands_to_published_d_tl_tabs(tmp_path):
    manifest = tmp_path / "worrychefs_sources.json"
    manifest.write_text(json.dumps({"sources": []}), encoding="utf-8")
    source_sheet = FakeSheet([
        ["スプレッドシートID", "gid"], ["published-id", "171060579"]
    ])
    spreadsheet = SimpleNamespace(worksheet=lambda _name: source_sheet)
    published_html = (
        'items.push({name: "TL Summary", pageUrl: "summary", gid: "171060579"});'
        'items.push({name: "B tier", pageUrl: "b-tier", gid: "455354528"});'
        'items.push({name: "D tier Simple TLs", pageUrl: "simple", gid: "1391911131"});'
        'items.push({name: "D tier Overtime", pageUrl: "overtime", gid: "835537673"});'
        'items.push({name: "D1 Manual", pageUrl: "manual-1", gid: "1305029905"});'
        'items.push({name: "D5 Manual", pageUrl: "manual-5", gid: "311078222"});'
        'items.push({name: "D tier Archives", pageUrl: "archives", gid: "1329829672"});'
    )

    with patch.object(worrychefs, "fetch_html", return_value=published_html):
        sources = worrychefs.load_worrychefs_sources(
            path=manifest, spreadsheet=spreadsheet
        )

    assert sources == [
        {"name": "simple", "gid": "1391911131", "spreadsheet_id": "published-id"},
        {"name": "overtime", "gid": "835537673", "spreadsheet_id": "published-id"},
        {"name": "manual-d1", "gid": "1305029905", "spreadsheet_id": "published-id"},
        {"name": "manual-d5", "gid": "311078222", "spreadsheet_id": "published-id"},
    ]


def test_configured_scan_stops_before_reading_tl_sheet_when_allowlist_is_empty():
    source_sheet = FakeSheet([[
        "スプレッドシートID", "gid"], ["", "171060579"]
    ])

    def worksheet(name):
        if name == "WorryChefs":
            return source_sheet
        raise AssertionError(f"unexpected worksheet access: {name}")

    spreadsheet = SimpleNamespace(worksheet=worksheet)
    with patch.object(worrychefs.discord, "configured_guild_keys", return_value=[]), \
            patch.object(worrychefs, "collect_worrychefs_records") as collect:
        assert worrychefs.scan_configured_worrychefs(
            spreadsheet=spreadsheet
        ) == []
    collect.assert_not_called()
