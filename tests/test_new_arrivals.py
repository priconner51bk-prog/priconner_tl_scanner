from pathlib import Path

from new_arrivals_markdown import write_arrival


def test_write_arrival_accepts_string_published_at(tmp_path: Path):
    result = write_arrival(
        "youtube-search",
        "sample",
        "https://example.test/video",
        published_at="2026-09-13T00:00:00Z",
        directory=tmp_path,
    )

    assert result.read_text(encoding="utf-8").splitlines()[3] == (
        "published_at: 2026-09-13T00:00:00Z"
    )
