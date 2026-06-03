from datetime import date
from unittest import TestCase

from app.schemas import FightHistoryItem, FightRead, FighterRead
from app.services.features import HistoricalFight, build_current_matchup_features, build_historical_matchup_features, estimate_data_quality


class FeatureBuilderTests(TestCase):
    def test_future_fights_are_not_used_for_historical_features(self) -> None:
        fights = [
            HistoricalFight(date(2024, 1, 1), "Fighter A", "Opponent 1", "win", 20, 2, 1),
            HistoricalFight(date(2024, 1, 1), "Fighter B", "Opponent 2", "loss", -5, 0, 0),
            HistoricalFight(date(2025, 1, 1), "Fighter A", "Future Opponent", "loss", -40, 0, 0),
        ]

        features = build_historical_matchup_features(fights, "Fighter A", "Fighter B", date(2024, 6, 1))

        self.assertEqual(features["ufc_experience_diff"], 0.0)
        self.assertGreater(features["recent_win_rate_diff"], 0)
        self.assertGreater(features["sig_str_diff_delta"], 0)

    def test_current_matchup_features_include_profile_and_recent_form(self) -> None:
        fight = FightRead(
            id="fight-1",
            event_id="event-1",
            weight_class="Lightweight",
            fighter_a=FighterRead(
                id="a",
                name="Fighter A",
                height_cm=180,
                reach_cm=185,
                stats={
                    "raw_fight_count": 10,
                    "strikes_landed_per_min": 5.0,
                    "strikes_absorbed_per_min": 3.0,
                    "sig_str_acc": 0.5,
                    "sig_str_def": 0.6,
                    "td_acc": 0.4,
                    "td_def": 0.8,
                },
                recent_fights=[
                    FightHistoryItem(result="win", method="KO/TKO Punch", sig_strikes_for=50, sig_strikes_against=20, takedowns_for=1, takedowns_against=0),
                    FightHistoryItem(result="win", method="Decision", sig_strikes_for=40, sig_strikes_against=35, takedowns_for=0, takedowns_against=1),
                ],
            ),
            fighter_b=FighterRead(
                id="b",
                name="Fighter B",
                height_cm=178,
                reach_cm=180,
                stats={
                    "raw_fight_count": 7,
                    "strikes_landed_per_min": 3.0,
                    "strikes_absorbed_per_min": 4.0,
                    "sig_str_acc": 0.42,
                    "sig_str_def": 0.52,
                    "td_acc": 0.2,
                    "td_def": 0.65,
                },
                recent_fights=[
                    FightHistoryItem(result="loss", method="Decision", sig_strikes_for=30, sig_strikes_against=45, takedowns_for=0, takedowns_against=2),
                ],
            ),
        )

        features = build_current_matchup_features(fight)

        self.assertGreater(features["striking_output_diff"], 0)
        self.assertGreater(features["recent_win_rate_diff"], 0)
        self.assertEqual(features["reach_cm_diff"], 5.0)
        self.assertIn("reach_to_height_ratio_diff", features)
        self.assertIn("grappling_control_pressure_diff", features)
        self.assertIn("ring_rust_log_diff", features)
        self.assertGreaterEqual(estimate_data_quality(fight), 80)

    def test_empty_fighter_data_forces_zero_quality(self) -> None:
        fight = FightRead(
            id="fight-empty",
            event_id="event-empty",
            weight_class="Lightweight",
            fighter_a=FighterRead(id="a", name="Empty A"),
            fighter_b=FighterRead(
                id="b",
                name="Loaded B",
                stats={"strikes_landed_per_min": 4.0, "sig_str_acc": 0.45},
                recent_fights=[FightHistoryItem(result="win", method="Decision")],
            ),
        )

        self.assertEqual(estimate_data_quality(fight), 0)
