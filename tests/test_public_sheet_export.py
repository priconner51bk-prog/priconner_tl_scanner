import public_sheet_export as exporter


class Response:
    content = "\ufeffa, b\n1,2\n".encode("utf-8")

    def raise_for_status(self):
        return None


class Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response()


def test_fetch_public_sheet_returns_rectangular_rows():
    session = Session()
    assert exporter.fetch_public_sheet("pub", "123", session=session) == [
        ["a", " b"],
        ["1", "2"],
    ]
    assert "gid=123" in session.calls[0][0]


def test_process_rows_normalizes_and_supports_parser():
    rows = [[" D503 -> ", " A > B "]]
    assert exporter.process_rows(rows) == [["D503", "A  B"]]
    assert exporter.process_rows(rows, lambda value: [value[0] + ["parsed"]]) == [
        ["D503", "A  B", "parsed"]
    ]


def test_write_rows_skips_empty_payload():
    class Values:
        def update(self, **kwargs):
            raise AssertionError("must not write empty rows")

    class Spreadsheets:
        def values(self):
            return Values()

    exporter.write_rows(Spreadsheets(), "id", [])


def test_main_builds_config_from_cli(monkeypatch):
    captured = {}

    def fake_export(config):
        captured["config"] = config
        return "https://docs.google.com/spreadsheets/d/result/edit"

    monkeypatch.setattr(exporter, "export", fake_export)
    assert exporter.main(["--publish-id", "p", "--source-gid", "7"]) == 0
    assert captured["config"].publish_id == "p"
    assert captured["config"].source_gid == "7"
