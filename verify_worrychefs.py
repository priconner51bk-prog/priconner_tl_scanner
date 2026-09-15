"""Read-only comparison of published simple TLs and Discord WorryChefs posts."""

import re
import requests

import discord_utils
import public_sheet_export
import worrychefs


def _messages(channel):
    messages = []
    before = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        response = requests.get(
            f"https://discord.com/api/v10/channels/{channel}/messages",
            headers={"Authorization": f"Bot {discord_utils._bot_token()}"},
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        page = response.json()
        messages.extend(page)
        if len(page) < 100:
            return messages
        before = page[-1]["id"]


def main():
    sources = worrychefs.load_worrychefs_sources()
    simple = next(source for source in sources if source["name"] == "simple")
    rows = public_sheet_export.fetch_public_sheet(simple["spreadsheet_id"], simple["gid"])
    records = [record for record in worrychefs.collect_worrychefs_records([simple])
               if record.get("code")]
    expected = {
        record["code"]: worrychefs.canonicalize_tl(worrychefs.format_tl_text(record["text"]))
        for record in records
    }
    actual = {}
    for boss in range(1, 6):
        channel = discord_utils.channel_id("default", f"boss{boss}_tl")
        for message in _messages(channel):
            content = message.get("content", "")
            if not content.startswith("[WorryChefs更新]"):
                continue
            match = re.search(r"\bD[1-5]\d{1,2}\b", content)
            block = re.search(r"```(?:md)?\n(.*?)```", content, re.DOTALL)
            if match and block:
                actual[match.group(0)] = worrychefs.canonicalize_tl(block.group(1))
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    different = sorted(code for code in set(expected) & set(actual) if expected[code] != actual[code])
    print(f"source={len(expected)} discord={len(actual)}")
    print("missing:", ", ".join(missing) or "なし")
    print("extra:", ", ".join(extra) or "なし")
    print("different:", ", ".join(different) or "なし")
    for code in different:
        print(f"\n[{code}] source={expected[code]!r}\n[{code}] discord={actual[code]!r}")


if __name__ == "__main__":
    main()
