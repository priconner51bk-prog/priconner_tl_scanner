from unittest.mock import patch

import tl_formatting


def test_extract_tl_text_filters_message_header():
    content = "今日のTLです\n1:20 アオイ→UB\n1:05 ネラ UB\nhttps://youtu.be/abc"
    assert tl_formatting.extract_tl_text(content) == "1:20 アオイ→UB\n1:05 ネラ UB"


def test_format_discord_tl_uses_formatter_api():
    with patch.object(tl_formatting, "_load_formatter", return_value=lambda text: "FORMATTED") as load:
        assert tl_formatting.format_discord_tl("1:20 アオイ UB\n1:05 ネラ UB") == "FORMATTED"
        load.assert_called_once()
