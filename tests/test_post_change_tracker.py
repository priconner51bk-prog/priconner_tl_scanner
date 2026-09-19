import post_change_tracker


def test_normalize_comparison_text_ignores_unicode_and_zero_width_variants():
    assert post_change_tracker.normalize_comparison_text("ＡＢ\u200b  C") == "AB C"
    assert post_change_tracker.normalize_comparison_text("同じタイトル") == "同じタイトル"


def test_changed_lines_does_not_report_equivalent_lines():
    previous = "動画タイトル: ＡＢ\u200b  C\n備考: 更新\n動画URL: https://example.test"
    current = "動画タイトル: AB C\n備考: 更新\n動画URL: https://example.test"
    assert post_change_tracker.changed_lines(previous, current) == ""


def test_git_diff_lines_uses_conventional_markers():
    assert post_change_tracker.git_diff_lines("old", "new") == "- old\n+ new"


def test_remove_ub_arrow_is_applied_to_update_diff_inputs():
    assert post_change_tracker.remove_ub_arrow("0:10 UB > SET A\n0:01 Final UB") == (
        "0:10 SET A\n0:01 Final UB"
    )


def test_force_full_update_posts_current_body_without_diff():
    record = {
        "status": "updated",
        "force_full": True,
        "previous_text": "削除される本文",
        "text": "現在の本文",
    }
    assert post_change_tracker.post_content(record) == "現在の本文"


def test_real_title_update_only_reports_title_line():
    previous = "動画タイトル: 旧タイトル\n備考: 対象ボス: ボス（更新）\n動画URL: https://example.test"
    current = "動画タイトル: 新タイトル\n備考: 対象ボス: ボス（更新）\n動画URL: https://example.test"
    assert post_change_tracker.changed_lines(previous, current) == (
        "削除: 動画タイトル: 旧タイトル\n追加: 動画タイトル: 新タイトル"
    )


def test_update_post_contains_diff_and_current_body():
    record = {
        "status": "updated",
        "previous_text": "動画タイトル: 旧タイトル",
        "text": "動画タイトル: 新タイトル",
    }
    assert post_change_tracker.post_content(record) == (
        "【差分】\n"
        "- 動画タイトル: 旧タイトル\n"
        "+ 動画タイトル: 新タイトル\n\n"
        "【現行本文】\n"
        "動画タイトル: 新タイトル"
    )


def test_equivalent_update_reposts_current_body_with_explicit_marker():
    record = {
        "status": "updated",
        "previous_text": "ＡＢ",
        "text": "AB",
    }
    assert post_change_tracker.post_content(record) == (
        "【差分なし】\n【現行本文】\nAB"
    )
