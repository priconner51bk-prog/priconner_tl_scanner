from types import SimpleNamespace

from video_relevance import DEFAULT_NG_TERMS, clan_battle_filter_reason


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


def test_rejects_clan_battle_final_day_live_stream_title():
    reason = clan_battle_filter_reason(
        video("【プリコネR】9月クランバトル最終日配信!【さんだ/#個人Vtuber】"),
        ["メデューサ"],
        DEFAULT_NG_TERMS,
        "2026-09",
    )
    assert reason == "NGワード: 配信"


def test_rejects_boss_damage_streaming_slot_title():
    reason = clan_battle_filter_reason(
        video("【プリコネ】フロストハウンドを伸ばす枠"),
        ["フロストハウンド"],
        DEFAULT_NG_TERMS,
        "2026-09",
    )
    assert reason == "NGワード: 伸ばす枠"
