"""Conservative relevance scoring for Princess Connect videos."""

import re
import unicodedata
from datetime import datetime as DateTime, timezone

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

CLAN_BATTLE_TITLE_PATTERN = re.compile(
    r"(?i)(?:クラバト|クランバトル|clan\s*battle|[1-5]\s*段階|"
    r"[1-5]\s*ボス|\bD[1-5]\d{1,2}P?\b|\bEX\s*[1-5]\b)"
)
EXPLICIT_BATTLE_MONTH_PATTERN = re.compile(
    r"(?P<month>1[0-2]|[1-9])\s*月(?:の)?\s*(?:クランバトル|クラバト)",
    re.IGNORECASE,
)

# These defaults keep filtering fail-closed if a local run cannot read the
# spreadsheet.  The authoritative editable list is the NGワード worksheet.
DEFAULT_NG_TERMS = (
    "原神",
    "崩壊スターレイル",
    "ブルーアーカイブ",
    "学マス",
    "ウマ娘",
    "モンスト",
    "パズドラ",
    "ヘブンバーンズレッド",
    "ヘブバン",
    "valorant",
    "minecraft",
    "イベント攻略",
    "復刻SP",
    "総力戦",
)
BOSS_ALIAS_TERMS = ("メドューサ",)


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


def load_ng_terms(spreadsheet=None):
    """Load enabled video NG terms from the shared spreadsheet."""
    try:
        if spreadsheet is None:
            import gspread_utils
            spreadsheet = gspread_utils.getNewArrivalsSheet()
        rows = spreadsheet.worksheet("NGワード").get_all_values()
    except Exception as error:  # noqa: BLE001 - keep the collector available
        print(f"警告: NGワードシートを読めないため既定値を使用: {error}")
        return tuple(DEFAULT_NG_TERMS)

    terms = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        term = str(row[1] or "").strip()
        enabled = len(row) < 4 or str(row[3] or "有効").strip().lower() not in {
            "0", "false", "no", "無効", "off",
        }
        if term and enabled:
            terms.append(term)
    return tuple(dict.fromkeys((*DEFAULT_NG_TERMS, *terms)))


def load_content_period(spreadsheet, content_month, fallback_start="", fallback_end=""):
    """Load the active battle period for the configured content month."""
    try:
        rows = spreadsheet.worksheet("クラバト期間").get_all_values()
    except Exception as error:  # noqa: BLE001 - config fallback is intentional
        print(f"警告: クラバト期間シートを読めないため設定値を使用: {error}")
        return fallback_start, fallback_end
    for row in rows[1:]:
        if len(row) < 6 or str(row[0]).strip() != str(content_month).strip():
            continue
        if str(row[5] or "TRUE").strip().lower() in {"0", "false", "no", "無効", "off"}:
            continue
        return str(row[1]).strip(), str(row[2]).strip()
    return fallback_start, fallback_end


def _video_text(video):
    def field(name, default=""):
        if isinstance(video, dict):
            return video.get(name, default)
        return getattr(video, name, default)

    title = _normalize(field("title"))
    description = _normalize(field("description"))[:MAX_DESCRIPTION_LENGTH]
    tags = " ".join(_normalize(tag) for tag in field("tags", ()) or ())
    return title, f"{title} {description} {tags}".strip()


def _has_content_month_conflict(title, content_month):
    if not content_month:
        return False
    try:
        target_month = int(str(content_month).split("-", 1)[1])
    except (IndexError, TypeError, ValueError):
        return False
    match = EXPLICIT_BATTLE_MONTH_PATTERN.search(title)
    return bool(match and int(match.group("month")) != target_month)


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, DateTime):
        parsed = value
    else:
        try:
            parsed = DateTime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _video_field(video, name, default=None):
    if isinstance(video, dict):
        return video.get(name, default)
    return getattr(video, name, default)


def _has_content_period_conflict(video, content_period_start, content_period_end):
    published = _parse_datetime(_video_field(video, "publish_date"))
    start = _parse_datetime(content_period_start)
    end = _parse_datetime(content_period_end)
    if not start and not end:
        return False
    if published is None:
        return True
    if start and published < start:
        return True
    if end and published >= end:
        return True
    return False


def is_in_content_period(value, content_period_start, content_period_end):
    """Return whether a timestamp is inside the configured half-open period."""
    parsed = _parse_datetime(value)
    start = _parse_datetime(content_period_start)
    end = _parse_datetime(content_period_end)
    if parsed is None or (not start and not end):
        return False
    return (not start or parsed >= start) and (not end or parsed < end)


def clan_battle_filter_reason(
    video,
    boss_names=(),
    ng_terms=(),
    content_month="",
    content_period_start="",
    content_period_end="",
):
    """Return a rejection reason, or an empty string when the video is allowed."""
    title, combined = _video_text(video)
    normalized_ng = tuple(_normalize(term) for term in ng_terms if str(term).strip())
    hit = next((term for term in normalized_ng if term in combined), "")
    if hit:
        return f"NGワード: {hit}"
    if _has_content_period_conflict(
        video, content_period_start, content_period_end
    ):
        return "クラバト本戦期間外または公開日時不明"
    if _has_content_month_conflict(title, content_month):
        return "クラバト内容月が対象月外"
    if (
        not _contains_any(combined, POSITIVE_GAME_TERMS)
        and not _contains_any(combined, boss_names)
        and not _contains_any(combined, BOSS_ALIAS_TERMS)
    ):
        return "プリコネ動画ではない"
    has_boss = _contains_any(title, (*boss_names, *BOSS_ALIAS_TERMS))
    has_battle_marker = bool(CLAN_BATTLE_TITLE_PATTERN.search(title))
    if not (has_boss or has_battle_marker):
        return "クラバト動画と判定できない"
    return ""


def is_allowed_clan_battle_video(
    video,
    boss_names=(),
    ng_terms=(),
    content_month="",
    content_period_start="",
    content_period_end="",
):
    return not clan_battle_filter_reason(
        video,
        boss_names,
        ng_terms,
        content_month,
        content_period_start,
        content_period_end,
    )


def is_clan_battle_video(video):
    """Return whether a title has an accepted clan-battle marker."""
    return bool(CLAN_BATTLE_TITLE_PATTERN.search(str(getattr(video, "title", "") or "")))
