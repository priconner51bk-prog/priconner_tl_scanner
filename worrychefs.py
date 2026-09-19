import csv
import hashlib
import io
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
import post_change_tracker as post_tracker
from new_arrivals_markdown import write_arrival
from public_sheet_export import fetch_public_sheet
from runtime_utils import run_locked

_D_CODE = re.compile(r"^D([1-5])\d{1,2}$")
_OT_CODE = re.compile(r"^D([1-5])T\d{2}$")
_TIME = re.compile(r"^(?:\d{1,2}:\d{2}|\d{1,3})$")
_DAMAGE = re.compile(r"\d+(?:\.\d+)?m\+?", re.IGNORECASE)
_AUTHOR = re.compile(r"(?:Original\s+)?Author:\s*(.*)", re.IGNORECASE)
_ANY_CODE = re.compile(r"\bD(?:[1-5]T\d{2}|[1-5]\d{1,2})\b")


def canonicalize_tl(text):
    """Normalize TL text for stable comparison and hashing."""
    text = text.replace("->", " ")
    return "\n".join(" ".join(text.split()).strip() for text in text.splitlines() if text.strip())


def tl_hash(text):
    return hashlib.sha256(canonicalize_tl(text).encode("utf-8")).hexdigest()


TL_HEADERS = [
    "TLキー", "TL本文", "TLハッシュ", "新規検出日時", "更新検出日時",
    "種別", "元シートURL", "投稿直前本文",
]

_character_aliases_cache = None
_formation_labels_cache = {}
_formation_source_labels = {}


def _load_character_aliases():
    """Load English-name to first-alias mappings from the characters sheet."""
    global _character_aliases_cache
    if _character_aliases_cache is not None:
        return _character_aliases_cache
    spreadsheet_id = os.environ.get("CHARACTER_MASTER_SPREADSHEET_ID", "").strip()
    gid = os.environ.get("CHARACTER_MASTER_GID", "").strip()
    if not spreadsheet_id or not gid:
        raise RuntimeError(
            "CHARACTER_MASTER_SPREADSHEET_ID and CHARACTER_MASTER_GID are required"
        )
    response = requests.get(
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export",
        params={"format": "csv", "gid": gid},
        timeout=30,
    )
    response.raise_for_status()
    aliases = {}
    for row in csv.DictReader(io.StringIO(response.text)):
        english = row.get("name_en", "").strip()
        if not english:
            continue
        try:
            names = json.loads(row.get("aliases", "[]"))
        except json.JSONDecodeError:
            names = []
        if names:
            aliases[english] = str(names[0])
    _character_aliases_cache = aliases
    return aliases


def initialize_character_aliases():
    """Fetch the character master once at process startup."""
    return _load_character_aliases()


def _translate_sheet_character_names(text):
    """Translate TL English tokens by partial-matching characters.name_en."""
    aliases = _load_character_aliases()
    text = re.sub(r"\bBoss\b", "ボス", text, flags=re.IGNORECASE)
    protected = []
    def protect(match):
        protected.append(match.group(0))
        return f"__TL_NOTE_{len(protected) - 1}__"
    text = re.sub(r"\([^()]*\)", protect, text)
    for token in sorted(set(re.findall(r"(?<![A-Za-z])[A-Za-z][A-Za-z-]{2,}(?![A-Za-z])", text)), key=len, reverse=True):
        token_lower = token.lower()
        matches = []
        for name in aliases:
            name_lower = name.lower()
            base_score = 1 if "(" not in name else 0
            if name_lower == token_lower:
                matches.append((len(name_lower), base_score, -len(name), name))
                continue
            # Accept a short, unambiguous prefix such as Peco -> Pecorine.
            if len(token_lower) >= 4 and name_lower.startswith(token_lower):
                matches.append((len(token_lower), base_score, -len(name), name))
                continue
            # Some sheets prefix a name fragment with uppercase initials, e.g.
            # PAoi, BNephi, GGMugi, and NYPeco. Try only fragments of at least
            # three characters after an all-uppercase prefix; never match
            # arbitrary words such as "frame".
            for split in range(1, len(token_lower) - 2):
                prefix = token[:split]
                fragment = token_lower[split:]
                if prefix.isupper() and len(fragment) >= 3 and fragment in name_lower:
                    matches.append((len(fragment), base_score, -len(name), name))
        if matches:
            name = max(matches)[3]
            text = re.sub(rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", aliases[name], text)
    for index, note in enumerate(protected):
        text = text.replace(f"__TL_NOTE_{index}__", note)
    return text


def _translate_known_character_tokens(text):
    """Translate only exact names and full-name initial prefixes in notes."""
    aliases = _load_character_aliases()
    exact = {name.lower(): alias for name, alias in aliases.items()}
    tokens = sorted(
        set(re.findall(r"(?<![A-Za-z])[A-Za-z][A-Za-z-]{2,}(?![A-Za-z])", text)),
        key=len,
        reverse=True,
    )
    for token in tokens:
        replacement = exact.get(token.lower())
        if replacement is None:
            for split in range(1, len(token) - 2):
                prefix = token[:split]
                fragment = token[split:]
                if prefix.isupper() and fragment.lower() in exact:
                    replacement = exact[fragment.lower()]
                    break
        if replacement is not None:
            text = re.sub(
                rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])",
                replacement,
                text,
            )
    return text


