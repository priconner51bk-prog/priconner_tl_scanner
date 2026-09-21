from types import SimpleNamespace

from video_relevance import clan_battle_filter_reason


def video(title, description="", tags=(), publish_date=None):
    return SimpleNamespace(
        title=title,
        description=description,
        tags=list(tags),
        publish_date=publish_date,
    )


def test_rejects_other_game_from_ng_terms():
    reason = clan_battle_filter_reason(
        video("【ブルーアーカイブ】総力戦ヒエロニムス"),
        ["メデューサ"],
        ["ブルーアーカイブ"],
        "2026-08",
    )
    assert reason.startswith("NGワード")


def test_rejects_clan_battle_content_from_another_month():
    reason = clan_battle_filter_reason(
        video("【プリコネR】4段階目 ライデン【7月クランバトル】"),
        ["メデューサ"],
        [],
        "2026-08",
    )
    assert reason == "クラバト内容月が対象月外"


def test_keeps_known_boss_typo_as_boss0_candidate():
    reason = clan_battle_filter_reason(
        video("メドューサ 49053"),
        ["メデューサ"],
        [],
        "2026-08",
    )
    assert reason == ""


def test_rejects_video_outside_actual_battle_period():
    reason = clan_battle_filter_reason(
        video("【プリコネR】4段階目 メデューサ 49053", publish_date="2026-08-22T23:00:00+09:00"),
        ["メデューサ"],
        [],
        "2026-08",
        "2026-08-23T12:00:00+09:00",
        "2026-08-31T00:00:00+09:00",
    )
    assert reason == "クラバト本戦期間外または公開日時不明"
