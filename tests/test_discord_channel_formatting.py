from discord_channel import _source_channel_label
from republish_discord_august import (
    SECTION_SEPARATOR,
    _extract_source_text,
    _md_block,
    build_post,
)
from tl_formatting import extract_tl_text


def test_source_channel_label_prefers_human_name():
    assert _source_channel_label("888415796617965618") == "四段セミオ_②"


def test_extract_tl_text_keeps_time_and_formation_rows_together():
    content = """説明
0:49　タマキ
－〇〇－〇
0:40　スミレ
〇〇〇－〇
動画URL https://youtu.be/example
"""
    assert extract_tl_text(content) == (
        "0:49　タマキ\n－〇〇－〇\n0:40　スミレ\n〇〇〇－〇"
    )


def test_republish_uses_only_original_fenced_tl():
    body = (
        "Discord投稿本文: TP8-2\n```cs\n1:18　タマキ\n〇〇〇〇〇\n"
        "1:00　シオリ\n〇〇〇〇〇\n```\n"
        "[54321]🅰️OFF\n1:18　タマキ\n[54321]"
    )
    source = _extract_source_text(body)
    assert source == "TP8-2\n1:18　タマキ\n〇〇〇〇〇\n1:00　シオリ\n〇〇〇〇〇"
    post = build_post(
        {
            "key": "discord://source/1",
            "message_id": "1541014939827834930",
            "source_channel_id": "888415796617965618",
            "source_channel_name": "四段セミオ_②",
            "source_time": __import__("datetime").datetime(2026, 8, 23),
            "source_url": "https://discord.com/channels/883/888415796617965618/1",
            "body": body,
        }
    )
    assert post["content"].count("```") == 4
    assert "TL（原文）:\n```md\n" in post["content"]
    assert "TL（整形済み）:\n```md\n" in post["content"]
    assert "TL（原文）:" in post["content"]
    assert "TL（整形済み）:" in post["content"]
    assert post["content"].count(SECTION_SEPARATOR) == 0
    assert "チャンネル: [四段セミオ_②](https://discord.com/channels/883/888415796617965618/1)" in post["content"]
    assert "元投稿:" not in post["content"]
    assert "重複キー" not in post["content"]


def test_republish_does_not_duplicate_video_url_already_in_source_body():
    url = "https://www.youtube.com/watch?v=6cTeVxmVsIE"
    post = build_post(
        {
            "key": "discord://source/2",
            "message_id": "1541768842127220886",
            "source_channel_id": "888415796617965618",
            "source_channel_name": "四段セミオ_②",
            "source_time": __import__("datetime").datetime(2026, 8, 27),
            "source_url": "https://discord.com/channels/883/888415796617965618/2",
            "body": (
                f"動画タイトル: テスト\n備考: Discordメッセージから検出\n"
                f"動画URL: {url}"
            ),
        }
    )
    assert post["content"].count(f"動画URL: {url}") == 1


def test_md_block_escapes_fences_in_original_text():
    prefix, suffix = _md_block("TL（原文）", "説明\n```md\n内部\n```")
    assert prefix.endswith("````md\n")
    assert suffix == "\n````"