def format_tl_text(text):
    """Resolve sheet abbreviations and apply the optional TL formatter."""
    try:
        # Use fuzzy translation for action rows, but strict known-name-only
        # translation for untimed notes so prose such as ``Tell`` is preserved.
        text = "\n".join(
            _translate_sheet_character_names(line)
            if re.match(r"^\s*\d{1,3}:\d{2}\b", line)
            else _translate_known_character_tokens(line)
            for line in text.splitlines()
        )
    except (requests.RequestException, RuntimeError, KeyError, ValueError):
        pass
    # The published sheet writes formation masks as ``OOXOX, Auto ON``.
    # The formatter expects the mask as a standalone token.
    text = re.sub(r"([OX〇⭕️❌－\-]{5})\s*,", r"\1 ", text)
    # ``UB >`` is a source-sheet routing marker, not part of the published TL.
    text = re.sub(r"\bUB\s*>\s*", "", text, flags=re.IGNORECASE)
    try:
        # Reuse the configured formatter loader so the local checkout at
        # [discord_channel] tl_formatter_path is available in this process.
        # A direct ``from priconner_tl`` import only works when the formatter
        # project has already been installed into the active interpreter.
        from tl_formatting import FormatterSyncError, _load_formatter

        format_text = _load_formatter()
    except FormatterSyncError:
        raise
    except (ImportError, RuntimeError):
        return canonicalize_tl(text)
    # Discord TL output should not contain route arrows.
    # O/X形式のSET操作はTL formatter側で[54321]形式へ変換する。
    return format_text(text, preserve_set_operations=True).replace("->", " ").replace(">", " ").strip()


