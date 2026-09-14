"""Export a published Google Sheet as a normalized Google Sheet.

This module is intentionally separate from ``tl_formatting.py``.  It owns the
I/O pipeline for the published source workbook and leaves TL-specific parsing
as an injectable processor until the source layout is finalized.
"""

from __future__ import annotations

import csv
import io
import argparse
import os
from dataclasses import dataclass
from typing import Callable, Sequence

import requests


DEFAULT_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
Row = list[str]
Rows = list[Row]
Processor = Callable[[Rows], Rows]


@dataclass(frozen=True)
class ExportConfig:
    publish_id: str
    source_gid: str
    service_account_file: str = "service_account.json"
    output_title: str = "プリコネTL_加工済み"
    output_sheet_title: str = "TL"


def fetch_public_sheet(
    publish_id: str,
    source_gid: str,
    *,
    session: requests.Session | None = None,
    http_get=None,
    timeout: int = 30,
) -> Rows:
    """Fetch one tab of a published spreadsheet as a rectangular CSV matrix."""
    url = (
        f"https://docs.google.com/spreadsheets/d/e/{publish_id}/pub"
        f"?gid={source_gid}&single=true&output=csv"
    )
    getter = http_get or (session.get if session is not None else requests.get)
    response = getter(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    if not rows:
        raise RuntimeError("公開スプレッドシートが空です")
    width = max(map(len, rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def normalize_rows(rows: Sequence[Sequence[object]]) -> Rows:
    """Apply safe, layout-independent cleanup to source cells."""
    return [
        [str(value).strip().replace("->", "").replace(">", "").strip() for value in row]
        for row in rows
    ]


def process_rows(rows: Rows, processor: Processor | None = None) -> Rows:
    """Normalize rows, optionally passing them through a TL parser."""
    normalized = normalize_rows(rows)
    return normalized if processor is None else processor(normalized)


def get_sheets_service(service_account_file: str):
    """Create a Sheets API client using only the Sheets scope."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials.from_service_account_file(
        service_account_file, scopes=list(DEFAULT_SCOPES)
    )
    return build("sheets", "v4", credentials=credentials)


def create_spreadsheet(service, title: str, sheet_title: str = "TL") -> str:
    result = service.spreadsheets().create(
        body={
            "properties": {"title": title},
            "sheets": [{"properties": {"title": sheet_title}}],
        }
    ).execute()
    return result["spreadsheetId"]


def write_rows(service, spreadsheet_id: str, rows: Rows, sheet_title: str = "TL") -> None:
    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_title}!A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()


def export(config: ExportConfig, processor: Processor | None = None, *, session=None) -> str:
    rows = fetch_public_sheet(config.publish_id, config.source_gid, session=session)
    processed = process_rows(rows, processor)
    service = get_sheets_service(config.service_account_file)
    spreadsheet_id = create_spreadsheet(service, config.output_title, config.output_sheet_title)
    write_rows(service, spreadsheet_id, processed, config.output_sheet_title)
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for the published-sheet export pipeline."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-id", default=os.environ.get("PUBLISH_ID"))
    parser.add_argument("--source-gid", default=os.environ.get("SOURCE_GID"))
    parser.add_argument(
        "--service-account-file",
        default=os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json"),
    )
    parser.add_argument(
        "--output-title",
        default=os.environ.get("OUTPUT_TITLE", "プリコネTL_加工済み"),
    )
    parser.add_argument(
        "--output-sheet-title",
        default=os.environ.get("OUTPUT_SHEET_TITLE", "TL"),
    )
    args = parser.parse_args(argv)
    if not args.publish_id or not args.source_gid:
        parser.error("--publish-id と --source-gid（または PUBLISH_ID / SOURCE_GID）が必要です")

    url = export(
        ExportConfig(
            publish_id=args.publish_id,
            source_gid=args.source_gid,
            service_account_file=args.service_account_file,
            output_title=args.output_title,
            output_sheet_title=args.output_sheet_title,
        )
    )
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
