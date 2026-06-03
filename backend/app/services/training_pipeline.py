from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import logging
from math import isfinite
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import models
from app.services.feature_builder import build_feature_vector, get_feature_names

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrainingSplit:
    X_train: np.ndarray
    y_train: np.ndarray
    weights_train: np.ndarray
    X_val: np.ndarray
    y_val: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray
    feature_names: list[str]
    train_examples: list[dict[str, Any]]
    val_examples: list[dict[str, Any]]
    test_examples: list[dict[str, Any]]
    split_summary: dict[str, Any]


def extract_training_examples(db: Session, min_date: date | None = None) -> list[dict[str, Any]]:
    """Extract leak-aware v6 examples using deterministic orientation balancing.

    The local historical importer stores most completed fights with the winner in
    fighter_a. If we trained/evaluated directly from that orientation, holdout
    labels would be all one class. This function alternates stored orientation
    before mirroring, which preserves matchup information while removing that
    import-order bias.
    """

    query = (
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Fight.fighter_a_id.is_not(None))
        .where(models.Fight.fighter_b_id.is_not(None))
        .where(models.Event.event_date.is_not(None))
        .options(joinedload(models.Fight.fighter_a), joinedload(models.Fight.fighter_b))
        .order_by(models.Event.event_date.asc(), models.Fight.id.asc())
    )
    if min_date is not None:
        query = query.where(models.Event.event_date >= min_date)

    rows = db.execute(query).all()
    feature_names = get_feature_names()
    examples: list[dict[str, Any]] = []
    elo_available = 0
    style_available = 0
    market_available = 0
    full_feature_coverage = 0

    for index, (fight, event) in enumerate(rows):
        try:
            stored_vector = build_feature_vector(fight.fighter_a, fight.fighter_b, fight, db)
        except Exception as exc:
            log.warning("Skipping fight %s because feature build failed: %s", fight.id, exc)
            continue

        stored_label = 1 if fight.result_winner_id == fight.fighter_a_id else 0
        swap_base_orientation = bool(index % 2)
        if swap_base_orientation:
            vector = mirror_feature_vector(stored_vector)
            label = 1 - stored_label
            fighter_a_id = fight.fighter_b_id
            fighter_b_id = fight.fighter_a_id
        else:
            vector = dict(stored_vector)
            label = stored_label
            fighter_a_id = fight.fighter_a_id
            fighter_b_id = fight.fighter_b_id

        vector = _validated_vector(vector, feature_names, fight.id)
        base_example = {
            "features": vector,
            "label": int(label),
            "fight_id": fight.id,
            "event_date": event.event_date,
            "weight_class": fight.weight_class,
            "fighter_a_id": fighter_a_id,
            "fighter_b_id": fighter_b_id,
            "is_mirror": False,
            "orientation_swapped": swap_base_orientation,
            "elo_available": fight.elo_a_before_fight is not None and fight.elo_b_before_fight is not None,
            "style_available": abs(vector.get("style_prior_probability", 0.5) - 0.5) > 0.0001,
            "market_available": bool(fight.market_feature_available),
            "full_feature_coverage": fight.elo_a_before_fight is not None and fight.ko_collision_score is not None,
        }
        examples.append(base_example)

        mirrored_vector = _validated_vector(mirror_feature_vector(vector), feature_names, f"{fight.id}:mirror")
        examples.append(
            {
                **base_example,
                "features": mirrored_vector,
                "label": 1 - int(label),
                "fighter_a_id": fighter_b_id,
                "fighter_b_id": fighter_a_id,
                "is_mirror": True,
            }
        )

        elo_available += int(base_example["elo_available"])
        style_available += int(base_example["style_available"])
        market_available += int(base_example["market_available"])
        full_feature_coverage += int(base_example["full_feature_coverage"])

    source_fights = len({example["fight_id"] for example in examples})
    print("TASK 1 EXTRACTION SUMMARY")
    print(f"Total fights processed: {source_fights}")
    print(f"Total examples including mirrors: {len(examples)}")
    print(f"Examples where Elo was available: {elo_available}/{source_fights}")
    print(f"Examples where style features were available: {style_available}/{source_fights}")
    print(f"Examples where market features were available: {market_available}/{source_fights}")
    pct_full = round((full_feature_coverage / source_fights * 100.0), 2) if source_fights else 0.0
    print(f"Full feature coverage: {pct_full}%")
    return examples


