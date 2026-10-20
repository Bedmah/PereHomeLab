import unittest

from app.rate_limit import InteractionLimiter


class InteractionLimiterTest(unittest.TestCase):
    def test_burst_recovers_and_notifications_are_limited(self):
        now = [0.0]
        limiter = InteractionLimiter(clock=lambda: now[0])
        self.assertTrue(all(limiter.allow('user')[0] for _ in range(12)))
        self.assertEqual(limiter.allow('user'), (False, True))
        self.assertEqual(limiter.allow('user'), (False, False))
        self.assertTrue(limiter.allow('other')[0])
        now[0] += 10
        self.assertTrue(all(limiter.allow('user')[0] for _ in range(12)))
        self.assertEqual(limiter.allow('user'), (False, True))

    def test_memory_is_bounded(self):
        limiter = InteractionLimiter(max_keys=32)
        for user in range(10000):
            limiter.allow(user)
        self.assertEqual(len(limiter.buckets), 32)
