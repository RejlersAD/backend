from datetime import datetime, timedelta, timezone
from django.test import SimpleTestCase
from apps.rbac.ai_engagement import engagement_score


class EngagementTests(SimpleTestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 3, tzinfo=timezone.utc)
        self.end = self.start + timedelta(days=28)

    def score(self, days, used=('a',), grants=('a',)):
        return engagement_score({(self.start + timedelta(days=d)).date() for d in days},
                                used, grants, self.start, self.end)

    def test_unknown_is_not_zero_engagement(self):
        self.assertIsNone(self.score([])['score'])
        self.assertEqual(self.score([])['band'], 'Unknown')

    def test_fixed_targets_and_weights(self):
        result = self.score([0, 7, 14, 21], grants=('a', 'b'))
        self.assertEqual(result['score'], 53)  # 8 frequency + 15 breadth + 30 consistency
        self.assertEqual(result['band'], 'Moderate')

    def test_caps_and_window_boundaries(self):
        self.assertEqual(self.score(range(-2, 31))['score'], 100)
        self.assertEqual(self.score([-1, 28])['active_days'], 0)

    def test_unentitled_modules_do_not_inflate_breadth(self):
        self.assertEqual(self.score([0], used=('a', 'other'))['modules_used'], 1)
        self.assertEqual(self.score([0], grants=())['feature_usage'], 0)

    def test_repeated_observations_do_not_inflate_frequency(self):
        self.assertEqual(self.score([0] * 100), self.score([0]))
