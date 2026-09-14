import re
import time
import hashlib
import io
import csv
import json
from pathlib import Path
from decimal import Decimal, InvalidOperation

import requests
from bs4 import BeautifulSoup

import datetime_utils as datetime
import discord_utils as discord
import gspread_utils as gspread
from public_sheet_export import fetch_public_sheet
from new_arrivals_markdown import write_arrival
from runtime_utils import run_locked

_D_CODE = re.compile(r"^D([1-5])\d{1,2}$")
_OT_CODE = re.compile(r"^D([1-5])T\d{2}$")
_TIME = re.compile(r"^(?:\d{1,2}:\d{2}|\d{1,3})$")
_DAMAGE = re.compile(r"\d+(?:\.\d+)?m\+?", re.IGNORECASE)
_AUTHOR = re.compile(r"(?:Original\s+)?Author:\s*(.*)", re.IGNORECASE)
_ANY_CODE = re.compile(r"\bD(?:[1-5]T\d{2}|[1-5]\d{1,2})\b")


def _is_target_code(code):
    """Return whether a code is the only code currently posted to Discord."""
    return str(code or "").strip().upper() == "D503"


def canonicalize_tl(text):
    """Normalize TL text for stable comparison and hashing."""
    text = text.replace("->", " ")
    return "\n".join(" ".join(text.split()).strip() for text in text.splitlines() if text.strip())


def tl_hash(text):
    return hashlib.sha256(canonicalize_tl(text).encode("utf-8")).hexdigest()


TL_HEADERS = [
    "TLキー", "TL本文", "TLハッシュ", "新規検出日時", "更新検出日時",
    "種別", "元シートURL",
]

_CHARACTER_MASTER_URL = "https://docs.google.com/spreadsheets/d/REMOVED_SPREADSHEET_ID/export"
_CHARACTER_MASTER_GID = "REMOVED_GID"
_character_aliases_cache = None


