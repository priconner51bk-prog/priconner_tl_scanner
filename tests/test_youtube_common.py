from post_change_tracker import (
    POST_SEPARATOR,
    markdown_note_line,
    suppress_discord_embeds,
)
from unittest.mock import patch

from youtube_common import video_post_body


def test_video_post_body_includes_description_and_formatted_tl():
    body = video_post_body(
        "タイトル",
        "対象ボス: ボス3",
        "https://www.youtube.com/watch?v=abc",
        "概要欄の本文",
        "1:00 キャラ→UB",
    )

    assert body.startswith(f"{POST_SEPARATOR}\n")
    assert len(POST_SEPARATOR) == 60
    assert body.index("動画タイトル: タイトル") < body.index(
        "動画URL: https://www.youtube.com/watch?v=abc"
    ) < body.index("**備考:** 対象ボス: ボス3")
    assert "概要欄:\n概要欄の本文" in body
    assert "TL（整形済み）:\n```scm\n1:00 キャラ→UB\n```" in body


def test_video_post_body_formats_description_in_common_path():
    with patch("youtube_common.format_discord_tl", return_value="整形済みTL") as formatter:
        body = video_post_body(
            "タイトル",
            "登録チャンネルの新着動画",
            "https://www.youtube.com/watch?v=abc",
            "概要欄\n1:00 キャラ→UB",
        )

    formatter.assert_called_once_with("概要欄\n1:00 キャラ→UB")
    assert "TL（整形済み）:\n```scm\n整形済みTL\n```" in body


def test_video_post_body_omits_empty_notes():
    body = video_post_body("タイトル", "", "https://www.youtube.com/watch?v=abc")
    assert "備考" not in body


def test_video_post_body_preserves_url_when_middle_content_is_long():
    url = "https://www.youtube.com/watch?v=abc"
    body = video_post_body("タイトル", "備考", url, "説明" * 3000, "TL" * 3000)

    assert len(body) <= 2000
    assert body.index("動画タイトル: タイトル") < body.index(f"動画URL: {url}")


def test_markdown_note_line_formats_label_and_escapes_value_markup():
    assert markdown_note_line("**危険** [リンク](https://example.test) `code`") == (
        r"**備考:** \*\*危険\*\* \[リンク\]\(<https://example.test>\) \`code\`"
    )


def test_suppress_discord_embeds_keeps_urls_clickable_without_previews():
    assert suppress_discord_embeds(
        "備考 https://www.youtube.com/watch?v=abc, 次"
    ) == "備考 <https://www.youtube.com/watch?v=abc>, 次"
