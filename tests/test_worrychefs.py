"""Unit tests for the pure WorryChefs parsing helpers."""

import requests

import worrychefs


def test_normalize_damage_plain_number():
    assert worrychefs.normalize_damage("1234") == 123400


def test_normalize_damage_strips_m_and_plus():
    assert worrychefs.normalize_damage("1234m") == 123400
    assert worrychefs.normalize_damage("1234+") == 123400


def test_normalize_damage_keeps_value_after_last_dash():
    assert worrychefs.normalize_damage("1234-5678m") == 567800


def test_normalize_damage_decimal():
    assert worrychefs.normalize_damage("12.5m") == 1250


def test_normalize_damage_invalid_returns_cleaned_text():
    assert worrychefs.normalize_damage("abc") == "abc"


def test_translate_style_translates_known_styles_distinctly():
    known = ["Semi-Auto", "Manual", "Simple Manual", "Simple", "Auto"]
    translated = [worrychefs.translate_style(key) for key in known]
    assert all(translated_value != key for key, translated_value in zip(known, translated))
    assert len(set(translated)) == len(known)


def test_translate_style_passes_through_unknown_style():
    assert worrychefs.translate_style("???") == "???"


def test_extract_damage_entries_collects_valid_triples():
    rows = [
        ["", "D101", "1234m", "Auto", ""],
        ["D202", "5678", "Semi-Auto", ""],
        ["D1000", "1", "Auto"],
        ["D01", "1", "Auto"],
        ["D101", "", "Auto"],
    ]
    assert worrychefs.extract_damage_entries(rows) == [
        ["D101", 123400, worrychefs.translate_style("Auto")],
        ["D202", 567800, worrychefs.translate_style("Semi-Auto")],
    ]


def test_nonempty_rows_drops_blank_rows():
    assert worrychefs.nonempty_rows([["a"], ["", ""], []]) == [["a"]]


def test_build_tl_values_skips_existing_and_dedupes():
    auto = worrychefs.translate_style("Auto")
    manual = worrychefs.translate_style("Manual")
    rows = [
        ["D101", "1234m", "Auto"],
        ["D101", "1234m", "Auto"],
        ["D102", "2m", "Manual"],
    ]
    values = worrychefs.build_tl_values(rows, [f"D101,123400,{auto}"], "scan")
    assert values == [[f"D102,200,{manual}", "scan"]]


def test_parse_table_rows_reads_table_cells():
    html = (
        "<table>"
        "<tr><th>code</th><th>damage</th><th>style</th></tr>"
        "<tr><td>D101</td><td>1234m</td><td>Auto</td></tr>"
        "<tr><td></td><td></td><td></td></tr>"
        "</table>"
    )
    assert worrychefs.parse_table_rows(html) == [
        ["code", "damage", "style"],
        ["D101", "1234m", "Auto"],
    ]


def test_parse_table_rows_returns_none_without_table():
    assert worrychefs.parse_table_rows("<p>no table</p>") is None


def test_fetch_html_returns_text_on_success():
    class FakeResponse:
        text = "<html>"

        def raise_for_status(self):
            return None

    response = worrychefs.fetch_html(
        "http://example.test",
        http_get=lambda url, timeout=10: FakeResponse(),
    )
    assert response == "<html>"


def test_fetch_html_retries_then_returns_none():
    calls = []

    def failing_get(url, timeout=10):
        calls.append(url)
        raise requests.RequestException("boom")

    assert worrychefs.fetch_html(
        "http://example.test",
        http_get=failing_get,
        retries=2,
        retry_sleep=lambda seconds: None,
    ) is None
    assert len(calls) == 3


def test_manual_block_keeps_seconds_and_set_auto_off():
    rows = [
        ["1", "", "D20"],
        ["15", "1:30", "SET", "OFF", "SET", "SET", "SET", "Auto", "OFF"],
        ["16", "57", "WShiori", "", "OXOXO"],
        ["17", "55", "Tia", "STamaki SET", "OOXOO"],
        ["18", "", "D21"],
    ]
    block = worrychefs.extract_tl_blocks(rows, "manual-d2")[0]
    assert block["code"] == "D20"
    assert "0:57 WShiori" in block["text"]
    assert "1:30 OXOOO" in block["text"]
    assert "🅰️OFF" in block["text"]


def test_compare_tl_records_detects_new_and_changed_only():
    records = [
        {"key": "simple:D101", "text": "a", "hash": "new-hash", "source": "simple", "url": "u"},
        {"key": "simple:D102", "text": "b", "hash": "same", "source": "simple", "url": "u"},
    ]
    changed = worrychefs.compare_tl_records(
        [["TLキー", "TL本文", "TLハッシュ"], ["simple:D102", "old", "same"]], records
    )
    assert [item["key"] for item in changed] == ["simple:D101"]


