"""Shared change tracking helpers for sheet-backed Discord posts."""

import difflib
import re
import unicodedata

_ZERO_WIDTH_CHARS = re.compile(r"[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180d\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\u3164\ufeff]")
_DISCORD_MARKDOWN_SPECIALS = re.compile(r"([\\`*_~|{}\[\]()<>#])")
_DISCORD_URL = re.compile(r"(?<!<)(https?://[^\s<>\\\[\]()]+)")
_UB_ARROW = re.compile(r"\bUB\s*>\s*", re.IGNORECASE)
_DISCORD_DETECTION_NOTE = "Discordメッセージから検出"
_DISCORD_DETECTION_NOTE_LINE = re.compile(
    r"^\s*(?:[-+*]\s+)?(?:\*\*)?備考\s*[:：]\s*(?:\*\*)?\s*"
    r"Discordメッセージから検出\s*$"
)
_DETECTION_TIMESTAMP_LINE = re.compile(
    r"(?m)^[ \t]*(?:[-+]\s+)?検出日時:[^\r\n]*(?:\r?\n|$)"
)
POST_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━" * 3


def add_post_separator(content):
    """Prefix a published message with a visible, shared boundary line."""
    content = str(content or "").strip()
    if not content or content == POST_SEPARATOR:
        return POST_SEPARATOR
    if content.startswith(f"{POST_SEPARATOR}\n"):
        return content
    return f"{POST_SEPARATOR}\n{content}"


def markdown_note_line(notes):
    """Render a safe Markdown notes line for Discord posts.

    The label is intentionally Markdown-formatted, while the value is
    escaped so notes containing Markdown syntax cannot alter the rest of the
    post or render accidental links, emphasis, code, or spoilers.
    """
    value = _DISCORD_MARKDOWN_SPECIALS.sub(r"\\\1", str(notes or '').strip())
    value = suppress_discord_embeds(value)
    return f"**備考:** {value}"


def suppress_discord_embeds(value):
    """Keep URLs clickable while preventing Discord link previews."""
    text = str(value or "")

    def wrap(match):
        url = match.group(1)
        trailing = ""
        while url and url[-1] in ".,!?;:、。" :
            trailing = url[-1] + trailing
            url = url[:-1]
        return f"<{url}>{trailing}"

    return _DISCORD_URL.sub(wrap, text)


def normalize_comparison_text(text):
    """Normalize text used to decide whether a post materially changed."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = _ZERO_WIDTH_CHARS.sub("", normalized)
    if _DISCORD_DETECTION_NOTE_LINE.fullmatch(normalized):
        normalized = f"備考: {_DISCORD_DETECTION_NOTE}"
    return " ".join(normalized.split()).strip()


def normalize_lines(text):
    return [normalize_comparison_text(line) for line in str(text or "").splitlines()
            if line.strip()]


def remove_discord_detection_note(text):
    """Remove the generated detection note when it is repeated in source text."""
    lines = [
        line for line in str(text or "").splitlines()
        if not _DISCORD_DETECTION_NOTE_LINE.fullmatch(line)
    ]
    return "\n".join(lines).strip()


def changed_lines(previous, current):
    """Return an ordered unified diff for changed post lines."""
    old = normalize_lines(previous)
    new = normalize_lines(current)
    diff = difflib.ndiff(old, new)
    lines = []
    for line in diff:
        if line.startswith("+ "):
            lines.append(f"追加: {line[2:]}")
        elif line.startswith("- "):
            lines.append(f"削除: {line[2:]}")
    return "\n".join(lines)


def git_diff_lines(previous, current):
    """Return changed lines using conventional ``-``/``+`` diff markers."""
    lines = []
    for line in changed_lines(previous, current).splitlines():
        if line.startswith("削除: "):
            lines.append(f"- {line[4:]}")
        elif line.startswith("追加: "):
            lines.append(f"+ {line[4:]}")
    return "\n".join(lines)


def remove_ub_arrow(text):
    """Remove the source-sheet-only ``UB >`` routing marker."""
    return _UB_ARROW.sub("", str(text or ""))


def remove_detection_timestamp(text):
    """Remove scan-time metadata from material post comparisons and diffs."""
    return _DETECTION_TIMESTAMP_LINE.sub("", str(text or ""))


def update_record(record, existing_row, content_index=1, hash_index=2):
    """Mark a record new/updated and retain the content immediately before posting."""
    if not existing_row:
        record["status"] = "new"
        return record
    old_content = existing_row[content_index] if len(existing_row) > content_index else ""
    old_hash = existing_row[hash_index] if len(existing_row) > hash_index else ""
    if old_hash == record.get("hash"):
        return None
    record["status"] = "updated"
    record["previous_text"] = old_content
    return record


def post_content(record, content_key="text"):
    """Use full content for new posts and diff plus current content for updates."""
    current = remove_ub_arrow(record.get(content_key, ""))
    if record.get("status") != "updated" or record.get("force_full"):
        return current
    diff = git_diff_lines(
        remove_detection_timestamp(remove_ub_arrow(record.get("previous_text", ""))),
        remove_detection_timestamp(current),
    )
    if diff:
        return f"【差分】\n{diff}\n\n【現行本文】\n{current}".strip()
    return f"【差分なし】\n【現行本文】\n{current}".strip()


def comparison_text(*parts):
    """Join only mutable, post-visible fields for update detection."""
    return "\n".join(
        remove_detection_timestamp(part).strip()
        for part in parts
        if remove_detection_timestamp(part).strip()
    )
