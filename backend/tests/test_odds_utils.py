from unittest import TestCase

from app.services.odds_utils import (
    american_to_decimal,
    american_to_implied_raw,
    compute_edge,
    compute_kelly_fraction,
    implied_to_american,
    remove_vig,
)
from app.services.odds_scraper import OddsSource
from app.services.odds_service import blend_with_market


class OddsUtilsTests(TestCase):
    def test_american_to_decimal(self) -> None:
        self.assertAlmostEqual(american_to_decimal(+130), 2.3)
        self.assertAlmostEqual(american_to_decimal(-150), 1.6667, places=4)

    def test_american_to_implied_raw(self) -> None:
        self.assertAlmostEqual(american_to_implied_raw(-150), 0.60, places=3)
        self.assertAlmostEqual(american_to_implied_raw(+130), 0.435, places=3)

    def test_remove_vig(self) -> None:
        clean_a, clean_b = remove_vig(0.60, 0.435)
        self.assertAlmostEqual(clean_a, 0.58, places=2)
        self.assertAlmostEqual(clean_b, 0.42, places=2)
        self.assertAlmostEqual(clean_a + clean_b, 1.0, places=6)

    def test_implied_to_american(self) -> None:
        self.assertEqual(implied_to_american(0.60), -150)
        self.assertEqual(implied_to_american(0.40), 150)

    def test_compute_edge(self) -> None:
        self.assertAlmostEqual(compute_edge(0.62, 0.58), 0.04)

    def test_compute_kelly_fraction_is_informational_and_nonnegative(self) -> None:
        self.assertGreaterEqual(compute_kelly_fraction(0.04, 2.0), 0.0)
        self.assertEqual(compute_kelly_fraction(-0.02, 2.0), 0.0)

    def test_market_blend_weights_by_data_quality(self) -> None:
        low_quality, low_weight = blend_with_market(0.70, 0.50, 30)
        high_quality, high_weight = blend_with_market(0.70, 0.50, 85)
        self.assertEqual(low_weight, 0.50)
        self.assertEqual(high_weight, 0.15)
        self.assertAlmostEqual(low_quality, 0.60)
        self.assertAlmostEqual(high_quality, 0.67)

    def test_parse_american_odds(self) -> None:
        source = OddsSource.__new__(OddsSource)
        self.assertEqual(source._parse_american_odds("-150"), -150)
        self.assertEqual(source._parse_american_odds("+130"), 130)
        self.assertEqual(source._parse_american_odds("150"), 150)
        self.assertEqual(source._parse_american_odds("EVEN"), 100)