def formation_image_urls(html, code=None):
    """Return the first five character portrait URLs from one TL block."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows = table.find_all("tr")
    grid = {}
    code_position = None
    header_codes = []
    for r, row in enumerate(rows):
        c = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while (r, c) in grid:
                c += 1
            colspan = max(1, int(cell.get("colspan", 1)))
            rowspan = max(1, int(cell.get("rowspan", 1)))
            value = cell.get_text(" ", strip=True)
            images = [img.get("src") for img in cell.find_all("img") if img.get("src")]
            sibling_labels = [s.get_text(" ", strip=True) for s in cell.find_next_siblings("td", recursive=False)[:5]]
            if images and sibling_labels:
                for image in images:
                    _formation_source_labels[image] = sibling_labels
            for rr in range(r, r + rowspan):
                for cc in range(c, c + colspan):
                    grid[(rr, cc)] = (value if rr == r and cc == c else "", images if rr == r and cc == c else [])
            # simple/manual uses D503, while overtime uses D3T05.
            # Both formats identify a TL panel header and must be usable
            # when locating the panel's five formation portraits.
            if value and (_D_CODE.fullmatch(value) or _OT_CODE.fullmatch(value)):
                header_codes.append((r, c, value))
                if value == code and code_position is None:
                    code_position = (r, c)
            c += colspan
    # Image cells span two columns; the five original character names are
    # placed immediately to their right on the same row.
    for (rr, cc), (_, image_list) in list(grid.items()):
        if image_list:
            labels = [grid.get((rr, cc + 2 + i), ("", []))[0].strip()
                      for i in range(5)]
            for image in image_list:
                _formation_source_labels[image] = labels
    if code_position is None:
        return []
    start_row, start_col = code_position
    same_header = sorted((col, value) for row, col, value in header_codes if row == start_row)
    next_cols = [col for col, _ in same_header if col > start_col]
    end_col = next_cols[0] if next_cols else max(col for (row, col) in grid if row == start_row) + 1
    next_rows = [row for row, col, _ in header_codes if row > start_row and start_col <= col < end_col]
    end_row = min(next_rows) if next_rows else max(row for row, _ in grid) + 1
    candidates = []
    for row in range(start_row, end_row):
        imgs = []
        labels = []
        seen = set()
        for col in range(start_col, end_col):
            cell_images = grid.get((row, col), ("", []))[1]
            for src in cell_images:
                if src not in seen:
                    imgs.append(src)
                    # Character names are usually in the row immediately
                    # below the portrait; use the original sheet text.
                    source_labels = _formation_source_labels.get(src, [])
                    label = source_labels[len(labels)] if len(labels) < len(source_labels) else ""
                    labels.append(label)
                    seen.add(src)
        if len(imgs) >= 5:
            candidates.append((imgs, labels))
    if not candidates:
        return []
    # The source row may contain one boss portrait in addition to the five
    # character portraits.  Never select the boss as a formation slot when
    # the published HTML provides a label for it.
    best_images, best_labels = candidates[max(range(len(candidates)), key=lambda i: len(candidates[i][0]))]
    character_pairs = [
        (image, label) for image, label in zip(best_images, best_labels)
        if label and not re.search(r"(?:\bboss\b|ボス|敵)", label, re.IGNORECASE)
    ]
    if len(character_pairs) >= 5:
        best_images, best_labels = zip(*character_pairs)
        best_images, best_labels = list(best_images), list(best_labels)
    result = best_images[:5]
    best_labels = best_labels[:5]
    # Keep the source image alt text for 404 placeholders.  This is the
    # original sheet name, before character-name translation.
    # The grid mapping above is the authoritative source for the label of
    # each image.  Do not infer names from preceding rows: rowspan/colspan
    # layouts can make those rows belong to another formation or to the TL
    # metadata, causing the same name to be rendered for several portraits.
    _formation_labels_cache[tuple(result)] = best_labels
    return result


def combined_formation_image(urls, http_get=requests.get):
    """Download five portraits and return one horizontal PNG file object."""
    from PIL import Image, ImageDraw
    portraits = []
    labels = _formation_labels_cache.get(tuple(urls), [""] * len(urls))
    for index, url in enumerate(urls[:5]):
        candidates = [url]
        if "=" in url:
            base = url.rsplit("=", 1)[0]
            candidates.extend([base + "=s256", base + "=w256-h256", base])
        loaded = None
        last_error = None
        for candidate in candidates:
            try:
                response = http_get(candidate, timeout=10)
                response.raise_for_status()
                loaded = Image.open(io.BytesIO(response.content)).convert("RGB").resize((128, 128))
                break
            except Exception as error:
                last_error = error
        if loaded is None:
            # A stale Google image URL must not remove the whole formation.
            # Keep the slot so the five-character layout remains aligned.
            print(f"警告: 編成画像の代替URLも失敗、白画像で補完: {last_error}")
            loaded = Image.new("RGB", (128, 128), "white")
            label = labels[index] if index < len(labels) else ""
            if label:
                draw = ImageDraw.Draw(loaded)
                draw.text((4, 54), label, fill="black")
        portraits.append(loaded)
    if len(portraits) < 5:
        return None
    output = Image.new("RGB", (640, 128), "white")
    for index, portrait in enumerate(portraits):
        output.paste(portrait, (index * 128, 0))
    stream = io.BytesIO()
    output.save(stream, format="PNG")
    stream.seek(0)
    return stream


def formation_info_md(html, code, manual_layout=False):
    """Extract formation data inside the target TL panel only."""
    soup = BeautifulSoup(html, "html.parser")
    trs = soup.find_all("tr")
    grid = {}
    headers = []
    for r, row in enumerate(trs):
        col = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while (r, col) in grid:
                col += 1
            colspan = int(cell.get("colspan", 1) or 1)
            rowspan = int(cell.get("rowspan", 1) or 1)
            value = cell.get_text(" ", strip=True)
            for rr in range(r, r + rowspan):
                for cc in range(col, col + colspan):
                    grid[(rr, cc)] = value if rr == r and cc == col else ""
            if value and value == code:
                headers.append((r, col))
            col += colspan
    if not headers:
        return ""
    start_row, start_col = headers[0]
    same_row = sorted(c for r, c in headers if r == start_row)
    later = [c for c in same_row if c > start_col]
    end_col = later[0] if later else max(c for (r, c) in grid if r == start_row) + 1
    # Read only until the next header row for the same panel position.
    next_header = next((r for r in range(start_row + 1, len(trs))
                        if grid.get((r, start_col), "") and
                        re.fullmatch(r"D[1-5](?:T\d{2}|\d{1,2})", grid[(r, start_col)])), len(trs))
    names = []
    info = {"⚔️": [], "⭐": [], "UE": []}
    for r in range(start_row + 1, next_header):
        values = [grid.get((r, c), "").strip() for c in range(start_col, end_col)]
        time_index = next((i for i, v in enumerate(values) if re.fullmatch(r"\d{1,3}:\d{2}", v)), None)
        if not names and manual_layout:
            candidate = values[1:6] if len(values) >= 6 else []
        elif not names and time_index is not None and time_index >= 5:
            candidate = values[time_index - 5:time_index]
        else:
            candidate = []
        if (not names and len(candidate) == 5 and all(candidate)
                and not any(re.search(r"Author|Transcribed|Duration|EV/OT", v, re.IGNORECASE)
                            for v in candidate)):
            names = candidate
        for marker in info:
            if marker in values:
                i = values.index(marker)
                candidate = [v for v in values[i + 1:i + 6]]
                if len(candidate) == 5:
                    info[marker] = candidate
    if len(names) != 5:
        return ""
    # Sheets display the formation left-to-right; posts require right-to-left.
    # 編成情報のキャラ名は原文を保持する。翻訳名ではキャラの種別・衣装等の
    # 情報が欠けるため、アクション行の翻訳とは分離する。
    names = list(reversed(names))
    for marker, values in info.items():
        info[marker] = list(reversed(values)) if len(values) == 5 else [""] * 5
    lines = ["編成情報", "", "キャラ名       ⚔️     ⭐     UE"]
    lines.extend(f"{name:<14} {info['⚔️'][i]:<5} {info['⭐'][i]:<5} {info['UE'][i]}"
                 for i, name in enumerate(names))
    return "\n".join(lines)


def load_worrychefs_sources(path=None):
    path = Path(path or Path(__file__).with_name("worrychefs_sources.json"))
    with path.open(encoding="utf-8") as stream:
        config = json.load(stream)
    spreadsheet_id = config["spreadsheet_id"]
    return [dict(source, spreadsheet_id=spreadsheet_id) for source in config["sources"]]


def collect_worrychefs_records(sources, http_get=requests.get, timeout=10,
                               retries=2, retry_sleep=time.sleep):
    """Fetch configured public sheets and return only action-bearing TL blocks."""
    records = []
    for source in sources:
        gid = source["gid"]
        url = (f"https://docs.google.com/spreadsheets/d/e/{source['spreadsheet_id']}"
               f"/pubhtml/sheet?headers=false&gid={gid}")
        try:
            rows = fetch_public_sheet(
                source["spreadsheet_id"], gid,
                http_get=http_get,
                timeout=timeout,
            )
        except (requests.RequestException, RuntimeError):
            rows = []
        html = fetch_html(url, http_get, timeout, retries, retry_sleep)
        if not rows:
            continue
        for block in extract_tl_blocks(rows, source["name"]):
            if not validate_tl_record(block):
                continue
            block["text"] = format_tl_text(block["text"])
            block["canonical"] = canonicalize_tl(block["text"])
            block["hash"] = tl_hash(block["text"])
            block["image_urls"] = formation_image_urls(html, block["code"]) if html else []
            block["formation_md"] = formation_info_md(
                html, block["code"], manual_layout=source["name"].startswith("manual")
            ) if html else ""
            block["key"] = f"{source['name']}:{block['code']}"
            block["url"] = url
            block["author"] = _source_author(rows, block["code"])
            block["comparison_text"] = post_tracker.comparison_text(
                block["text"], block.get("formation_md"), block.get("author"), block.get("damage")
            )
            block["hash"] = tl_hash(block["comparison_text"])
            records.append(block)
    return records


def _source_author(rows, code):
    """Read the author displayed beside a TL code in the source sheet."""
    for row in rows:
        for index, cell in enumerate(row):
            if cell.strip() != code:
                continue
            for candidate in row[index + 1:index + 4]:
                match = _AUTHOR.fullmatch(candidate.strip())
                if match:
                    return match.group(1).strip()
    return ""


def validate_tl_record(record):
    """Return false for records containing another boss code (fail closed)."""
    code = record.get("code", "")
    if not code or not record.get("text"):
        return False
    # Parenthesized notes may legitimately reference another TL (for example
    # ``see D3T04``). Only action text should trigger the cross-boss guard.
    action_text = re.sub(r"\([^()]*\)", "", record["text"])
    foreign = set(_ANY_CODE.findall(action_text)) - {code}
    if foreign:
        print(f"警告: {code} に別ボスコードが混入したため投稿を抑止: {sorted(foreign)}")
        return False
    return True


def _render_post_text(record, post_text):
    """Render update posts as separate git-style diff and current-body blocks."""
    if record.get("status") != "updated":
        return f"```scm\n{post_text}\n```"
    if "【現行本文】" not in post_text:
        return f"```scm\n{post_text}\n```"
    diff_text, current_text = post_text.split("【現行本文】", 1)
    diff_text = diff_text.strip()
    current_text = current_text.strip()
    if diff_text.startswith("【差分】"):
        diff_text = diff_text[len("【差分】"):].strip()
        diff_block = f"【差分】\n```diff\n{diff_text}\n```"
    else:
        diff_block = "【差分なし】"
    return f"{diff_block}\n\n【現行本文】\n```scm\n{current_text}\n```"


def _split_text_lines(text, limit):
    """Split text on lines and hard-wrap only an overlong individual line."""
    chunks = []
    current = ""
    for line in str(text or "").splitlines() or [""]:
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else f"{current}\n{line}"
        if current and len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current or not chunks:
        chunks.append(current)
    return chunks


def build_worrychefs_split_messages(record, post_text, detected_at, limit=1950):
    """Build attachment-free messages for TL bodies exceeding Discord's limit."""
    header = (
        f"[WorryChefs更新] {record['code']} ({record['source']})\n"
        f"更新検知日時: {detected_at if record.get('status') == 'updated' else ''}\n"
        f"参照: {record['url']}\n"
        "TL本文（分割）\n"
    )
    payload_limit = max(100, limit - len(header) - 32)
    chunks = _split_text_lines(post_text, payload_limit)
    total = len(chunks)
    return [
        f"{header}分割 {index}/{total}\n```text\n{chunk}\n```"
        for index, chunk in enumerate(chunks, start=1)
    ]


