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
    def test_extracts_five_main_bosses_and_skips_parts(self):
        source = (
            '..., "マダムエレクトラ", 0, 401908101,\n'
            '..., "ミストシーカー", 0, 401908102,\n'
            '..., "バイオドーザー", 0, 401908103,\n'
            '..., "トライロッカー", 0, 401908104,\n'
            '..., "トライロッカーA", 0, 401908105,\n'
            '..., "トライロッカーB", 0, 401908106,\n'
            '..., "トライロッカーC", 0, 401908107,\n'
            '..., "メデューサ", 0, 401908108,\n'
        )
        self.assertEqual(
            boss_names_sync.extract_boss_names(source, "401908"),
            ["マダムエレクトラ", "ミストシーカー", "バイオドーザー", "トライロッカー", "メデューサ"],
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
