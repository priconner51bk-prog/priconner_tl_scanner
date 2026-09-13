"""Write newly detected items as Markdown inbox files for downstream bots."""
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import gspread_utils as gspread
from runtime_utils import default_runtime_dir


def output_dir():
    configured = gspread.get_config_value("monitor", "markdown_output_dir", "")
    return Path(configured).expanduser() if configured else default_runtime_dir() / "inbox"


def _safe(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value))[:80] or "item"


def _published_at_text(published_at):
    if not published_at:
        return ""
    isoformat = getattr(published_at, "isoformat", None)
    return isoformat() if callable(isoformat) else str(published_at)


def write_arrival(source, title, url="", published_at=None, channel_name="",
                  notes="", notes_history=None, details=None, directory=None):
    directory = Path(directory) if directory else output_dir()
    directory.mkdir(parents=True, exist_ok=True)
    detected = datetime.now(timezone.utc)
    filename = f"{detected.strftime('%Y%m%dT%H%M%S.%fZ')}_{_safe(source)}_{_safe(title)}.md"
    history = notes_history or []
    lines = ["---", f"source: {_safe(source)}", f"detected_at: {detected.isoformat()}",
             f"published_at: {_published_at_text(published_at)}", f"url: {url}",
             f"title: {title}", f"channel_name: {channel_name}", "---", "",
             f"# {title}", "", "## 動画URL", "", url or "", "",
             "## チャンネル名", "", channel_name or "", "", "## 備考", "", notes or "",
             "", "## 備考欄の更新履歴", ""]
    if history:
        lines.extend(f"- {entry}" for entry in history)
    else:
        lines.append("（更新履歴なし）")
    lines.append("")
    for key, value in (details or {}).items(): lines.extend((f"## {key}", "", str(value), ""))
    descriptor, temporary = tempfile.mkstemp(prefix=".arrival-", suffix=".md", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines)); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, directory / filename)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return directory / filename
