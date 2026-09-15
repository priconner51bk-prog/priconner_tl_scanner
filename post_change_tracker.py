"""Shared change tracking helpers for sheet-backed Discord posts."""


def normalize_lines(text):
    return [" ".join(line.split()).strip() for line in str(text or "").splitlines()
            if line.strip()]


def changed_lines(previous, current):
    """Return only lines in the current content not present in the previous post."""
    old = set(normalize_lines(previous))
    return "\n".join(line for line in normalize_lines(current) if line not in old)


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
    """Use the complete content for new posts and only additions for updates."""
    current = record.get(content_key, "")
    if record.get("status") != "updated":
        return current
    return changed_lines(record.get("previous_text", ""), current) or "（本文の変更を検出しました）"


def comparison_text(*parts):
    """Join only mutable, post-visible fields for update detection."""
    return "\n".join(str(part or "").strip() for part in parts if str(part or "").strip())