def build_formation_attachment(record):
    """Build one PNG attachment from the record's five formation portraits."""
    urls = list(dict.fromkeys(record.get("image_urls") or []))[:5]
    if len(urls) < 5:
        return ""
    image = combined_formation_image(urls)
    if image is None:
        return ""
    return ("formation.png", image.getvalue(), "image/png")


def build_worrychefs_content(record, post_text, detected_at, limit=1950):
    """Build one Discord message per TL without crossing Discord's limit."""
    code = record["code"]
    source = record["source"]
    url = record["url"]
    author = record.get("author") or "不明"
    damage = record.get("damage") or "未記入"
    rendered_post = _render_post_text(record, post_text)
    full = (
        f"[WorryChefs更新] {code} ({source})\n"
        f"新規投稿日時: {record.get('first_seen', detected_at)}\n"
        f"更新検知日時: {detected_at if record.get('status') == 'updated' else ''}\n"
        f"参照スプシ: [シートを開く]({url})\n"
        f"制作者: {author}\n"
        f"ダメージ: {damage}\n\n"
        f"{rendered_post}"
    )
    formation = f"\n\n```text\n{record['formation_md']}\n```" if record.get("formation_md") else ""
    candidates = [
        full + formation,
        (
            f"[WorryChefs更新] {code} ({source})\n"
            f"参照スプシ: [シートを開く]({url})\n"
            f"制作者: {author} / ダメージ: {damage}\n\n"
            f"{rendered_post}" + formation
        ),
        (
            f"[WorryChefs更新] {code} ({source})\n"
            f"参照: {url}\n"
            f"{rendered_post}"
        ),
        (
            f"[WorryChefs更新] {code} ({source})\n"
            f"{rendered_post}" + formation
        ),
        (
            f"[WorryChefs更新] {code}\n"
            f"{rendered_post}" + formation
        ),
    ]
    return next((candidate for candidate in candidates if len(candidate) <= limit), None)


def compare_tl_records(rows, records):
    """Return new/changed records without treating unchanged rows as updates."""
    existing = {}
    for row in rows[1:]:
        if len(row) >= 1 and row[0]:
            existing[row[0]] = row
    result = []
    for record in records:
        old = existing.get(record["key"])
        if old is None:
            record["status"] = "new"
        elif len(old) < 3 or not old[2] or old[2] != record["hash"]:
            # Older rows may have been hashed before English character names
            # were translated.  Compare normalized visible text as a migration
            # fallback so a name translation alone is not a full update.
            old_text = old[1] if len(old) > 1 else ""
            try:
                old_normalized = canonicalize_tl(format_tl_text(old_text))
            except (requests.RequestException, RuntimeError, KeyError, ValueError):
                old_normalized = canonicalize_tl(old_text)
            current_normalized = canonicalize_tl(record.get("text", ""))
            if old_normalized == current_normalized:
                continue
            record["status"] = "updated"
            record["previous_text"] = old[1] if len(old) > 1 else ""
        else:
            continue
        if old and len(old) > 3:
            record["first_seen"] = old[3]
        result.append(record)
    return result


