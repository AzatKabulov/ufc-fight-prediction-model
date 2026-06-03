from unittest import TestCase

from datetime import datetime, timezone

from app.schemas import FightHistoryItem, FightRead, FighterRead, RiskSignalRead
from app.services.analyzer import analyze_fight


class AnalyzerContextTests(TestCase):
    def test_prime_and_form_context_moves_older_declining_fighter_down(self) -> None:
        fight = FightRead(
            id="fight",
            event_id="event",
            weight_class="Bantamweight",
            fighter_a=FighterRead(
                id="a",
                name="Prime Fighter",
                age=28,
                stats={"strikes_landed_per_min": 4.2, "strikes_absorbed_per_min": 3.1},
                recent_fights=[
                    FightHistoryItem(result="win", opponent="Recent Opponent", method="U-DEC"),
                    FightHistoryItem(result="win", opponent="Another Opponent", method="KO/TKO"),
                    FightHistoryItem(result="loss", opponent="Elite Opponent", method="U-DEC"),
                ],
            ),
            fighter_b=FighterRead(
                id="b",
                name="Older Fighter",
                age=38,
                stats={"strikes_landed_per_min": 3.8, "strikes_absorbed_per_min": 4.4},
                recent_fights=[
                    FightHistoryItem(result="loss", opponent="Recent Opponent", method="KO/TKO"),
                    FightHistoryItem(result="loss", opponent="Another Opponent", method="U-DEC"),
                    FightHistoryItem(result="win", opponent="Past Opponent", method="SUB"),
                ],
            ),
        )
        vector = {
            "striking_output_diff": 0.0,
            "sig_str_defense_diff": 0.0,
            "takedown_defense_diff": 0.0,
            "takedown_accuracy_diff": 0.0,
            "takedown_activity_diff": 0.0,
            "submission_activity_diff": 0.0,
            "recent_win_rate_diff": 0.0,
            "recent_finish_rate_diff": 0.0,
            "recent_sig_strike_diff_delta": 0.0,
            "recent_damage_absorbed_delta": 0.0,
            "recent_knockdown_absorbed_delta": 0.0,
            "recent_knockdown_for_delta": 0.0,
            "reach_cm_diff": 0.0,
        }

        prediction = analyze_fight(fight, feature_vector=vector, model_probability_a=0.5)

        self.assertGreaterEqual(prediction.pre_dampening_probability, 0.63)
        self.assertLess(prediction.adjusted_probability_a, prediction.pre_dampening_probability)
        self.assertIn("prime-window advantage", " ".join(prediction.risk_signals))
        self.assertIn("multi-fight skid", " ".join(prediction.risk_signals))

    def test_close_probability_becomes_no_pick(self) -> None:
        fight = FightRead(
            id="fight",
            event_id="event",
            weight_class="Lightweight",
            fighter_a=FighterRead(id="a", name="Fighter A", stats={"raw_fight_count": 4}),
            fighter_b=FighterRead(id="b", name="Fighter B", stats={"raw_fight_count": 4}),
        )

        prediction = analyze_fight(fight, model_probability_a=0.54, model_context={"model_source": "winner_extra_trees"})

        self.assertEqual(prediction.pick_grade, "no_pick")
        self.assertEqual(prediction.confidence, "low")
        self.assertIn("54%", prediction.no_pick_reason or "")
        self.assertTrue(any("No official pick" in warning for warning in prediction.trust_warnings))
        self.assertTrue(prediction.model_notes)

    def test_empty_fighter_data_blocks_prediction(self) -> None:
        fight = FightRead(
            id="fight",
            event_id="event",
            weight_class="Lightweight",
            fighter_a=FighterRead(id="a", name="Empty A"),
            fighter_b=FighterRead(id="b", name="Empty B"),
        )

        prediction = analyze_fight(fight, model_probability_a=0.72, model_context={"model_source": "winner_extra_trees"})

        self.assertEqual(prediction.data_quality, 0)
        self.assertEqual(prediction.pick_grade, "no_pick")
        self.assertEqual(prediction.adjusted_probability_a, 0.5)
        self.assertEqual(prediction.likely_method, "insufficient_data")
        self.assertIn("Insufficient fighter data", prediction.no_pick_reason or "")

    def test_raw_and_calibrated_probabilities_stay_separate(self) -> None:
        fight = FightRead(
            id="fight",
            event_id="event",
            weight_class="Lightweight",
            fighter_a=FighterRead(id="a", name="Fighter A", stats={"raw_fight_count": 4}),
            fighter_b=FighterRead(id="b", name="Fighter B", stats={"raw_fight_count": 4}),
        )

        prediction = analyze_fight(
            fight,
            model_probability_a=0.62,
            model_context={
                "model_source": "winner_extra_trees",
                "raw_model_probability_a": 0.7,
            },
        )

        self.assertEqual(prediction.base_probability_a, 0.7)
        self.assertEqual(prediction.calibrated_probability_a, 0.62)
        self.assertEqual(prediction.pre_dampening_probability, 0.62)
        self.assertEqual(prediction.adjusted_probability_a, 0.501)
        self.assertTrue(any("Raw model" in note for note in prediction.model_notes))

    def test_fight_week_adjustment_is_capped(self) -> None:
        fight = FightRead(
            id="fight",
            event_id="event",
            weight_class="Lightweight",
            fighter_a=FighterRead(id="a", name="Fighter A", stats={"raw_fight_count": 4}),
            fighter_b=FighterRead(id="b", name="Fighter B", stats={"raw_fight_count": 4}),
        )
        risk = RiskSignalRead(
            id="risk",
            fighter_id="a",
            fight_id="fight",
            signal_type="injury",
            severity="high",
            confidence="high",
            source="test",
            summary="Oversized positive test signal",
            impact_score=0.5,
            created_at=datetime.now(timezone.utc),
        )

        prediction = analyze_fight(fight, [risk], model_probability_a=0.6, model_context={"model_source": "winner_extra_trees"})

        self.assertLessEqual(prediction.total_adjustment, 0.14)
        self.assertAlmostEqual(prediction.pre_dampening_probability, 0.74)
        self.assertAlmostEqual(prediction.adjusted_probability_a, 0.502)
