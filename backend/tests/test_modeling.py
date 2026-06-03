from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models
from app.db.base import Base
from app.schemas import FightHistoryItem, FightRead, FighterRead
from app.services.modeling import build_training_dataset, predict_fight_with_latest_model, train_winner_model


class ModelingTests(TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_training_dataset_uses_only_prior_fights(self) -> None:
        with self.Session() as db:
            fighter_a = self._fighter(db, "Fighter A")
            fighter_b = self._fighter(db, "Fighter B")
            fighter_x = self._fighter(db, "Fighter X")
            fighter_y = self._fighter(db, "Fighter Y")

            self._completed_fight(
                db,
                date(2024, 1, 1),
                fighter_a,
                fighter_x,
                winner=fighter_a,
                a_stats={"sig_strikes_landed": 50, "takedowns_landed": 2, "submission_attempts": 0, "knockdowns": 1},
                b_stats={"sig_strikes_landed": 25, "takedowns_landed": 0, "submission_attempts": 0, "knockdowns": 0},
            )
            self._completed_fight(
                db,
                date(2024, 2, 1),
                fighter_b,
                fighter_y,
                winner=fighter_y,
                a_stats={"sig_strikes_landed": 20, "takedowns_landed": 0, "submission_attempts": 0, "knockdowns": 0},
                b_stats={"sig_strikes_landed": 40, "takedowns_landed": 3, "submission_attempts": 1, "knockdowns": 0},
            )
            target_fight_id = self._completed_fight(
                db,
                date(2024, 3, 1),
                fighter_a,
                fighter_b,
                winner=fighter_a,
                a_stats={"sig_strikes_landed": 5, "takedowns_landed": 0, "submission_attempts": 0, "knockdowns": 0},
                b_stats={"sig_strikes_landed": 100, "takedowns_landed": 5, "submission_attempts": 2, "knockdowns": 3},
            )
            self._completed_fight(
                db,
                date(2024, 12, 1),
                fighter_a,
                fighter_x,
                winner=fighter_x,
                a_stats={"sig_strikes_landed": 999, "takedowns_landed": 9, "submission_attempts": 9, "knockdowns": 9},
                b_stats={"sig_strikes_landed": 1, "takedowns_landed": 0, "submission_attempts": 0, "knockdowns": 0},
            )

            examples = build_training_dataset(db, min_prior_fights=1, include_mirrored=False)

        target = next(example for example in examples if example.fight_id == target_fight_id)
        self.assertEqual(target.label, 1)
        self.assertEqual(target.vector["ufc_experience_diff"], 0.0)
        self.assertEqual(target.vector["sig_strikes_landed_avg_diff"], 30.0)
        self.assertEqual(target.vector["sig_strikes_absorbed_avg_diff"], -15.0)
        self.assertEqual(target.vector["striking_differential_avg_diff"], 45.0)
        self.assertGreater(target.vector["career_win_rate_diff"], 0)

    def test_debutant_features_are_flagged_and_imputed(self) -> None:
        with self.Session() as db:
            debutant = self._fighter(db, "Debutant")
            veteran = self._fighter(db, "Veteran")
            opponent = self._fighter(db, "Opponent")

            self._completed_fight(
                db,
                date(2024, 1, 1),
                veteran,
                opponent,
                winner=veteran,
                a_stats={"sig_strikes_landed": 30, "sig_strikes_against": 20, "takedowns_landed": 1},
                b_stats={"sig_strikes_landed": 20, "sig_strikes_against": 30, "takedowns_landed": 0},
            )
            target_fight_id = self._completed_fight(
                db,
                date(2024, 3, 1),
                debutant,
                veteran,
                winner=veteran,
            )

            examples = build_training_dataset(db, min_prior_fights=0, include_mirrored=False)

        target = next(example for example in examples if example.fight_id == target_fight_id)
        self.assertEqual(target.vector["is_debutant_diff"], 1.0)
        self.assertEqual(target.vector["long_layoff_diff"], 1.0)
        self.assertGreater(target.vector["log_ring_rust_diff"], 0)
        self.assertNotEqual(target.vector["sig_strikes_landed_avg_diff"], -30.0)

    def test_winner_model_training_persists_model_version(self) -> None:
        with TemporaryDirectory() as artifact_dir:
            with self.Session() as db:
                fighter_a = self._fighter(db, "Fighter A")
                fighter_b = self._fighter(db, "Fighter B")
                fighter_x = self._fighter(db, "Fighter X")
                fighter_y = self._fighter(db, "Fighter Y")

                self._completed_fight(db, date(2023, 1, 1), fighter_a, fighter_x, fighter_a)
                self._completed_fight(db, date(2023, 2, 1), fighter_b, fighter_y, fighter_y)
                self._completed_fight(db, date(2024, 1, 1), fighter_a, fighter_b, fighter_a)

                result = train_winner_model(db, min_examples=2, artifact_dir=Path(artifact_dir))
                model_versions = db.scalars(select(models.ModelVersion)).all()
                fight = FightRead(
                    id="upcoming-fight",
                    event_id="upcoming-event",
                    weight_class="Lightweight",
                    fighter_a=FighterRead(
                        id=fighter_a.id,
                        name=fighter_a.name,
                        stats={"raw_fight_count": 2},
                        recent_fights=[
                            FightHistoryItem(
                                result="win",
                                date="2024-01-01",
                                method="U-DEC",
                                sig_strikes_for=40,
                                takedowns_for=1,
                            )
                        ],
                    ),
                    fighter_b=FighterRead(
                        id=fighter_b.id,
                        name=fighter_b.name,
                        stats={"raw_fight_count": 2},
                        recent_fights=[
                            FightHistoryItem(
                                result="loss",
                                date="2024-02-01",
                                method="U-DEC",
                                sig_strikes_for=20,
                                takedowns_for=0,
                            )
                        ],
                    ),
                )
                prediction = predict_fight_with_latest_model(db, fight)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(model_versions), 1)
        self.assertIn("model_version_id", result["result"])
        self.assertIsNone(prediction)
        self.assertTrue(model_versions[0].is_deprecated)
        self.assertFalse(model_versions[0].is_active)
        self.assertIn("calibration", result["result"]["metrics"])

    def _fighter(self, db, name: str) -> models.Fighter:
        fighter = models.Fighter(id=str(uuid4()), name=name, slug=name.lower().replace(" ", "-"), profile_stats={})
        db.add(fighter)
        db.flush()
        return fighter

    def _completed_fight(
        self,
        db,
        event_date: date,
        fighter_a: models.Fighter,
        fighter_b: models.Fighter,
        winner: models.Fighter,
        a_stats: dict | None = None,
        b_stats: dict | None = None,
    ) -> str:
        event = models.Event(
            id=str(uuid4()),
            name=f"Event {event_date.isoformat()}",
            event_date=event_date,
            location="Test City",
            status="completed",
        )
        fight = models.Fight(
            id=str(uuid4()),
            event_id=event.id,
            fighter_a_id=fighter_a.id,
            fighter_b_id=fighter_b.id,
            weight_class="Lightweight",
            bout_order=1,
            scheduled_rounds=3,
            status="completed",
            result_winner_id=winner.id,
            result_method="U-DEC",
        )
        db.add_all([event, fight])
        db.flush()

        self._stats_row(db, fight, fighter_a, fighter_b, winner, a_stats or {})
        self._stats_row(db, fight, fighter_b, fighter_a, winner, b_stats or {})
        db.commit()
        return fight.id

    def _stats_row(
        self,
        db,
        fight: models.Fight,
        fighter: models.Fighter,
        opponent: models.Fighter,
        winner: models.Fighter,
        stats: dict,
    ) -> None:
        db.add(
            models.FighterFightStats(
                fight_id=fight.id,
                fighter_id=fighter.id,
                opponent_id=opponent.id,
                stats={
                    "result": "win" if fighter.id == winner.id else "loss",
                    "method": fight.result_method,
                    "weight_class": fight.weight_class,
                    "sig_strikes_landed": stats.get("sig_strikes_landed", 30),
                    "takedowns_landed": stats.get("takedowns_landed", 1),
                    "submission_attempts": stats.get("submission_attempts", 0),
                    "knockdowns": stats.get("knockdowns", 0),
                },
            )
        )