def record_to_row(record, detected_at, existing_row=None):
    """Build the WorryChefs TL sheet row, preserving the original detect time."""
    first_seen = existing_row[3] if existing_row and len(existing_row) > 3 and existing_row[3] else detected_at
    updated = detected_at if existing_row else ""
    previous = record.get("previous_text", "") if existing_row else ""
    return [record["key"], record["text"], record["hash"], first_seen, updated,
            record["source"], record["url"], previous]


def prepare_sheet_changes(sheet_rows, records, detected_at):
    """Return header, inserts, and cell updates for the WorryChefs TL sheet."""
    rows = list(sheet_rows or [])
    existing = {row[0]: (index, row) for index, row in enumerate(rows[1:], start=2)
                if row and row[0]}
    changed = compare_tl_records(rows, records)
    inserts = []
    updates = []
    for record in changed:
        old = existing.get(record["key"])
        new_row = record_to_row(record, detected_at, old[1] if old else None)
        if old:
            updates.append((old[0], new_row))
        else:
            inserts.append(new_row)
    header = list(rows[0]) if rows and len(rows[0]) >= 7 else list(TL_HEADERS)
    if "投稿直前本文" not in header:
        header.append("投稿直前本文")
    for _, row in updates:
        while len(row) < len(header):
            row.append("")
    for row in inserts:
        while len(row) < len(header):
            row.append("")
    return header, inserts, updates


def record_matches_month(record, sheet_rows, target_month):
    """Return whether a record belongs to the requested trial snapshot.

    The published WorryChefs tabs contain TL layouts but no reliable source
    publication date.  ``target_month`` is therefore a trial/snapshot label,
    not a filter against the destination sheet's detection timestamp.  A TL
    first collected in September can still be part of the August snapshot.
    Keep the sheet argument for API compatibility with callers and tests.
    """
    return True


def save_record_changes(sheet, header, inserts, updates):
    """Persist prepared changes using gspread's range update primitives."""
    if not sheet.get_all_values() or sheet.get_all_values()[0] != header:
        end_col = chr(ord("A") + len(header) - 1)
        sheet.update(f"A1:{end_col}1", [header], value_input_option="USER_ENTERED")
    for row_number, row in updates:
        end_col = chr(ord("A") + len(header) - 1)
        sheet.update(f"A{row_number}:{end_col}{row_number}", [row], value_input_option="USER_ENTERED")
    if inserts:
        sheet.insert_rows(inserts, row=2, value_input_option="USER_ENTERED")


def apply_alternating_post_modes(records, sheet_rows):
    """Mark selected records alternately as full new posts and diff updates."""
    existing = {
        row[0]: row for row in (sheet_rows or [])[1:] if row and row[0]
    }
    counts = {"new": 0, "updated": 0}
    for index, record in enumerate(records):
        old = existing.get(record.get("key"))
        if index % 2 == 0 or not old or len(old) <= 1:
            record["status"] = "new"
            record["force_full"] = True
            record.pop("previous_text", None)
            counts["new"] += 1
            continue
        record["status"] = "updated"
        record["force_full"] = False
        record["previous_text"] = old[1]
        counts["updated"] += 1
    return counts


def format_previous_post_text(record):
    """Normalize the stored old body before rendering an update diff."""
    if record.get("status") == "updated" and record.get("previous_text"):
        record["previous_text"] = format_tl_text(record["previous_text"])
    return record


def scan_configured_worrychefs(spreadsheet=None, now_factory=datetime.now,
                               format_time=datetime.dateTime2String,
                               reset_discord=False, target_code=None, source_kind=None,
                               force_post=False, target_boss=None, target_month=None,
                               alternate_new_update=False):
    """Collect configured WorryChefs sheets, persist diffs, and route Bot posts."""
    test_only = bool(
        reset_discord or force_post or alternate_new_update
        or target_code or source_kind or target_boss or target_month
    )
    post_guild_keys = discord.configured_guild_keys(test_only=test_only)
    ss = spreadsheet or gspread.getNewArrivalsSheet()
    sheet = ss.worksheet("WorryChefs TL")
    sheet_rows = sheet.get_all_values()
    records = collect_worrychefs_records(load_worrychefs_sources())
    if target_month:
        month_records = [record for record in records
                         if record_matches_month(record, sheet_rows, target_month)]
        print(f"WorryChefs対象月 {target_month}: {len(month_records)}件")
        if not month_records:
            print(f"停止: {target_month}の保存済みWorryChefs TLがないため、既存投稿は削除しません")
            return records
    if reset_discord:
        channel_keys = [f"boss{target_boss}_tl"] if target_boss else None
        deleted = 0
        for guild_key in post_guild_keys:
            deleted += discord.delete_worrychefs_posts(
                guild_key=guild_key, channel_keys=channel_keys
            )
        print(f"WorryChefs既存投稿を削除: {deleted}件")
    detected_at = format_time(now_factory())
    header, inserts, updates = prepare_sheet_changes(sheet_rows, records, detected_at)
    if inserts or updates or (sheet_rows and sheet_rows[0] != header):
        save_record_changes(sheet, header, inserts, updates)
    # 通常時はシート上のハッシュで重複投稿を防止する。
    # リセット時または明示的な強制投稿時は、選択フィルタ内の全件を投稿する。
    if reset_discord or force_post:
        changed_keys = {record["key"] for record in records
                        if (source_kind is None or record.get("source") == source_kind
                            or (source_kind == "manual" and str(record.get("source", "")).startswith("manual-")))
                        and (target_code is None or str(record.get("code", "")).strip().upper() == target_code)
                        and record_matches_month(record, sheet_rows, target_month)
                        and (target_boss is None or str(record.get("code", "")).strip().upper().startswith(f"D{target_boss}"))}
    else:
        changed_keys = {
            record["key"] for record in records
            if (source_kind is None or record.get("source") == source_kind
                or (source_kind == "manual" and str(record.get("source", "")).startswith("manual-")))
            and (target_code is None or str(record.get("code", "")).strip().upper() == target_code)
            and record_matches_month(record, sheet_rows, target_month)
            and (target_boss is None or str(record.get("code", "")).strip().upper().startswith(f"D{target_boss}"))
            and record.get("status") in {"new", "updated"}
        }
    selected_records = [record for record in records if record["key"] in changed_keys]
    if alternate_new_update:
        counts = apply_alternating_post_modes(selected_records, sheet_rows)
        print(f"交互投稿計画: 新規{counts['new']}件 更新{counts['updated']}件")
    for record in selected_records:
        format_previous_post_text(record)
    for record in records:
        if os.environ.get("PRICONNER_NO_POST"):
            continue
        if record["key"] not in changed_keys:
            continue
        if (reset_discord or force_post) and not alternate_new_update:
            record["force_full"] = True
        post_text = post_tracker.post_content(record)
        content = build_worrychefs_content(record, post_text, detected_at)
        formation_attachment = None
        if record.get("image_urls"):
            try:
                formation_attachment = build_formation_attachment(record) or None
            except Exception as error:
                print(f"警告: 編成画像の生成に失敗 {record['key']}: {error}")
        if content is None:
            messages = build_worrychefs_split_messages(record, post_text, detected_at)
        else:
            messages = [content]
        try:
            if target_boss is not None:
                record_boss = str(record.get("code", "")).strip().upper()[1:2]
                if record_boss != str(target_boss):
                    print(f"警告: 試験担当外の投稿を抑止: {record['key']}")
                    continue
            for index, message in enumerate(messages):
                files = {"file": formation_attachment} if formation_attachment and index == 0 else None
                discord.post_for_boss_to_configured_guilds(
                    record["code"],
                    message,
                    files=files,
                    guild_keys=post_guild_keys,
                )
        except Exception as error:
            print(f"失敗: WorryChefs Discord通知 {record['key']}: {error}")
    return records


