import unittest

import boss_names_sync


class BossNamesSyncTests(unittest.TestCase):
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
