from unittest.mock import patch

import tl_formatting


def test_extract_tl_text_filters_message_header():
    content = "今日のTLです\n1:20 アオイ→UB\n1:05 ネラ UB\nhttps://youtu.be/abc"
    assert tl_formatting.extract_tl_text(content) == "1:20 アオイ→UB\n1:05 ネラ UB"


def test_extract_tl_text_keeps_tl_across_standalone_url():
    content = "0:49 タマキ\nhttps://youtu.be/abc\n0:40 スミレ"
    assert tl_formatting.extract_tl_text(content) == "0:49 タマキ\n0:40 スミレ"


def test_extract_tl_text_returns_one_contiguous_block_not_scattered_matches():
    content = """説明欄の補足 4:02 は動画時間です
オート設定の説明
0:49　タマキ
－〇〇－〇
0:40　スミレ
〇〇〇－〇
参考URL https://example.test/reference
1:20　別ブロック
1:05　別ブロック
"""
    assert tl_formatting.extract_tl_text(content) == (
        "0:49　タマキ\n－〇〇－〇\n0:40　スミレ\n〇〇〇－〇"
    )


def test_extract_tl_text_keeps_marker_continuations_inside_block():
    content = """0:49　タマキ
→　UB
0:40　スミレ
通常攻撃後に発動
"""
    assert tl_formatting.extract_tl_text(content) == (
        "0:49　タマキ\n→　UB\n0:40　スミレ\n通常攻撃後に発動"
    )


def test_extract_tl_text_keeps_stars_and_supplement_lines_at_block_end():
    content = """0:49　タマキ
0:40　スミレ
☆ 手動確認
※補足: 最速入力
補足情報
https://example.test/video
#プリコネ
"""
    assert tl_formatting.extract_tl_text(content) == (
        "0:49　タマキ\n0:40　スミレ\n☆ 手動確認\n※補足: 最速入力\n補足情報"
    )


def test_extract_tl_text_keeps_final_marubatsu_formation_line():
    content = "0:49　タマキ\n✕✕✕〇✕\n0:40　スミレ\n⭕️❌⭕️❌⭕️"
    assert tl_formatting.extract_tl_text(content) == content


def test_format_discord_tl_passes_final_marubatsu_formation_to_formatter():
    content = "0:49　タマキ\n✕✕✕〇✕\n0:40　スミレ\n⭕️❌⭕️❌⭕️"
    received = []

    def formatter(source):
        received.append(source)
        return "FORMATTED"

    with patch.object(tl_formatting, "_load_formatter", return_value=formatter):
        assert tl_formatting.format_discord_tl(content) == "FORMATTED"

    assert received == [content]


def test_format_discord_tl_uses_formatter_api():
    with patch.object(tl_formatting, "_load_formatter", return_value=lambda text: "FORMATTED") as load:
        assert tl_formatting.format_discord_tl("1:20 アオイ UB\n1:05 ネラ UB") == "FORMATTED"
        load.assert_called_once()


def test_format_discord_tl_keeps_extracted_tl_when_formatter_rejects_elapsed_time():
    with patch.object(
        tl_formatting,
        "_load_formatter",
        return_value=lambda text: (_ for _ in ()).throw(ValueError("unsupported time")),
    ):
        assert tl_formatting.format_discord_tl("4:02 アオイ UB\n4:01 ネラ UB") == (
            "4:02 アオイ UB\n4:01 ネラ UB"
        )