def _format_manual_row(row, inherited_time=""):
    """Convert a Manual sheet row to formatter input."""
    if len(row) < 3 or (not _TIME.fullmatch(row[1]) and not inherited_time):
        return ""
    time_value = row[1] if _TIME.fullmatch(row[1]) else inherited_time
    if time_value.isdigit():
        time_value = f"0:{int(time_value):02d}"
    unit = row[2].strip()
    action = row[3].strip() if len(row) > 3 else ""
    # Columns 2..6 are SET/OFF state in the five formation positions.
    if unit == "SET" and len(row) >= 7:
        state = "".join("O" if value.strip().upper() == "SET" else "X"
                        for value in row[2:7])
        auto = row[7:9]
        suffix = "🅰️OFF" if auto == ["Auto", "OFF"] else ""
        return f"{time_value} {state} {suffix}".strip()
    states = [match.group(0) for value in row[4:]
              for match in re.finditer(r"[OX〇○◯×☓]{5}", value.strip(), re.IGNORECASE)]
    state_text = " / ".join(
        "".join("○" if value.upper() in {"O", "〇", "○", "◯"} else "×" for value in state)
        for state in states
    )
    if not unit and not action and not state_text:
        return ""
    return " ".join(part for part in (time_value, unit, action, state_text) if part)


def extract_tl_blocks(rows, source_kind="simple"):
    """Extract D-code blocks, retaining all action lines in each block."""
    if source_kind in {"overtime", "simple"}:
        return _extract_overtime_blocks(rows, source_kind,
                                        _OT_CODE if source_kind == "overtime" else _D_CODE)
    starts = [(i, row[j]) for i, row in enumerate(rows) for j in range(len(row))
              if _D_CODE.fullmatch(row[j] if j < len(row) else "")]
    blocks = []
    for index, (start, code) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(rows)
        block_rows = rows[start:end]
        if source_kind.startswith("manual"):
            lines = []
            inherited_time = ""
            for row in block_rows:
                if len(row) > 1 and _TIME.fullmatch(row[1]):
                    inherited_time = row[1]
                line = _format_manual_row(row, inherited_time)
                if line:
                    lines.append(line)
            damage = next((match.group(0) for row in block_rows for cell in row
                           for match in [_DAMAGE.search(cell)] if match), "")
        else:
            lines = []
            damage = ""
            pair_counts = {}
            for row in block_rows:
                for pos, cell in enumerate(row[:-1]):
                    action = row[pos + 1].strip()
                    if (re.fullmatch(r"\d{1,2}:\d{2}", cell)
                            and action and not _TIME.fullmatch(action)):
                        pair_counts[pos] = pair_counts.get(pos, 0) + 1
                for cell in row:
                    match = _DAMAGE.search(cell)
                    if match and not damage:
                        damage = match.group(0)
            if pair_counts:
                time_col = max(pair_counts, key=pair_counts.get)
                for row in block_rows:
                    if time_col + 1 < len(row):
                        cell, action = row[time_col], row[time_col + 1].strip()
                        if re.fullmatch(r"\d{1,2}:\d{2}", cell) and action:
                            lines.append(f"{cell} {action}")
        text = "\n".join(lines)
        # Manual sheets contain empty boss panels with only an initial SET
        # row.  They are not TLs and must not be posted.
        if source_kind.startswith("manual") and lines and not any(
                not re.fullmatch(r"\s*\d{1,3}:\d{2}\s+[OX]{5}(?:\s+🅰️(?:ON|OFF))?\s*", line)
                for line in lines):
            continue
        if lines:
            blocks.append({"code": code, "source": source_kind, "damage": damage,
                           "text": text, "canonical": canonicalize_tl(text),
                           "hash": tl_hash(text)})
    return blocks