def mirror_feature_vector(vector: dict[str, float]) -> dict[str, float]:
    mirrored = dict(vector)

    for key in list(mirrored):
        if key.endswith("_differential") or key in {
            "elo_differential",
            "stance_adjustment",
            "striking_dominance_score",
            "chin_vs_power_score",
        }:
            mirrored[key] = -float(mirrored.get(key, 0.0))

    for key in ("elo_implied_probability_a", "style_prior_probability", "market_implied_probability_a", "market_implied_probability"):
        if key in mirrored:
            mirrored[key] = 1.0 - float(mirrored.get(key, 0.5))

    swap_pairs = [
        ("chin_score_a", "chin_score_b"),
        ("ko_loss_count_a", "ko_loss_count_b"),
        ("recent_ko_loss_flag_a", "recent_ko_loss_flag_b"),
        ("ring_rust_flag_a", "ring_rust_flag_b"),
        ("age_vs_peak_a", "age_vs_peak_b"),
        ("past_prime_a", "past_prime_b"),
        ("win_streak_a", "win_streak_b"),
        ("days_since_last_fight_a", "days_since_last_fight_b"),
        ("style_a_encoded", "style_b_encoded"),
        ("elo_a_percentile", "elo_b_percentile"),
        ("finish_rate_rw_a", "finish_rate_rw_b"),
    ]
    for left, right in swap_pairs:
        if left in mirrored and right in mirrored:
            mirrored[left], mirrored[right] = mirrored[right], mirrored[left]

    return mirrored


def compute_sample_weights(examples: list[dict[str, Any]], reference_date: datetime | date | None = None) -> np.ndarray:
    if reference_date is None:
        reference_date = datetime.today()
    if isinstance(reference_date, date) and not isinstance(reference_date, datetime):
        reference_date = datetime.combine(reference_date, datetime.min.time())

    weights = []
    for example in examples:
        fight_date = example["event_date"]
        if isinstance(fight_date, date) and not isinstance(fight_date, datetime):
            fight_date = datetime.combine(fight_date, datetime.min.time())
        years_ago = max(0.0, (reference_date - fight_date).days / 365.25)
        weights.append(float(np.exp(-0.693 * years_ago / 3.0)))

    weights_array = np.array(weights, dtype=float)
    total = weights_array.sum()
    if total <= 0:
        return np.ones(len(examples), dtype=float)
    return weights_array * len(weights_array) / total


