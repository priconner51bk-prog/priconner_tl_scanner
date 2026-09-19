import worrychefs


def test_translate_prefixed_and_short_character_names(monkeypatch):
    monkeypatch.setattr(
        worrychefs,
        "_character_aliases_cache",
        {
            "Shiori": "シオリ",
            "Pecorine": "ペコ",
            "Sheffy": "シェフィ",
        },
    )
    assert worrychefs._translate_sheet_character_names("WShiori SPeco WDSheffy") == "シオリ ペコ シェフィ"


def test_translate_known_names_in_notes_without_translating_prose(monkeypatch):
    monkeypatch.setattr(
        worrychefs,
        "_character_aliases_cache",
        {
            "Violet": "すみれ",
            "Sono": "ソノ",
            "Yukino": "ユキノ",
            "Tia": "ティア",
        },
    )
    source = "Tell: Fast reaction by IViolet, Sono, Yukino and Tia"
    assert worrychefs._translate_known_character_tokens(source) == (
        "Tell: Fast reaction by すみれ, ソノ, ユキノ and ティア"
    )


def test_format_tl_text_removes_ub_arrow_marker(monkeypatch):
    monkeypatch.setattr(worrychefs, "_character_aliases_cache", {})
    result = worrychefs.format_tl_text("0:10 WShiori UB > SET WShiori\n0:01 Final UB")
    assert "UB >" not in result
    assert "Final" in result
    assert "UB" in result


def test_format_tl_text_uses_configured_formatter_loader(monkeypatch):
    monkeypatch.setattr(worrychefs, "_character_aliases_cache", {})
    monkeypatch.setattr(
        "tl_formatting._load_formatter",
        lambda: lambda text, preserve_set_operations=False: text.replace("OOXOO", "[54-21]"),
    )
    assert "[54-21]" in worrychefs.format_tl_text("1:30 OOXOO")