def _extract_overtime_blocks(rows, source_kind, code_pattern=_OT_CODE):
    """Extract horizontally arranged D1Txx..D5Txx overtime blocks."""
    starts = []
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if code_pattern.fullmatch(cell):
                starts.append((i, j, cell))
    records = []
    for n, (start, col, code) in enumerate(starts):
        same_row = [item for item in starts if item[0] == start]
        # Assign columns to the nearest boss anchor; never assume a fixed
        # seven-column layout (especially for the rightmost boss).
        anchors = sorted((item[1], item[2]) for item in same_row)
        # Assign each column to the nearest boss anchor, using the midpoint
        # between adjacent anchors as the boundary.  A fixed-width radius
        # leaks D403 actions into D503 on the published sheet because the
        # rightmost block is wider than the others.
        anchor_index = next(index for index, item in enumerate(anchors) if item[0] == col)
        # The code cell is the left edge of its merged block.  Starting at
        # the midpoint would include the preceding boss's action column.
        left = 0 if anchor_index == 0 else col
        # Each published boss panel occupies the columns up to the next
        # header anchor.  Midpoint boundaries truncate the time/action
        # columns for D1-D4, while D5 happens to work because it is last.
        right = anchors[anchor_index + 1][0] if anchor_index + 1 < len(anchors) else max(len(row) for row in rows[start:])
        owned = list(range(left, right))
        if not owned:
            owned = [col]
        left, right = min(owned), max(owned) + 1
        # A header row contains all five boss anchors, so the next item in
        # ``starts`` is usually another boss on the *same* row.  End at the
        # next later header for this boss instead of truncating at that item.
        boss = code[1]
        end = next(
            (item[0] for item in starts[n + 1:]
             if item[0] > start and item[2][1] == boss),
            len(rows),
        )
        lines = []
        damage = ""
        time_column = next(
            (index for index in range(left, right)
             if rows[start][index].strip().lower() == "time"),
            left,
        )
        for row in rows[start:end]:
            cells = row[left:right]
            for value in cells:
                match = _DAMAGE.search(value)
                if match and not damage:
                    damage = match.group(0)
            row_had_time = False
            for pos, value in enumerate(cells):
                if left + pos < time_column:
                    continue
                value = value.strip()
                # Overtime tables contain auxiliary numeric counters and
                # repeated timestamps in the same horizontal block. Keep
                # only actual action descriptions after a time cell. Do not
                # depend on adjacency: merged cells can place the action
                # several columns to the right (notably D503).
                time_pattern = (r"\d{1,2}:\d{2}|\d{1,3}" if source_kind in {"overtime", "simple"}
                                else r"\d{1,2}:\d{2}")
                if not re.fullmatch(time_pattern, value):
                    continue
                row_had_time = True
                actions = []
                for candidate in cells[pos + 1:]:
                    candidate = candidate.strip()
                    if re.fullmatch(time_pattern, candidate):
                        break
                    if (candidate and not code_pattern.fullmatch(candidate)
                            and not re.fullmatch(r"\d{1,3}", candidate)):
                        actions.append(candidate)
                action = " ".join(actions).strip()
                if action:
                    # Simple TLの編成欄が結合セル展開後に時刻列へ
                    # 流れ込むことがある。操作記述を含まない複数名の
                    # 行は編成情報なので、TL本文から除外する。
                    if (len(actions) >= 2 and not re.search(
                            r"\b(?:UB|SET|UNSET|AUTO|OFF|ON|LAST|OOO|XXX|OX|XOX|->|>)\b",
                            action, re.IGNORECASE)):
                        continue
                    t = value if ":" in value else f"0:{int(value):02d}"
                    lines.append(f"{t} {action}")
            # Keep untimed TL continuation/comment rows.  Parenthesized notes
            # such as "(tell ...)" are part of the instructions, not
            # formation metadata, and must not be discarded.
            if not row_had_time:
                continuation = next(
                    (value.strip() for value in cells[time_column:]
                     if value.strip().startswith(("(", "（", "Tell:", "tell:"))),
                    "",
                )
                if continuation:
                    lines.append(continuation)
        if lines:
            lines = list(dict.fromkeys(lines))
            text = "\n".join(lines)
            records.append({"code": code, "source": source_kind, "damage": damage,
                            "text": text, "canonical": canonicalize_tl(text),
                            "hash": tl_hash(text)})
    return records


def normalize_damage(value):
    """Convert a damage value to the sheet's integer representation."""
    value = value.rsplit("-", 1)[-1]
    cleaned = value.replace("m", "").replace("+", "")
    try:
        return int(Decimal(cleaned) * 100)
    except (InvalidOperation, TypeError, ValueError):
        return cleaned


def translate_style(style):
    return {
        "Semi-Auto": "セミオート",
        "Manual": "手動",
        "Simple Manual": "簡易手動",
        "Simple": "簡易",
        "Auto": "オート",
    }.get(style, style)


def extract_damage_entries(rows):
    """Extract consecutive code, damage, and style triples from table rows."""
    extracted = []
    for row in rows:
        for index in range(len(row) - 2):
            code, damage, style = row[index : index + 3]
            if re.match(r"^D([1-5][0-9]{1,2}|10)$", code) and all(
                value != "" for value in (code, damage, style)
            ):
                extracted.append(
                    [code, normalize_damage(damage), translate_style(style)]
                )
    return extracted


def nonempty_rows(rows):
    return [row for row in rows if any(cell != "" for cell in row)]