def _load_character_aliases():
    """Load English-name to first-alias mappings from the characters sheet."""
    global _character_aliases_cache
    if _character_aliases_cache is not None:
        return _character_aliases_cache
    response = requests.get(
        _CHARACTER_MASTER_URL,
        params={"format": "csv", "gid": _CHARACTER_MASTER_GID},
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
    for token in sorted(set(re.findall(r"(?<![A-Za-z])[A-Za-z][A-Za-z-]{2,}(?![A-Za-z])", text)), key=len, reverse=True):
        token_lower = token.lower()
        matches = []
        for name in aliases:
            name_lower = name.lower()
            # WorryChefs prefixes short names, e.g. NYPeco/BNephi/GGMugi.
            # Match the longest meaningful contiguous fragment of name_en.
            if name_lower in token_lower or token_lower in name_lower:
                matches.append((len(name_lower), name))
                continue
            fragments = [name_lower[index:index + size]
                         for size in range(4, len(name_lower) + 1)
                         for index in range(len(name_lower) - size + 1)]
            if any(fragment in token_lower for fragment in fragments):
                matches.append((max(len(fragment) for fragment in fragments if fragment in token_lower), name))
        if matches:
            name = max(matches)[1]
            text = re.sub(rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", aliases[name], text)
    return text


def format_tl_text(text):
    """Resolve sheet abbreviations and apply the optional TL formatter."""
    try:
        text = _translate_sheet_character_names(text)
    except (requests.RequestException, RuntimeError, KeyError, ValueError):
        pass
    # The published sheet writes formation masks as ``OOXOX, Auto ON``.
    # The formatter expects the mask as a standalone token.
    text = re.sub(r"([OX〇⭕️❌－\-]{5})\s*,", r"\1 ", text)
    try:
        from priconner_tl import format_text
    except ImportError:
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
            for rr in range(r, r + rowspan):
                for cc in range(c, c + colspan):
                    grid[(rr, cc)] = (value if rr == r and cc == c else "", images if rr == r and cc == c else [])
            if value and re.fullmatch(r"D[1-5]\d{1,2}", value):
                header_codes.append((r, c, value))
                if value == code and code_position is None:
                    code_position = (r, c)
            c += colspan
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
        seen = set()
        for col in range(start_col, end_col):
            for src in grid.get((row, col), ("", []))[1]:
                if src not in seen:
                    imgs.append(src)
                    seen.add(src)
        if len(imgs) >= 5:
            candidates.append(imgs)
    if not candidates:
        return []
    # The source row may contain one boss portrait followed by five characters.
    return candidates[max(range(len(candidates)), key=lambda i: len(candidates[i]))][-5:]


def combined_formation_image(urls, http_get=requests.get):
    """Download five portraits and return one horizontal PNG file object."""
    from PIL import Image
    portraits = []
    for url in urls[:5]:
        response = http_get(url, timeout=10)
        response.raise_for_status()
        portraits.append(Image.open(io.BytesIO(response.content)).convert("RGB").resize((128, 128)))
    if len(portraits) < 5:
        return None
    output = Image.new("RGB", (640, 128), "white")
    for index, portrait in enumerate(portraits):
        output.paste(portrait, (index * 128, 0))
    stream = io.BytesIO()
    output.save(stream, format="PNG")
    stream.seek(0)
    return stream


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
            block["key"] = f"{source['name']}:{block['code']}"
            block["url"] = url
            block["author"] = _source_author(rows, block["code"])
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
    foreign = set(_ANY_CODE.findall(record["text"])) - {code}
    if foreign:
        print(f"警告: {code} に別ボスコードが混入したため投稿を抑止: {sorted(foreign)}")
        return False
    return True


def compare_tl_records(rows, records):
    """Return new/changed records without treating unchanged rows as updates."""
    existing = {}
    for row in rows[1:]:
        if len(row) >= 3 and row[0] and row[2]:
            existing[row[0]] = row
    result = []
    for record in records:
        old = existing.get(record["key"])
        if old is None:
            record["status"] = "new"
        elif old[2] != record["hash"]:
            record["status"] = "updated"
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
    return [record["key"], record["text"], record["hash"], first_seen, updated,
            record["source"], record["url"]]


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
    header = rows[0] if rows and len(rows[0]) >= len(TL_HEADERS) else TL_HEADERS
    return header, inserts, updates


def save_record_changes(sheet, header, inserts, updates):
    """Persist prepared changes using gspread's range update primitives."""
    if not sheet.get_all_values() or sheet.get_all_values()[0] != header:
        sheet.update("A1:G1", [header], value_input_option="USER_ENTERED")
    for row_number, row in updates:
        sheet.update(f"A{row_number}:G{row_number}", [row], value_input_option="USER_ENTERED")
    if inserts:
        sheet.insert_rows(inserts, row=2, value_input_option="USER_ENTERED")


def scan_configured_worrychefs(spreadsheet=None, now_factory=datetime.now,
                               format_time=datetime.dateTime2String,
                               reset_discord=False, target_code="D503", source_kind=None):
    """Collect configured WorryChefs sheets, persist diffs, and route Bot posts."""
    ss = spreadsheet or gspread.getNewArrivalsSheet()
    sheet = ss.worksheet("WorryChefs TL")
    if reset_discord:
        deleted = discord.delete_worrychefs_posts()
        print(f"WorryChefs既存投稿を削除: {deleted}件")
    records = collect_worrychefs_records(load_worrychefs_sources())
    detected_at = format_time(now_factory())
    reference_month = detected_at[:7].replace("-", "/")
    header, inserts, updates = prepare_sheet_changes(sheet.get_all_values(), records, detected_at)
    if inserts or updates or (sheet.get_all_values() and sheet.get_all_values()[0] != header):
        save_record_changes(sheet, header, inserts, updates)
    # 通常時はシート上のハッシュで重複投稿を防止する。
    # Discord投稿を削除して再構築するリセット時だけD503を強制投稿する。
    if reset_discord:
        changed_keys = {record["key"] for record in records
                        if (source_kind is None or record.get("source") == source_kind)
                        and (target_code is None or str(record.get("code", "")).strip().upper() == target_code)}
    else:
        changed_keys = {record["key"] for record in records
                        if (source_kind is None or record.get("source") == source_kind)
                        and (target_code is None or str(record.get("code", "")).strip().upper() == target_code)
                        and record.get("status") in {"new", "updated"}}
    for record in records:
        if record["key"] not in changed_keys:
            continue
        if record["key"] not in changed_keys:
            continue
        content = (f"[WorryChefs更新] {record['code']} ({record['source']})\n"
                   f"新規投稿日時: {record.get('first_seen', detected_at)}\n"
                   f"更新検知日時: {detected_at if record.get('status') == 'updated' else ''}\n"
                   f"参照スプシ: [シートを開く]({record['url']})\n"
                   f"制作者: {record.get('author') or '不明'}\n"
                   f"ダメージ: {record['damage'] or '未記入'}\n\n"
                   f"```md\n{record['text']}\n```")
        image = None
        try:
            image = combined_formation_image(record.get("image_urls", []))
        except Exception as error:
            print(f"警告: 編成画像取得失敗 {record['key']}: {error}")
        files = {"files[0]": (f"{record['code']}_formation.png", image, "image/png")} if image else None
        try:
            discord.post_for_boss(record["code"], content, files=files)
        except Exception as error:
            print(f"失敗: WorryChefs Discord通知 {record['key']}: {error}")
    return records


def _format_manual_row(row):
    """Convert a Manual sheet row to formatter input."""
    if len(row) < 3 or not _TIME.fullmatch(row[1]):
        return ""
    time_value = row[1]
    if time_value.isdigit():
        time_value = f"0:{int(time_value):02d}"
    unit = row[2].strip()
    action = row[3].strip() if len(row) > 3 else ""
    # Columns 2..6 are SET/OFF state in the five formation positions.
    if unit == "SET" and len(row) >= 7:
        mask = "".join(pos if value == "SET" else "-" for pos, value in zip("54321", row[2:7]))
        auto = row[7:9]
        suffix = "🅰️OFF" if auto == ["Auto", "OFF"] else ""
        return f"{time_value} [{mask}]{suffix}".strip()
    return " ".join(part for part in (time_value, unit, action) if part)


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
            lines = [_format_manual_row(row) for row in block_rows]
            lines = [line for line in lines if line]
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
    tl_values = [
        row for row in values
        if row and _is_target_code(str(row[0]).split(",", 1)[0])
    ]
    if not tl_values:
        return
    text = "\n".join(row[0] for row in tl_values)
    notify(f"[WorryChefs]({link_url})\n```cs\n{text}```")


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
        target = "D503"
        source = None
        if "--code" in sys.argv:
            target = sys.argv[sys.argv.index("--code") + 1].strip().upper()
        if "--source" in sys.argv:
            source = sys.argv[sys.argv.index("--source") + 1].strip().lower()
            target = None
        scan_configured_worrychefs(reset_discord=reset, target_code=target, source_kind=source)

        print("-----------------------------------------------")
        print(f"終了{datetime.nowString()}")
        print("-----------------------------------------------")

    return run_locked(run, lock_name="worrychefs.lock")


if __name__ == "__main__":
    main()