def test_compare_tl_records_uses_stored_formatted_body_before_reformatting(monkeypatch):
    text = "0:43 シオリ [5---1] ノゾミ & シオリ , 🅰️OFF"
    monkeypatch.setattr(
        worrychefs, "format_tl_text",
        lambda _text: "0:43 シオリ [5---1] ノゾミ & シオリ ,",
    )
    record = {"key": "manual-d2:D20", "text": text, "hash": "new-hash"}
    rows = [worrychefs.TL_HEADERS, ["manual-d2:D20", text, "old-hash"]]

    assert worrychefs.compare_tl_records(rows, [record]) == []


def test_prepare_sheet_changes_preserves_first_seen_for_updates():
    record = {"key": "simple:D101", "text": "new", "hash": "h2", "source": "simple", "url": "u"}
    header, inserts, updates = worrychefs.prepare_sheet_changes(
        [["TLキー", "TL本文", "TLハッシュ", "新規検出日時"],
         ["simple:D101", "old", "h1", "first"]], [record], "now"
    )
    assert header[0] == "TLキー"
    assert inserts == []
    assert updates == [(2, ["simple:D101", "new", "h2", "first", "now", "simple", "u", "old"])]


def test_record_matches_month_treats_target_as_trial_snapshot_label():
    record = {"key": "simple:D401"}
    rows = [["TLキー", "TL本文", "TLハッシュ", "新規検出日時"],
            ["simple:D401", "text", "hash", "2026/08/31 12:00:00"]]
    assert worrychefs.record_matches_month(record, rows, "2026/08") is True
    assert worrychefs.record_matches_month(record, rows, "2026/09") is True


def test_apply_alternating_post_modes_marks_new_and_diff_update():
    records = [
        {"key": "simple:D401", "text": "new body"},
        {"key": "simple:D402", "text": "current body"},
    ]
    rows = [
        ["TLキー", "TL本文", "TLハッシュ"],
        ["simple:D401", "old body", "h1"],
        ["simple:D402", "old body", "h2"],
    ]
    assert worrychefs.apply_alternating_post_modes(records, rows) == {
        "new": 1,
        "updated": 1,
    }
    assert records[0]["status"] == "new"
    assert records[0]["force_full"] is True
    assert records[1]["status"] == "updated"
    assert records[1]["force_full"] is False
    assert records[1]["previous_text"] == "old body"
    assert worrychefs.post_tracker.post_content(records[1]) == (
        "【差分】\n- old body\n+ current body\n\n"
        "【現行本文】\ncurrent body"
    )


def test_build_worrychefs_split_messages_does_not_use_attachments():
    record = {
        "code": "D443",
        "source": "manual-d4",
        "url": "https://example.test/source",
        "status": "updated",
    }
    messages = worrychefs.build_worrychefs_split_messages(
        record, "【差分】\n- old\n+ new\n\n【現行本文】\n" + "x" * 2200, "now", limit=500
    )
    assert len(messages) > 1
    assert all("添付" not in message for message in messages)
    assert all(len(message) <= 500 for message in messages)


def test_build_worrychefs_content_keeps_source_link_in_compact_versions():
    url = "https://docs.google.com/spreadsheets/d/e/current-month/pubhtml/sheet?gid=123"
    record = {
        "code": "D102",
        "source": "simple",
        "url": url,
        "status": "updated",
        "author": "g8",
        "damage": "378.5m",
        "first_seen": "2026/09/01",
        "formation_md": "formation details",
    }

    content = worrychefs.build_worrychefs_content(record, "TL本文", "2026/09/25", 180)

    assert content is not None
    assert url in content


def test_build_formation_attachment_uses_png_not_markdown(monkeypatch):
    record = {
        "code": "D443",
        "source": "manual-d4",
        "url": "https://example.test/source",
        "image_urls": [
            "https://images.example.test/one.png",
            "https://images.example.test/two.png",
            "https://images.example.test/three.png",
            "https://images.example.test/four.png",
            "https://images.example.test/five.png",
        ],
    }
    class FakeImage:
        def getvalue(self):
            return b"png-bytes"

    monkeypatch.setattr(worrychefs, "combined_formation_image", lambda urls: FakeImage())
    assert worrychefs.build_formation_attachment(record) == (
        "formation.png",
        b"png-bytes",
        "image/png",
    )
