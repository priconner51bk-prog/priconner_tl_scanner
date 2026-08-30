import unittest

import boss_names_sync


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self.payload = payload
        self.text = text

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class BossNamesSyncTests(unittest.TestCase):
    def test_extracts_first_five_bosses_in_order(self):
        source = (
            '..., "ラットン", 30, 1, 0, 200600, 102190101, 1,'
            '..., "ネジレアカシア", 30, 1, 0, 200901, 102190102, 1,'
            '..., "トレント", 30, 1, 0, 200902, 102190103, 1,'
            '..., "ゴブリンガード", 30, 1, 0, 203300, 102190104, 1,'
            '..., "リーフボア", 30, 1, 0, 204700, 102190105, 1,'
        )
        self.assertEqual(
            boss_names_sync.extract_boss_names(source, "10219"),
            ["ラットン", "ネジレアカシア", "トレント", "ゴブリンガード", "リーフボア"],
        )

    def test_fetch_source_uses_latest_commit_sha(self):
        calls = []

        def get(url, timeout):
            calls.append((url, timeout))
            if len(calls) == 1:
                return FakeResponse([{"sha": "latest-sha"}])
            return FakeResponse(text="最新コミットのSQL")

        self.assertEqual(
            boss_names_sync.fetch_source(get=get),
            "最新コミットのSQL",
        )
        self.assertIn("commits?per_page=1", calls[0][0])
        self.assertIn("/latest-sha/", calls[1][0])

    def test_retry_sleeps_five_minutes_until_success(self):
        attempts = []
        sleeps = []

        def sync():
            attempts.append(True)
            return len(attempts) == 2

        result = boss_names_sync.retry_until_success(
            sync=sync,
            sleep=sleeps.append,
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [boss_names_sync.RETRY_INTERVAL])

    def test_retry_sleeps_after_fetch_exception(self):
        attempts = []
        sleeps = []

        def sync():
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("not ready")
            return True

        result = boss_names_sync.retry_until_success(
            sync=sync,
            sleep=sleeps.append,
        )

        self.assertEqual(result, 0)
        self.assertEqual(sleeps, [boss_names_sync.RETRY_INTERVAL])


if __name__ == "__main__":
    unittest.main()