def build_train_val_test_split(examples: list[dict[str, Any]]) -> TrainingSplit:
    feature_names = get_feature_names()
    sorted_examples = sorted(examples, key=lambda item: (item["event_date"], item["fight_id"], item["is_mirror"]))

    train_cutoff = date(2023, 1, 1)
    val_start = date(2023, 1, 1)
    test_start = date(2024, 1, 1)
    train = [e for e in sorted_examples if e["event_date"] < train_cutoff]
    val = [e for e in sorted_examples if val_start <= e["event_date"] <= date(2023, 12, 31) and not e["is_mirror"]]
    test = [e for e in sorted_examples if e["event_date"] >= test_start and not e["is_mirror"]]

    if len(test) < 100:
        test_start = date(2023, 7, 1)
        val = [e for e in sorted_examples if val_start <= e["event_date"] < test_start and not e["is_mirror"]]
        test = [e for e in sorted_examples if e["event_date"] >= test_start and not e["is_mirror"]]

    if len(train) < 200 or len(val) < 50 or len(set(e["label"] for e in val)) < 2 or len(set(e["label"] for e in test)) < 2:
        log.warning(
            "Fixed year split is not usable for the local dataset; using chronological 60/20/20 split with mirrors only in training."
        )
        base_examples = [e for e in sorted_examples if not e["is_mirror"]]
        n = len(base_examples)
        train_end = max(1, int(n * 0.60))
        val_end = max(train_end + 1, int(n * 0.80))
        train_ids = {e["fight_id"] for e in base_examples[:train_end]}
        val_ids = {e["fight_id"] for e in base_examples[train_end:val_end]}
        test_ids = {e["fight_id"] for e in base_examples[val_end:]}
        train = [e for e in sorted_examples if e["fight_id"] in train_ids]
        val = [e for e in base_examples if e["fight_id"] in val_ids]
        test = [e for e in base_examples if e["fight_id"] in test_ids]

    X_train, y_train = examples_to_matrix(train, feature_names)
    X_val, y_val = examples_to_matrix(val, feature_names)
    X_test, y_test = examples_to_matrix(test, feature_names)
    weights_train = compute_sample_weights(train)

    summary = {
        "train_examples": len(train),
        "validation_examples": len(val),
        "test_examples": len(test),
        "train_range": _date_range(train),
        "validation_range": _date_range(val),
        "test_range": _date_range(test),
        "mirrors_in_validation": sum(1 for e in val if e["is_mirror"]),
        "mirrors_in_test": sum(1 for e in test if e["is_mirror"]),
    }
    print("TASK 1 SPLIT SUMMARY")
    print(summary)
    return TrainingSplit(X_train, y_train, weights_train, X_val, y_val, X_test, y_test, feature_names, train, val, test, summary)


def apply_noise_augmentation(X_train: np.ndarray, y_train: np.ndarray, n_extra: int = 500) -> tuple[np.ndarray, np.ndarray]:
    if len(X_train) >= 1000 or len(X_train) == 0:
        return X_train, y_train

    rng = np.random.default_rng(42)
    sample_count = min(n_extra, len(X_train))
    indices = rng.choice(len(X_train), size=sample_count, replace=True)
    X_aug = X_train[indices].copy()
    std = X_train.std(axis=0)
    noise = rng.normal(0, 0.02, X_aug.shape) * std
    X_aug = X_aug + noise
    y_aug = y_train[indices].copy()
    return np.vstack([X_train, X_aug]), np.concatenate([y_train, y_aug])


def examples_to_matrix(
    examples: list[dict[str, Any]],
    feature_names: list[str] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    names = feature_names or get_feature_names()
    X = np.array([[float(example["features"].get(name, 0.0)) for name in names] for example in examples], dtype=float)
    y = np.array([int(example["label"]) for example in examples], dtype=int)
    return X, y


def build_X(examples: list[dict[str, Any]], feature_names: list[str] | None = None) -> np.ndarray:
    return examples_to_matrix(examples, feature_names)[0]


def build_y(examples: list[dict[str, Any]]) -> np.ndarray:
    return np.array([int(example["label"]) for example in examples], dtype=int)


def _validated_vector(vector: dict[str, float], feature_names: list[str], fight_id: str) -> dict[str, float]:
    clean: dict[str, float] = {}
    for name in feature_names:
        if name not in vector or vector[name] is None:
            raise ValueError(f"Feature {name} is None for fight {fight_id}")
        value = float(vector[name])
        if not isfinite(value):
            log.warning("Non-finite feature %s=%s for fight %s; replacing with 0.0", name, value, fight_id)
            value = 0.0
        clean[name] = value
    return clean


def _date_range(examples: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    if not examples:
        return None, None
    dates = [example["event_date"] for example in examples]
    return min(dates).isoformat(), max(dates).isoformat()
