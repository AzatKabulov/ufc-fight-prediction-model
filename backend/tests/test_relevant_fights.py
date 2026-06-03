from unittest import TestCase

from app.schemas import FightHistoryItem, FightRead, FighterRead
from app.services.relevant_fights import build_relevant_fight_notes, build_style_summary


class RelevantFightFinderTests(TestCase):
    def test_relevant_fight_notes_include_common_opponents_and_style_samples(self) -> None:
        fight = FightRead(
            id="fight-1",
            event_id="event-1",
            weight_class="Lightweight",
            fighter_a=FighterRead(
                id="a",
                name="Fighter A",
                stats={
                    "strikes_landed_per_min": 4.4,
                    "strikes_absorbed_per_min": 3.1,
                    "td_avg_per_15": 1.6,
                    "td_def": 0.82,
                },
                recent_fights=[
                    FightHistoryItem(
                        result="win",
                        opponent="Common Opponent",
                        date="2025-01-01",
                        method="KO/TKO Punch",
                        sig_strikes_against=20,
                    ),
                    FightHistoryItem(
                        result="loss",
                        opponent="Volume Striker",
                        date="2024-01-01",
                        method="U-DEC",
                        sig_strikes_against=92,
                    ),
                ],
            ),
            fighter_b=FighterRead(
                id="b",
                name="Fighter B",
                stats={
                    "strikes_landed_per_min": 5.8,
                    "strikes_absorbed_per_min": 5.2,
                    "finish_rate": 0.62,
                },
                recent_fights=[
                    FightHistoryItem(
                        result="loss",
                        opponent="Common Opponent",
                        date="2024-08-01",
                        method="S-DEC",
                        takedowns_against=1,
                    ),
                    FightHistoryItem(
                        result="win",
                        opponent="Wrestler",
                        date="2024-03-01",
                        method="U-DEC",
                        takedowns_against=4,
                    ),
                ],
            ),
        )

        notes = build_relevant_fight_notes(
            fight,
            {
                "striking_output_diff": 1.0,
                "recent_damage_absorbed_delta": 30.0,
            },
        )
        summary = build_style_summary(fight, 0.61, {"striking_output_diff": 1.0})

        self.assertTrue(any("Common opponent" in note for note in notes))
        self.assertTrue(any("Volume-striking sample" in note for note in notes))
        self.assertTrue(any("Grappling sample" in note for note in notes))
        self.assertTrue(any("finish signal" in note for note in notes))
        self.assertIn("striking differential", summary)
