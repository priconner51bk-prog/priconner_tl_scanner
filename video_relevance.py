"""Conservative relevance scoring for Princess Connect videos."""

import unicodedata

MAX_DESCRIPTION_LENGTH = 5000
POSITIVE_GAME_TERMS = ("プリコネ", "プリンセスコネクト", "priconne")
POSITIVE_TL_TERMS = (
    "クランバトル",
    "クラバト",
    "フルオート",
    "セミオート",
    "簡易手動",
    "編成",
    "持越し",
    "持ち越し",
    "tl",
    "ub",
)
NEGATIVE_GAME_TERMS = (
    "原神",
    "崩壊スターレイル",
    "ブルーアーカイブ",
    "学マス",
    "ウマ娘",
    "モンスト",
    "パズドラ",
    "valorant",
    "minecraft",
)


def _normalize(value):
    return unicodedata.normalize("NFKC", str(value or "")).lower()


def _contains_any(text, terms):
    return any(_normalize(term) in text for term in terms)


def relevance_score(video, boss_names=()):
    """Score a yt-dlp video using title, description, tags, and known bosses."""
    title = _normalize(getattr(video, "title", ""))
    description = _normalize(getattr(video, "description", ""))[:MAX_DESCRIPTION_LENGTH]
    tags = " ".join(_normalize(tag) for tag in getattr(video, "tags", ()) or ())
    combined = f"{title} {description} {tags}"
    score = 0
    if _contains_any(combined, POSITIVE_GAME_TERMS):
        score += 4
    if _contains_any(combined, POSITIVE_TL_TERMS):
        score += 3
    if _contains_any(combined, boss_names):
        score += 5
    if _contains_any(combined, NEGATIVE_GAME_TERMS):
        score -= 6
    return score


def is_relevant_video(video, boss_names=()):
    """Return true for likely Princess Connect/TL videos."""
    return relevance_score(video, boss_names) >= 4