def fetch_html(
    url,
    http_get=requests.get,
    timeout=10,
    retries=2,
    retry_sleep=time.sleep,
):
    for attempt in range(retries + 1):
        try:
            response = http_get(url, timeout=timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.RequestException:
            if attempt == retries:
                return None
            retry_sleep(2**attempt)


def parse_table_rows(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return None
    occupied = {}
    for r, row in enumerate(table.find_all("tr")):
        c = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while (r, c) in occupied:
                c += 1
            value = cell.get_text(" ", strip=True)
            rowspan = max(1, int(cell.get("rowspan", 1)))
            colspan = max(1, int(cell.get("colspan", 1)))
            for rr in range(r, r + rowspan):
                for cc in range(c, c + colspan):
                    occupied[(rr, cc)] = value if (rr, cc) == (r, c) else ""
            c += colspan
    if not occupied:
        return []
    width = max(c for _, c in occupied) + 1
    return nonempty_rows([[occupied.get((r, c), "") for c in range(width)]
                          for r in range(max(r for r, _ in occupied) + 1)])


def parse_csv_rows(text):
    """Parse published CSV into a rectangular logical grid."""
    rows = list(csv.reader(io.StringIO(text)))
    width = max((len(row) for row in rows), default=0)
    return nonempty_rows([row + [""] * (width - len(row)) for row in rows])


def build_tl_values(rows, existing_tls, scan_time):
    existing_tls = set(existing_tls)
    values = []
    for code, damage, style in extract_damage_entries(rows):
        tl = f"{code},{damage},{style}"
        if tl in existing_tls:
            continue
        values.append([tl, scan_time])
        existing_tls.add(tl)
    return values


def save_tl_values(sheet, values):
    sheet.insert_rows(values, row=2)
    sheet.sort((2, "des"), range="A2:Z10000")
    gspread.deleteEmptyRows(sheet)


def notify_tl_values(notify, link_url, values):
    if not values:
        return
    tl_values = [row for row in values if row]
    if not tl_values:
        return
    text = "\n".join(row[0] for row in tl_values)
    content = f"[WorryChefs]({link_url})\n```cs\n{text}```"
    if notify is discord.notify:
        discord.notify_to_configured_guilds(content)
    else:
        notify(content)


def checkNewArrivalsForWorryChefs(
    spreadsheet=None,
    http_get=requests.get,
    notify=discord.notify,
    now_factory=datetime.now,
    format_time=datetime.dateTime2String,
    http_timeout=10,
    http_retries=2,
    retry_sleep=time.sleep,
):
    print("新着チェック対象:WorryChefs")

    ss = spreadsheet or gspread.getNewArrivalsSheet()
    sheet_tl = ss.worksheet("WorryChefs TL")
    existing_tls = sheet_tl.col_values(1)
    sheet_channel = ss.worksheet("WorryChefs")
    source_rows = sheet_channel.get_all_values()
    tl_values = []
    last_link_url = ""
    valid_source_seen = False

    for row in source_rows[1:]:
        gsheet_id, gid = row[:2]
        if not gsheet_id or not gid:
            continue

        url = f"https://docs.google.com/spreadsheets/d/e/{gsheet_id}/pubhtml/sheet?headers=false&gid={gid}"
        last_link_url = f"https://docs.google.com/spreadsheets/d/e/{gsheet_id}/pubhtml"
        print(url)
        html = fetch_html(
            url,
            http_get,
            timeout=http_timeout,
            retries=http_retries,
            retry_sleep=retry_sleep,
        )
        if html is None:
            continue
        rows = parse_table_rows(html)
        if rows is None:
            continue
        valid_source_seen = True
        scan_time = format_time(now_factory())
        new_values = build_tl_values(rows, existing_tls, scan_time)
        existing_tls.extend(row[0] for row in new_values)
        tl_values.extend(new_values)

    if not valid_source_seen:
        return

    if tl_values:
        save_tl_values(sheet_tl, tl_values)
        for tl_value, scan_time in tl_values:
            code, damage, style = tl_value.split(",", 2)
            write_arrival(
                "worrychefs",
                f"動画用TL情報 {code}",
                last_link_url,
                notes="WorryChefsスプレッドシートから抽出した動画用TL情報",
                notes_history=[f"{scan_time}: WorryChefsから新規抽出"],
                details={
                    "動画用TL情報": tl_value,
                    "ボスコード": code,
                    "ダメージ": damage,
                    "操作方式": style,
                    "元スプレッドシート": last_link_url,
                },
            )
        try:
            notify_tl_values(notify, last_link_url, tl_values)
        except Exception as error:
            # The TL rows are already durable; do not roll back the sheet or
            # make the whole scheduled stage fail after a notification error.
            print(f"失敗: WorryChefs通知: {error}")


def main():
    def run():
        initialize_character_aliases()
        print("-----------------------------------------------")
        print(f"開始{datetime.nowString()}")
        print("-----------------------------------------------")

        import sys
        reset = "--reset-posts" in sys.argv
        force = "--force" in sys.argv
        alternate = "--alternate-new-update" in sys.argv
        target = None
        source = None
        boss = None
        month = None
        if "--code" in sys.argv:
            target = sys.argv[sys.argv.index("--code") + 1].strip().upper()
        if "--source" in sys.argv:
            source = sys.argv[sys.argv.index("--source") + 1].strip().lower()
            if "--code" not in sys.argv:
                target = None
        if "--boss" in sys.argv:
            boss = sys.argv[sys.argv.index("--boss") + 1].strip()
            if boss not in {"1", "2", "3", "4", "5"}:
                raise ValueError("--boss must be one of 1, 2, 3, 4, or 5")
            target = None
        if "--month" in sys.argv:
            month = sys.argv[sys.argv.index("--month") + 1].strip().replace("-", "/")
            if not re.fullmatch(r"\d{4}/\d{2}", month):
                raise ValueError("--month must be YYYY/MM")
        scan_configured_worrychefs(reset_discord=reset, target_code=target,
                                   source_kind=source, force_post=force,
                                   target_boss=boss, target_month=month,
                                   alternate_new_update=alternate)

        print("-----------------------------------------------")
        print(f"終了{datetime.nowString()}")
        print("-----------------------------------------------")

    return run_locked(run, lock_name="worrychefs.lock")


if __name__ == "__main__":
    main()
