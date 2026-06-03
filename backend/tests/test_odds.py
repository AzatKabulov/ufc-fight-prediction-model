from datetime import datetime, timezone
from unittest import TestCase

from app.schemas import OddsSnapshotRead, PredictionRead
from app.services.odds import (
    american_to_decimal,
    american_to_implied_probability,
    decimal_to_american,
    apply_market_context,
    no_vig_probability,
)
from app.services.onewin_odds import parse_1win_odds_text


class OddsEngineTests(TestCase):
    def test_american_odds_conversion(self) -> None:
        self.assertEqual(american_to_decimal(+150), 2.5)
        self.assertAlmostEqual(american_to_decimal(-150), 1.6667)
        self.assertAlmostEqual(american_to_implied_probability(+150), 0.4)
        self.assertAlmostEqual(american_to_implied_probability(-150), 0.6)
        self.assertEqual(decimal_to_american(2.5), 150)
        self.assertEqual(decimal_to_american(1.5), -200)

    def test_no_vig_probability_normalizes_two_sided_market(self) -> None:
        self.assertAlmostEqual(no_vig_probability(0.6, 0.45), 0.571429)

    def test_apply_market_context_flags_positive_value(self) -> None:
        prediction = PredictionRead(
            id="prediction",
            fight_id="fight",
            fighter_a="Fighter A",
            fighter_b="Fighter B",
            base_probability_a=0.66,
            calibrated_probability_a=0.66,
            adjusted_probability_a=0.66,
            fightiq_probability_a=0.66,
            confidence="medium",
            likely_method="decision",
            data_quality=82,
            main_factors=["recent form"],
            risk_signals=[],
            method_probabilities={"decision": 0.6, "KO/TKO": 0.25, "submission": 0.1, "other": 0.05},
            style_summary="Test",
            relevant_fights=[],
            pick_grade="medium",
            created_at=datetime.now(timezone.utc),
        )
        odds = OddsSnapshotRead(
            id="odds",
            fight_id="fight",
            source="manual",
            sportsbook="TestBook",
            snapshot_type="current",
            fighter_a_american_odds=130,
            fighter_b_american_odds=-150,
            captured_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )

        updated = apply_market_context(prediction, odds)

        self.assertTrue(updated.value_flag)
        self.assertIsNotNone(updated.market_probability_a)
        self.assertGreater(updated.edge or 0, 0.04)
        self.assertGreater(updated.expected_value or 0, 0.04)
        self.assertIn("possible value", updated.odds_summary)

    def test_large_market_gap_with_low_data_forces_no_pick(self) -> None:
        prediction = PredictionRead(
            id="prediction",
            fight_id="fight",
            fighter_a="Fighter A",
            fighter_b="Fighter B",
            base_probability_a=0.75,
            calibrated_probability_a=0.75,
            adjusted_probability_a=0.75,
            fightiq_probability_a=0.75,
            confidence="medium",
            likely_method="decision",
            data_quality=45,
            main_factors=["recent form"],
            risk_signals=[],
            method_probabilities={"decision": 0.6, "KO/TKO": 0.25, "submission": 0.1, "other": 0.05},
            style_summary="Test",
            relevant_fights=[],
            pick_grade="lean",
            created_at=datetime.now(timezone.utc),
        )
        odds = OddsSnapshotRead(
            id="odds",
            fight_id="fight",
            source="manual",
            sportsbook="TestBook",
            snapshot_type="current",
            fighter_a_american_odds=300,
            fighter_b_american_odds=-400,
            captured_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )

        updated = apply_market_context(prediction, odds)

        self.assertEqual(updated.pick_grade, "no_pick")
        self.assertEqual(updated.confidence, "low")
        self.assertIn("market odds", updated.no_pick_reason or "")
        self.assertTrue(any("Large gap" in warning for warning in updated.trust_warnings))

    def test_parse_1win_static_odds_text(self) -> None:
        html = """
        <html><body>
          <div>UFC Freedom 250 Ilia Topuria 1.35 Justin Gaethje 3.20 Main card</div>
        </body></html>
        """

        parsed = parse_1win_odds_text(html, "Ilia Topuria", "Justin Gaethje")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["fighter_a_decimal_odds"], 1.35)
        self.assertEqual(parsed["fighter_b_decimal_odds"], 3.20)
