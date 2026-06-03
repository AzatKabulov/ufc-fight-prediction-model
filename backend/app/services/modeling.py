from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging
from math import log1p
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import models
from app.schemas import FightHistoryItem, FightRead, FighterRead
from app.services import db_store

FEATURE_VERSION = "historical-v5"
SUPPORTED_WINNER_ALGORITHMS = (
    "logistic_regression",
    "random_forest",
    "gradient_boosting",
    "extra_trees",
    "StackingEnsemble_XGB_LGBM_ET",
)
MIN_ROLLING_TRAIN_EXAMPLES = 50
MIN_ROLLING_TEST_EXAMPLES = 20
PURGE_GAP_DAYS = 7
SAFE_VALIDATION_TYPE = "rolling_year_holdout"
MIN_SAFE_SOURCE_FIGHTS = 200
log = logging.getLogger(__name__)
FEATURE_KEYS = [
    "height_cm_diff",
    "reach_cm_diff",
    "reach_to_height_ratio_diff",
    "ufc_experience_diff",
    "is_debutant_diff",
    "career_win_rate_diff",
    "recent_win_rate_diff",
    "decayed_win_rate_diff",
    "sig_strikes_landed_avg_diff",
    "sig_strikes_absorbed_avg_diff",
    "recent_sig_strikes_landed_avg_diff",
    "recent_sig_strikes_absorbed_avg_diff",
    "striking_differential_avg_diff",
    "recent_striking_differential_avg_diff",
    "decayed_striking_differential_diff",
    "takedowns_landed_avg_diff",
    "takedowns_allowed_avg_diff",
    "takedown_differential_avg_diff",
    "submission_attempts_avg_diff",
    "submission_attempts_allowed_avg_diff",
    "knockdowns_avg_diff",
    "knockdowns_absorbed_avg_diff",
    "recent_knockdowns_absorbed_avg_diff",
    "recent_ko_loss_flag_diff",
    "grappling_control_pressure_diff",
    "submission_grappling_pressure_diff",
    "finish_threat_durability_diff",
    "finish_win_rate_diff",
    "finish_loss_rate_diff",
    "ko_loss_rate_diff",
    "submission_loss_rate_diff",
    "win_streak_diff",
    "loss_streak_diff",
    "age_vs_peak_diff",
    "days_since_last_fight_diff",
    "log_ring_rust_diff",
    "long_layoff_diff",
    "short_turnaround_diff",
    "same_weight_class_experience_diff",
    "weight_class_kg",
]

DEFAULT_DEBUTANT_DAYS_SINCE_LAST_FIGHT = 365
DIVISION_MEDIAN_PRIOR = {
    "height_cm": 178.0,
    "reach_cm": 180.0,
    "sig_strikes_landed": 35.0,
    "sig_strikes_absorbed": 35.0,
    "takedowns_landed": 1.0,
    "takedowns_allowed": 1.0,
    "submission_attempts": 0.2,
    "submission_attempts_allowed": 0.2,
    "knockdowns": 0.15,
    "knockdowns_absorbed": 0.15,
    "finish_win_rate": 0.48,
    "finish_loss_rate": 0.28,
    "ko_loss_rate": 0.12,
    "submission_loss_rate": 0.08,
}


@dataclass(frozen=True)
class FighterStatRecord:
    fighter_id: str
    fight_id: str
    event_date: date
    result: str
    method: str | None
    weight_class: str | None
    sig_strikes_landed: float
    sig_strikes_absorbed: float
    takedowns_landed: float
    takedowns_allowed: float
    submission_attempts: float
    submission_attempts_allowed: float
    knockdowns: float
    knockdowns_absorbed: float


@dataclass(frozen=True)
class TrainingExample:
    fight_id: str
    event_date: date
    fighter_a_id: str
    fighter_b_id: str
    label: int
    vector: dict[str, float]


@dataclass(frozen=True)
class WinnerModelPrediction:
    probability_a: float
    raw_probability_a: float
    calibration_method: str | None
    model_version_id: str
    model_source: str
    model_feature_version: str
    model_feature_vector: dict[str, float]
    model_top_factors: list[str]


def build_training_dataset(
    db: Session,
    min_prior_fights: int = 0,
    include_mirrored: bool = True,
) -> list[TrainingExample]:
    """Build winner-model rows using only fighter stats dated before each fight."""

    stats_by_fighter = _load_stats_by_fighter(db)
    examples: list[TrainingExample] = []

    for fight, event in _completed_fights_with_dates(db):
        if not fight.result_winner_id or fight.result_winner_id not in {fight.fighter_a_id, fight.fighter_b_id}:
            continue

        target_date = event.event_date
        a_prior = _prior_records(stats_by_fighter.get(fight.fighter_a_id, []), target_date)
        b_prior = _prior_records(stats_by_fighter.get(fight.fighter_b_id, []), target_date)
        if len(a_prior) < min_prior_fights or len(b_prior) < min_prior_fights:
            continue

        vector = _build_prior_feature_vector(
            a_prior,
            b_prior,
            target_date,
            fight.weight_class,
            fight.fighter_a,
            fight.fighter_b,
        )
        label = 1 if fight.result_winner_id == fight.fighter_a_id else 0
        examples.append(
            TrainingExample(
                fight_id=fight.id,
                event_date=target_date,
                fighter_a_id=fight.fighter_a_id,
                fighter_b_id=fight.fighter_b_id,
                label=label,
                vector=vector,
            )
        )

        if include_mirrored:
            examples.append(
                TrainingExample(
                    fight_id=fight.id,
                    event_date=target_date,
                    fighter_a_id=fight.fighter_b_id,
                    fighter_b_id=fight.fighter_a_id,
                    label=1 - label,
                    vector=_mirror_vector(vector),
                )
            )

    return examples


def train_winner_model(
    db: Session,
    min_examples: int = 20,
    min_prior_fights: int = 0,
    artifact_dir: Path | None = None,
) -> dict[str, Any]:
    examples = build_training_dataset(db, min_prior_fights=min_prior_fights)
    summary = _dataset_summary(examples)
    labels = {example.label for example in examples}

    if len(examples) < min_examples or len(labels) < 2:
        return {
            "status": "failed",
            "message": (
                "Not enough leak-proof historical examples yet. Import more completed events, "
                "then run training again."
            ),
            "result": {
                "algorithm": "logistic_regression",
                "feature_version": FEATURE_VERSION,
                "required_examples": min_examples,
                "min_prior_fights": min_prior_fights,
                "dataset": summary,
            },
        }

    candidate_results = _evaluate_candidate_models(examples)
    selected_algorithm = _select_best_algorithm(candidate_results)
    selected_metrics = candidate_results[selected_algorithm]

    final_model, calibrator, calibration_report = _fit_final_model_with_calibration(examples, selected_algorithm)
    feature_importance = _model_feature_importance(final_model, selected_algorithm)

    artifact_path = _artifact_path(artifact_dir, selected_algorithm)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": final_model,
            "algorithm": selected_algorithm,
            "feature_keys": FEATURE_KEYS,
            "feature_importance": feature_importance,
            "feature_version": FEATURE_VERSION,
            "calibrator": calibrator,
            "calibration": calibration_report,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "min_prior_fights": min_prior_fights,
        },
        artifact_path,
    )

    trained_until = max(example.event_date for example in examples)
    metrics = {
        "validation": selected_metrics["validation"],
        "accuracy": selected_metrics["accuracy"],
        "log_loss": selected_metrics["log_loss"],
        "brier_score": selected_metrics["brier_score"],
        "folds": selected_metrics["folds"],
        "dataset": summary,
        "feature_importance": feature_importance,
        "candidate_results": candidate_results,
        "calibration": calibration_report,
    }
    model_version_id = db_store.save_model_version(
        db,
        name=f"winner-{selected_algorithm.replace('_', '-')}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        algorithm=selected_algorithm,
        artifact_uri=str(artifact_path),
        metrics=metrics,
        trained_until=trained_until,
    )
    active_version = enforce_model_registry_safety(db)

    return {
        "status": "completed",
        "message": (
            f"Winner model trained with {selected_algorithm.replace('_', ' ')} "
            f"on {summary['examples']} leak-proof examples."
        ),
        "result": {
            "algorithm": selected_algorithm,
            "feature_version": FEATURE_VERSION,
            "feature_keys": FEATURE_KEYS,
            "feature_importance": feature_importance,
            "model_version_id": model_version_id,
            "active_model_version_id": active_version.id if active_version else None,
            "artifact_uri": str(artifact_path),
            "trained_until": trained_until.isoformat(),
            "metrics": metrics,
        },
    }


def build_current_winner_model_features(fight: FightRead, as_of: date | None = None) -> dict[str, float]:
    target_date = as_of or datetime.now(timezone.utc).date()
    a = _aggregate_current_fighter(fight.fighter_a, target_date, fight.weight_class)
    b = _aggregate_current_fighter(fight.fighter_b, target_date, fight.weight_class)
    return _diff_vector(a, b)


def predict_fight_with_latest_model(db: Session, fight: FightRead) -> WinnerModelPrediction | None:
    model_versions = db.scalars(
        select(models.ModelVersion)
        .where(models.ModelVersion.algorithm.in_(SUPPORTED_WINNER_ALGORITHMS))
        .order_by(models.ModelVersion.created_at.desc())
    ).all()
    model_version = _select_active_model_version(model_versions)
    if model_version is None or not model_version.artifact_uri:
        return None

    artifact_path = Path(model_version.artifact_uri)
    if not artifact_path.exists():
        return None

    try:
        artifact = joblib.load(artifact_path)
        model = artifact["model"]
        feature_keys = artifact.get("feature_keys") or artifact.get("feature_names") or FEATURE_KEYS
        feature_importance = artifact.get("feature_importance") or {}
        feature_version = artifact.get("feature_version") or FEATURE_VERSION
        calibrator = artifact.get("calibrator")
        calibration = artifact.get("calibration") or {}
        if feature_version == "v6":
            from app.services.feature_builder import build_feature_vector

            orm_fight = db.scalar(
                select(models.Fight)
                .where(models.Fight.id == fight.id)
                .options(joinedload(models.Fight.fighter_a), joinedload(models.Fight.fighter_b))
            )
            if orm_fight is None:
                return None
            vector = build_feature_vector(orm_fight.fighter_a, orm_fight.fighter_b, orm_fight, db)
        else:
            vector = build_current_winner_model_features(fight)
        matrix = np.array([[vector.get(key, 0.0) for key in feature_keys]], dtype=float)
        raw_probability = float(model.predict_proba(matrix)[0, 1])
        probability = _apply_calibrator(raw_probability, calibrator)
        top_factors = _top_model_factor_labels(vector, feature_importance)
    except Exception:
        return None

    return WinnerModelPrediction(
        probability_a=probability,
        raw_probability_a=raw_probability,
        calibration_method=calibration.get("method"),
        model_version_id=model_version.id,
        model_source=f"winner_{model_version.algorithm}",
        model_feature_version=feature_version,
        model_feature_vector=vector,
        model_top_factors=top_factors,
    )


def enforce_model_registry_safety(db: Session) -> models.ModelVersion | None:
    """Backfill model registry metadata and mark exactly one safe active model."""
    model_versions = db.scalars(
        select(models.ModelVersion)
        .where(models.ModelVersion.algorithm.in_(SUPPORTED_WINNER_ALGORITHMS))
        .order_by(models.ModelVersion.created_at.desc())
    ).all()

    for model_version in model_versions:
        source_fights = _model_source_fights(model_version)
        accuracy = _model_accuracy(model_version)
        validation_type = _model_validation_type(model_version)

        model_version.source_fights = source_fights
        model_version.accuracy = accuracy
        model_version.validation_type = validation_type

        if _is_resubstitution_validation(validation_type):
            if not _model_is_deprecated(model_version):
                log.error(
                    "Blocking resubstitution model from active registry: id=%s algorithm=%s source_fights=%s accuracy=%s validation=%s",
                    model_version.id,
                    model_version.algorithm,
                    source_fights,
                    accuracy,
                    validation_type,
                )
            model_version.is_deprecated = True
        if _model_failed_calibration(model_version):
            log.error(
                "Blocking failed-calibration model from active registry: id=%s algorithm=%s source_fights=%s accuracy=%s validation=%s",
                model_version.id,
                model_version.algorithm,
                source_fights,
                accuracy,
                validation_type,
            )
            model_version.is_deprecated = True

    selected = _select_active_model_version(model_versions)
    for model_version in model_versions:
        model_version.is_active = bool(selected and model_version.id == selected.id)

    db.commit()
    if selected:
        log.info(
            "Active winner model selected: id=%s algorithm=%s source_fights=%s accuracy=%s validation=%s",
            selected.id,
            selected.algorithm,
            _model_source_fights(selected),
            _model_accuracy(selected),
            _model_validation_type(selected),
        )
    else:
        log.warning("No usable winner model is active after registry safety enforcement")
    return selected


def _select_active_model_version(model_versions: list[models.ModelVersion]) -> models.ModelVersion | None:
    available = [
        model_version
        for model_version in model_versions
        if model_version.artifact_uri and Path(model_version.artifact_uri).exists()
    ]
    if not available:
        return None

    selectable: list[models.ModelVersion] = []
    for model_version in available:
        validation_type = _model_validation_type(model_version)
        if _model_is_deprecated(model_version):
            continue
        if _is_resubstitution_validation(validation_type):
            log.error(
                "Blocked unsafe resubstitution model from selection: id=%s algorithm=%s source_fights=%s accuracy=%s validation=%s",
                model_version.id,
                model_version.algorithm,
                _model_source_fights(model_version),
                _model_accuracy(model_version),
                validation_type,
            )
            continue
        selectable.append(model_version)

    valid = [
        model_version
        for model_version in selectable
        if _model_validation_type(model_version) == SAFE_VALIDATION_TYPE
        and (_model_source_fights(model_version) or 0) >= MIN_SAFE_SOURCE_FIGHTS
    ]
    if valid:
        return max(valid, key=_safe_model_sort_key)

    if not selectable:
        return None

    log.warning(
        "No properly validated winner model exists; falling back to largest non-deprecated non-resubstitution model"
    )
    return max(selectable, key=_safe_model_sort_key)


def _safe_model_sort_key(model_version: models.ModelVersion) -> tuple[int, float, float]:
    created_at = model_version.created_at
    timestamp = created_at.timestamp() if hasattr(created_at, "timestamp") else 0.0
    return (
        _model_source_fights(model_version) or 0,
        _model_accuracy(model_version) or 0.0,
        timestamp,
    )


def _model_source_fights(model_version: models.ModelVersion) -> int | None:
    value = getattr(model_version, "source_fights", None)
    if value is None:
        value = ((model_version.metrics or {}).get("dataset") or {}).get("source_fights")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _model_accuracy(model_version: models.ModelVersion) -> float | None:
    value = getattr(model_version, "accuracy", None)
    if value is None:
        value = (model_version.metrics or {}).get("accuracy")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _model_validation_type(model_version: models.ModelVersion) -> str | None:
    value = getattr(model_version, "validation_type", None)
    return value or (model_version.metrics or {}).get("validation")


def _model_is_deprecated(model_version: models.ModelVersion) -> bool:
    return bool(getattr(model_version, "is_deprecated", False))


def _model_failed_calibration(model_version: models.ModelVersion) -> bool:
    metrics = model_version.metrics or {}
    deployment_status = str(metrics.get("deployment_status") or "").lower()
    calibration_status = str((metrics.get("test_calibration") or {}).get("status") or "").upper()
    return "rolled_back" in deployment_status or calibration_status == "NEEDS_IMPROVEMENT"


def _is_resubstitution_validation(validation_type: str | None) -> bool:
    return "resubstitution" in (validation_type or "").lower()


def _metric_value(metrics: dict[str, Any] | None, key: str) -> float:
    if not metrics:
        return float("inf")
    value = metrics.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _completed_fights_with_dates(db: Session):
    rows = db.execute(
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.status == "completed")
        .where(models.Fight.result_winner_id.isnot(None))
        .where(models.Event.event_date.isnot(None))
        .order_by(models.Event.event_date, models.Fight.bout_order)
    ).all()
    return rows


def _load_stats_by_fighter(db: Session) -> dict[str, list[FighterStatRecord]]:
    rows = db.execute(
        select(models.FighterFightStats, models.Fight, models.Event)
        .join(models.Fight, models.FighterFightStats.fight_id == models.Fight.id)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.status == "completed")
        .where(models.Event.event_date.isnot(None))
    ).all()

    stat_rows_by_fight_fighter = {
        (stat_row.fight_id, stat_row.fighter_id): stat_row.stats or {}
        for stat_row, _fight, _event in rows
    }
    records: dict[str, list[FighterStatRecord]] = {}
    for stat_row, fight, event in rows:
        stats = stat_row.stats or {}
        opponent_stats = stat_rows_by_fight_fighter.get((stat_row.fight_id, stat_row.opponent_id), {})
        result = stats.get("result")
        if result is None and fight.result_winner_id:
            result = "win" if fight.result_winner_id == stat_row.fighter_id else "loss"

        sig_strikes_landed = _number(stats.get("sig_strikes_landed"))
        sig_strikes_absorbed = _first_number(
            stats,
            ["sig_strikes_absorbed", "sig_strikes_against"],
            fallback=_number(opponent_stats.get("sig_strikes_landed")),
        )
        takedowns_landed = _number(stats.get("takedowns_landed"))
        takedowns_allowed = _first_number(
            stats,
            ["takedowns_allowed", "takedowns_against"],
            fallback=_number(opponent_stats.get("takedowns_landed")),
        )
        submission_attempts = _number(stats.get("submission_attempts"))
        submission_attempts_allowed = _first_number(
            stats,
            ["submission_attempts_allowed", "submission_attempts_against"],
            fallback=_number(opponent_stats.get("submission_attempts")),
        )
        knockdowns = _number(stats.get("knockdowns"))
        knockdowns_absorbed = _first_number(
            stats,
            ["knockdowns_absorbed", "knockdowns_against"],
            fallback=_number(opponent_stats.get("knockdowns")),
        )

        record = FighterStatRecord(
            fighter_id=stat_row.fighter_id,
            fight_id=stat_row.fight_id,
            event_date=event.event_date,
            result=str(result or "").lower(),
            method=stats.get("method") or fight.result_method,
            weight_class=stats.get("weight_class") or fight.weight_class,
            sig_strikes_landed=sig_strikes_landed,
            sig_strikes_absorbed=sig_strikes_absorbed,
            takedowns_landed=takedowns_landed,
            takedowns_allowed=takedowns_allowed,
            submission_attempts=submission_attempts,
            submission_attempts_allowed=submission_attempts_allowed,
            knockdowns=knockdowns,
            knockdowns_absorbed=knockdowns_absorbed,
        )
        records.setdefault(stat_row.fighter_id, []).append(record)

    for fighter_records in records.values():
        fighter_records.sort(key=lambda item: item.event_date)
    return records


def _prior_records(records: list[FighterStatRecord], as_of: date) -> list[FighterStatRecord]:
    return [record for record in records if record.event_date < as_of]


def _build_prior_feature_vector(
    a_prior: list[FighterStatRecord],
    b_prior: list[FighterStatRecord],
    target_date: date,
    weight_class: str | None,
    fighter_a: models.Fighter | None = None,
    fighter_b: models.Fighter | None = None,
) -> dict[str, float]:
    a = _aggregate(a_prior, target_date, weight_class, fighter_a)
    b = _aggregate(b_prior, target_date, weight_class, fighter_b)
    return _diff_vector(a, b)


_SYMMETRIC_FEATURES = frozenset({"weight_class_kg"})


def _mirror_vector(vector: dict[str, float]) -> dict[str, float]:
    """Negate directional features; keep symmetric fight-level scalars unchanged."""
    return {
        key: (value if key in _SYMMETRIC_FEATURES else -value)
        for key, value in vector.items()
    }


def _diff_vector(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    vector = {
        key: a[key.removesuffix("_diff")] - b[key.removesuffix("_diff")]
        for key in FEATURE_KEYS
        if key.endswith("_diff") and key.removesuffix("_diff") in a and key.removesuffix("_diff") in b
    }
    vector["grappling_control_pressure_diff"] = (
        a["takedowns_landed_avg"] * (1 + b["takedowns_allowed_avg"])
        - b["takedowns_landed_avg"] * (1 + a["takedowns_allowed_avg"])
    )
    vector["submission_grappling_pressure_diff"] = (
        a["submission_attempts_avg"] * (1 + b["submission_attempts_allowed_avg"])
        - b["submission_attempts_avg"] * (1 + a["submission_attempts_allowed_avg"])
    )
    vector["finish_threat_durability_diff"] = (
        a["finish_win_rate"] * (0.35 + b["finish_loss_rate"])
        - b["finish_win_rate"] * (0.35 + a["finish_loss_rate"])
    )
    # weight_class_kg is a fight-level scalar (same for both fighters), taken from fighter a
    vector["weight_class_kg"] = a.get("weight_class_kg", 70.3)
    return {key: float(vector.get(key, 0.0)) for key in FEATURE_KEYS}


def _aggregate(
    records: list[FighterStatRecord],
    target_date: date,
    weight_class: str | None,
    fighter: models.Fighter | None = None,
) -> dict[str, float]:
    recent = sorted(records, key=lambda item: item.event_date, reverse=True)[:5]
    last_fight_date = max((record.event_date for record in records), default=None)
    days_since_last_fight = (
        float((target_date - last_fight_date).days)
        if last_fight_date
        else float(DEFAULT_DEBUTANT_DAYS_SINCE_LAST_FIGHT)
    )
    decayed_win_rate = _time_decayed_rate(records, target_date, lambda item: item.result == "win", default=0.5)
    decayed_striking_diff = _time_decayed_average(
        records, target_date, lambda item: item.sig_strikes_landed - item.sig_strikes_absorbed
    )
    median = _division_median_prior(weight_class)
    age = _age_on(fighter.date_of_birth, target_date) if fighter and fighter.date_of_birth else None
    height_cm = float(fighter.height_cm or median["height_cm"]) if fighter else median["height_cm"]
    reach_cm = float(fighter.reach_cm or median["reach_cm"]) if fighter else median["reach_cm"]
    sig_landed = [record.sig_strikes_landed for record in records] or [median["sig_strikes_landed"]]
    sig_absorbed = [record.sig_strikes_absorbed for record in records] or [median["sig_strikes_absorbed"]]
    recent_sig_landed = [record.sig_strikes_landed for record in recent] or sig_landed
    recent_sig_absorbed = [record.sig_strikes_absorbed for record in recent] or sig_absorbed
    takedowns_landed = [record.takedowns_landed for record in records] or [median["takedowns_landed"]]
    takedowns_allowed = [record.takedowns_allowed for record in records] or [median["takedowns_allowed"]]
    submission_attempts = [record.submission_attempts for record in records] or [median["submission_attempts"]]
    submission_attempts_allowed = (
        [record.submission_attempts_allowed for record in records] or [median["submission_attempts_allowed"]]
    )
    knockdowns = [record.knockdowns for record in records] or [median["knockdowns"]]
    knockdowns_absorbed = [record.knockdowns_absorbed for record in records] or [median["knockdowns_absorbed"]]
    recent_knockdowns_absorbed = [record.knockdowns_absorbed for record in recent] or knockdowns_absorbed
    return {
        "height_cm": height_cm,
        "reach_cm": reach_cm,
        "reach_to_height_ratio": reach_cm / height_cm if height_cm else 1.0,
        "ufc_experience": float(len(records)),
        "is_debutant": float(len(records) == 0),
        "career_win_rate": _rate(records, lambda item: item.result == "win", default=0.5),
        "recent_win_rate": _rate(recent, lambda item: item.result == "win", default=0.5),
        "decayed_win_rate": decayed_win_rate,
        "sig_strikes_landed_avg": _winsorize(_average(sig_landed)),
        "sig_strikes_absorbed_avg": _winsorize(_average(sig_absorbed)),
        "recent_sig_strikes_landed_avg": _winsorize(_average(recent_sig_landed)),
        "recent_sig_strikes_absorbed_avg": _winsorize(_average(recent_sig_absorbed)),
        "striking_differential_avg": _winsorize(_average(
            [landed - absorbed for landed, absorbed in zip(sig_landed, sig_absorbed, strict=False)]
        )),
        "recent_striking_differential_avg": _winsorize(_average(
            [landed - absorbed for landed, absorbed in zip(recent_sig_landed, recent_sig_absorbed, strict=False)]
        )),
        "decayed_striking_differential": decayed_striking_diff,
        "takedowns_landed_avg": _average(takedowns_landed),
        "takedowns_allowed_avg": _average(takedowns_allowed),
        "takedown_differential_avg": _average(
            [landed - allowed for landed, allowed in zip(takedowns_landed, takedowns_allowed, strict=False)]
        ),
        "submission_attempts_avg": _average(submission_attempts),
        "submission_attempts_allowed_avg": _average(submission_attempts_allowed),
        "knockdowns_avg": _average(knockdowns),
        "knockdowns_absorbed_avg": _average(knockdowns_absorbed),
        "recent_knockdowns_absorbed_avg": _average(recent_knockdowns_absorbed),
        "recent_ko_loss_flag": float(any(record.result == "loss" and _is_ko(record.method) for record in recent[:2])),
        "finish_win_rate": _rate(
            records,
            lambda item: item.result == "win" and _is_finish(item.method),
            default=median["finish_win_rate"],
        ),
        "finish_loss_rate": _rate(
            records,
            lambda item: item.result == "loss" and _is_finish(item.method),
            default=median["finish_loss_rate"],
        ),
        "ko_loss_rate": _rate(
            records,
            lambda item: item.result == "loss" and _is_ko(item.method),
            default=median["ko_loss_rate"],
        ),
        "submission_loss_rate": _rate(
            records,
            lambda item: item.result == "loss" and _is_submission(item.method),
            default=median["submission_loss_rate"],
        ),
        "win_streak": float(_current_streak(records, "win")),
        "loss_streak": float(_current_streak(records, "loss")),
        "age_vs_peak": float((age or 29) - 29) if age else 0.0,
        "days_since_last_fight": days_since_last_fight,
        "log_ring_rust": log1p(days_since_last_fight),
        "long_layoff": float(days_since_last_fight >= 365),
        "short_turnaround": float(0 < days_since_last_fight <= 70),
        "same_weight_class_experience": float(
            sum(1 for record in records if weight_class and record.weight_class == weight_class)
        ),
        "weight_class_kg": _weight_class_to_kg(weight_class),
    }


def _aggregate_current_fighter(
    fighter: FighterRead,
    target_date: date,
    weight_class: str | None,
) -> dict[str, float]:
    records = fighter.recent_fights
    recent = records[:5]
    last_fight_date = _most_recent_history_date(records)
    days_since_last_fight = (
        float((target_date - last_fight_date).days)
        if last_fight_date
        else float(DEFAULT_DEBUTANT_DAYS_SINCE_LAST_FIGHT)
    )
    age = fighter.age
    median = _division_median_prior(weight_class)
    height_cm = float(fighter.height_cm or median["height_cm"])
    reach_cm = float(fighter.reach_cm or median["reach_cm"])
    sig_landed = [float(item.sig_strikes_for or 0) for item in records] or [
        _profile_stat(fighter, "strikes_landed_per_min", median["sig_strikes_landed"])
    ]
    sig_absorbed = [float(item.sig_strikes_against or 0) for item in records] or [
        _profile_stat(fighter, "strikes_absorbed_per_min", median["sig_strikes_absorbed"])
    ]
    recent_sig_landed = [float(item.sig_strikes_for or 0) for item in recent] or sig_landed
    recent_sig_absorbed = [float(item.sig_strikes_against or 0) for item in recent] or sig_absorbed
    takedowns_landed = [float(item.takedowns_for or 0) for item in records] or [
        _profile_stat(fighter, "td_avg_per_15", median["takedowns_landed"])
    ]
    takedowns_allowed = [float(item.takedowns_against or 0) for item in records] or [median["takedowns_allowed"]]
    submission_attempts = [float(item.sub_attempts_for or 0) for item in records] or [
        _profile_stat(fighter, "sub_avg_per_15", median["submission_attempts"])
    ]
    submission_attempts_allowed = [float(item.sub_attempts_against or 0) for item in records] or [
        median["submission_attempts_allowed"]
    ]
    knockdowns = [float(item.knockdowns_for or 0) for item in records] or [median["knockdowns"]]
    knockdowns_absorbed = [float(item.knockdowns_against or 0) for item in records] or [
        median["knockdowns_absorbed"]
    ]
    recent_knockdowns_absorbed = [float(item.knockdowns_against or 0) for item in recent] or knockdowns_absorbed

    decayed_win_rate = _time_decayed_history_rate(
        records, target_date, lambda item: item.result.lower().startswith("win"), default=0.5
    )
    decayed_striking_diff = _time_decayed_history_average(
        records, target_date, lambda item: float(item.sig_strikes_for or 0) - float(item.sig_strikes_against or 0)
    )

    return {
        "height_cm": height_cm,
        "reach_cm": reach_cm,
        "reach_to_height_ratio": reach_cm / height_cm if height_cm else 1.0,
        "ufc_experience": _current_experience_count(fighter),
        "is_debutant": float(_current_experience_count(fighter) <= 0),
        "career_win_rate": _history_rate(records, lambda item: item.result.lower().startswith("win"), default=0.5),
        "recent_win_rate": _history_rate(recent, lambda item: item.result.lower().startswith("win"), default=0.5),
        "decayed_win_rate": decayed_win_rate,
        "sig_strikes_landed_avg": _winsorize(_average(sig_landed)),
        "sig_strikes_absorbed_avg": _winsorize(_average(sig_absorbed)),
        "recent_sig_strikes_landed_avg": _winsorize(_average(recent_sig_landed)),
        "recent_sig_strikes_absorbed_avg": _winsorize(_average(recent_sig_absorbed)),
        "striking_differential_avg": _winsorize(_average(
            [landed - absorbed for landed, absorbed in zip(sig_landed, sig_absorbed, strict=False)]
        )),
        "recent_striking_differential_avg": _winsorize(_average(
            [landed - absorbed for landed, absorbed in zip(recent_sig_landed, recent_sig_absorbed, strict=False)]
        )),
        "decayed_striking_differential": decayed_striking_diff,
        "takedowns_landed_avg": _average(takedowns_landed),
        "takedowns_allowed_avg": _average(takedowns_allowed),
        "takedown_differential_avg": _average(
            [landed - allowed for landed, allowed in zip(takedowns_landed, takedowns_allowed, strict=False)]
        ),
        "submission_attempts_avg": _average(submission_attempts),
        "submission_attempts_allowed_avg": _average(submission_attempts_allowed),
        "knockdowns_avg": _average(knockdowns),
        "knockdowns_absorbed_avg": _average(knockdowns_absorbed),
        "recent_knockdowns_absorbed_avg": _average(recent_knockdowns_absorbed),
        "recent_ko_loss_flag": float(_current_recent_ko_loss_flag(recent)),
        "finish_win_rate": _history_rate(
            records,
            lambda item: item.result.lower().startswith("win") and _is_finish(item.method),
            default=_profile_stat(fighter, "finish_rate", median["finish_win_rate"]),
        ),
        "finish_loss_rate": _history_rate(
            records,
            lambda item: item.result.lower().startswith("loss") and _is_finish(item.method),
            default=median["finish_loss_rate"],
        ),
        "ko_loss_rate": _history_rate(
            records,
            lambda item: item.result.lower().startswith("loss") and _is_ko(item.method),
            default=median["ko_loss_rate"],
        ),
        "submission_loss_rate": _history_rate(
            records,
            lambda item: item.result.lower().startswith("loss") and _is_submission(item.method),
            default=median["submission_loss_rate"],
        ),
        "win_streak": float(_current_history_streak(records, win=True)),
        "loss_streak": float(_current_history_streak(records, win=False)),
        "age_vs_peak": float((age or 29) - 29) if age else 0.0,
        "days_since_last_fight": days_since_last_fight,
        "log_ring_rust": log1p(days_since_last_fight),
        "long_layoff": float(days_since_last_fight >= 365),
        "short_turnaround": float(0 < days_since_last_fight <= 70),
        "same_weight_class_experience": _same_weight_class_estimate(fighter, weight_class),
        "weight_class_kg": _weight_class_to_kg(weight_class),
    }


def _matrix_and_labels(examples: list[TrainingExample]) -> tuple[np.ndarray, np.ndarray]:
    x = np.array([[example.vector[key] for key in FEATURE_KEYS] for example in examples], dtype=float)
    y = np.array([example.label for example in examples], dtype=int)
    return x, y


def _year_holdout_split(examples: list[TrainingExample]) -> tuple[list[TrainingExample], list[TrainingExample], str]:
    years = sorted({example.event_date.year for example in examples})
    if len(years) < 2:
        return examples, [], "resubstitution_single_year"

    holdout_year = years[-1]
    train_examples = [example for example in examples if example.event_date.year < holdout_year]
    test_examples = [example for example in examples if example.event_date.year == holdout_year]
    return train_examples, test_examples, f"train_before_{holdout_year}_test_{holdout_year}"


def _has_two_classes(examples: list[TrainingExample]) -> bool:
    return len({example.label for example in examples}) == 2


def _evaluate_candidate_models(examples: list[TrainingExample]) -> dict[str, dict[str, Any]]:
    x_all, y_all = _matrix_and_labels(examples)
    results: dict[str, dict[str, Any]] = {}
    for algorithm in SUPPORTED_WINNER_ALGORITHMS:
        yearly_scores = _rolling_year_validation(examples, algorithm)
        if yearly_scores:
            results[algorithm] = _aggregate_yearly_scores(yearly_scores)
            continue

        model = _new_model(algorithm)
        model.fit(x_all, y_all)
        metrics = _evaluate_model(model, x_all, y_all)
        results[algorithm] = {
            **metrics,
            "validation": "resubstitution_insufficient_year_split",
            "folds": 0,
            "yearly": [],
        }
    return results


def _rolling_year_validation(examples: list[TrainingExample], algorithm: str) -> list[dict[str, Any]]:
    years = sorted({example.event_date.year for example in examples})
    scores: list[dict[str, Any]] = []
    for holdout_year in years[1:]:
        holdout_start = date(holdout_year, 1, 1)
        train_cutoff = date.fromordinal(holdout_start.toordinal() - PURGE_GAP_DAYS)
        train_examples = [example for example in examples if example.event_date <= train_cutoff]
        test_examples = [example for example in examples if example.event_date.year == holdout_year]
        if (
            len(train_examples) < MIN_ROLLING_TRAIN_EXAMPLES
            or len(test_examples) < MIN_ROLLING_TEST_EXAMPLES
            or not _has_two_classes(train_examples)
        ):
            continue

        model = _new_model(algorithm)
        x_train, y_train = _matrix_and_labels(train_examples)
        x_test, y_test = _matrix_and_labels(test_examples)
        model.fit(x_train, y_train)
        scores.append(
            {
                **_evaluate_model(model, x_test, y_test),
                "holdout_year": holdout_year,
                "train_examples": len(train_examples),
                "test_examples": len(test_examples),
                "purge_gap_days": PURGE_GAP_DAYS,
            }
        )
    return scores


def _aggregate_yearly_scores(scores: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "accuracy": round(float(np.mean([score["accuracy"] for score in scores])), 4),
        "log_loss": round(float(np.mean([score["log_loss"] for score in scores])), 4),
        "brier_score": round(float(np.mean([score["brier_score"] for score in scores])), 4),
        "validation": "rolling_year_holdout",
        "folds": len(scores),
        "yearly": scores,
    }


def _select_best_algorithm(candidate_results: dict[str, dict[str, Any]]) -> str:
    return min(
        candidate_results,
        key=lambda algorithm: (
            candidate_results[algorithm]["log_loss"],
            candidate_results[algorithm]["brier_score"],
            -candidate_results[algorithm]["accuracy"],
        ),
    )


def _fit_final_model_with_calibration(
    examples: list[TrainingExample],
    algorithm: str,
) -> tuple[Pipeline, LogisticRegression | None, dict[str, Any]]:
    train_examples, calibration_examples, split_name = _calibration_split(examples)
    model = _new_model(algorithm)

    if not calibration_examples:
        x_all, y_all = _matrix_and_labels(examples)
        model.fit(x_all, y_all)
        return model, None, {
            "method": None,
            "status": "skipped",
            "reason": split_name,
            "examples": 0,
        }

    x_train, y_train = _matrix_and_labels(train_examples)
    x_calibration, y_calibration = _matrix_and_labels(calibration_examples)
    model.fit(x_train, y_train)

    raw_probabilities = model.predict_proba(x_calibration)[:, 1].reshape(-1, 1)
    calibrator = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)
    calibrator.fit(raw_probabilities, y_calibration)
    calibrated_probabilities = calibrator.predict_proba(raw_probabilities)[:, 1]

    return model, calibrator, {
        "method": "platt_logistic_validation_year",
        "status": "completed",
        "split": split_name,
        "train_examples": len(train_examples),
        "calibration_examples": len(calibration_examples),
        "raw_brier_score": round(float(brier_score_loss(y_calibration, raw_probabilities.ravel())), 4),
        "calibrated_brier_score": round(float(brier_score_loss(y_calibration, calibrated_probabilities)), 4),
        "raw_log_loss": round(float(log_loss(y_calibration, raw_probabilities.ravel(), labels=[0, 1])), 4),
        "calibrated_log_loss": round(float(log_loss(y_calibration, calibrated_probabilities, labels=[0, 1])), 4),
    }


def _calibration_split(examples: list[TrainingExample]) -> tuple[list[TrainingExample], list[TrainingExample], str]:
    years = sorted({example.event_date.year for example in examples})
    if len(years) < 2:
        return examples, [], "single_year_dataset"

    calibration_year = years[-1]
    calibration_start = date(calibration_year, 1, 1)
    train_cutoff = date.fromordinal(calibration_start.toordinal() - PURGE_GAP_DAYS)
    train_examples = [example for example in examples if example.event_date <= train_cutoff]
    calibration_examples = [example for example in examples if example.event_date.year == calibration_year]
    if (
        len(train_examples) < MIN_ROLLING_TRAIN_EXAMPLES
        or len(calibration_examples) < MIN_ROLLING_TEST_EXAMPLES
        or not _has_two_classes(train_examples)
        or not _has_two_classes(calibration_examples)
    ):
        return examples, [], "insufficient_validation_year_for_calibration"
    return train_examples, calibration_examples, f"train_before_{calibration_year}_calibrate_{calibration_year}"


def _apply_calibrator(raw_probability: float, calibrator: LogisticRegression | None) -> float:
    if calibrator is None:
        return raw_probability
    return float(calibrator.predict_proba(np.array([[raw_probability]], dtype=float))[0, 1])


def _new_model(algorithm: str) -> Pipeline:
    if algorithm == "random_forest":
        return Pipeline(
            steps=[
                (
                    "classifier",
                    RandomForestClassifier(
                        n_estimators=400,
                        min_samples_leaf=4,
                        max_features="sqrt",
                        class_weight="balanced",
                        random_state=42,
                    ),
                ),
            ]
        )
    if algorithm == "gradient_boosting":
        return Pipeline(
            steps=[
                (
                    "classifier",
                    GradientBoostingClassifier(
                        n_estimators=250,
                        learning_rate=0.03,
                        max_depth=3,
                        min_samples_leaf=6,
                        subsample=0.80,
                        max_features="sqrt",
                        random_state=42,
                    ),
                ),
            ]
        )
    if algorithm == "extra_trees":
        return Pipeline(
            steps=[
                (
                    "classifier",
                    ExtraTreesClassifier(
                        n_estimators=600,
                        min_samples_leaf=5,
                        max_features="sqrt",
                        class_weight="balanced",
                        random_state=42,
                    ),
                ),
            ]
        )
    return Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)),
        ]
    )


def _model_feature_importance(model: Pipeline, algorithm: str) -> dict[str, float]:
    classifier = model.named_steps["classifier"]
    if algorithm in {"random_forest", "gradient_boosting", "extra_trees"} and hasattr(classifier, "feature_importances_"):
        raw = classifier.feature_importances_
    elif hasattr(classifier, "coef_"):
        raw = np.abs(classifier.coef_[0])
    else:
        raw = np.zeros(len(FEATURE_KEYS))

    total = float(np.sum(raw))
    if total <= 0:
        return {key: 0.0 for key in FEATURE_KEYS}
    return {
        key: round(float(value / total), 5)
        for key, value in zip(FEATURE_KEYS, raw, strict=False)
    }


def _top_model_factor_labels(
    vector: dict[str, float],
    feature_importance: dict[str, float],
    limit: int = 3,
) -> list[str]:
    ranked = sorted(
        (
            (abs(vector.get(key, 0.0)) * float(weight or 0.0), _feature_label(key))
            for key, weight in feature_importance.items()
        ),
        reverse=True,
    )
    return [label for score, label in ranked if score > 0][:limit]


def _feature_label(key: str) -> str:
    labels = {
        "height_cm_diff": "learned edge: height advantage",
        "reach_cm_diff": "learned edge: reach advantage",
        "reach_to_height_ratio_diff": "learned edge: reach-to-height leverage",
        "ufc_experience_diff": "learned edge: UFC experience gap",
        "is_debutant_diff": "learned edge: debutant risk",
        "career_win_rate_diff": "learned edge: career win rate",
        "recent_win_rate_diff": "learned edge: recent form",
        "decayed_win_rate_diff": "learned edge: time-weighted win rate",
        "sig_strikes_landed_avg_diff": "learned edge: historical striking output",
        "sig_strikes_absorbed_avg_diff": "learned edge: historical damage absorbed",
        "recent_sig_strikes_landed_avg_diff": "learned edge: recent striking output",
        "recent_sig_strikes_absorbed_avg_diff": "learned edge: recent damage absorbed",
        "striking_differential_avg_diff": "learned edge: striking differential",
        "recent_striking_differential_avg_diff": "learned edge: recent striking differential",
        "decayed_striking_differential_diff": "learned edge: time-weighted striking edge",
        "takedowns_landed_avg_diff": "learned edge: takedown output",
        "takedowns_allowed_avg_diff": "learned edge: takedowns allowed",
        "takedown_differential_avg_diff": "learned edge: takedown differential",
        "submission_attempts_avg_diff": "learned edge: submission activity",
        "submission_attempts_allowed_avg_diff": "learned edge: submission attempts allowed",
        "knockdowns_avg_diff": "learned edge: knockdown threat",
        "knockdowns_absorbed_avg_diff": "learned edge: knockdowns absorbed",
        "recent_knockdowns_absorbed_avg_diff": "learned edge: recent knockdown durability",
        "recent_ko_loss_flag_diff": "learned edge: recent KO-loss flag",
        "grappling_control_pressure_diff": "learned edge: wrestling control pressure",
        "submission_grappling_pressure_diff": "learned edge: submission pressure",
        "finish_threat_durability_diff": "learned edge: finish threat vs durability",
        "finish_win_rate_diff": "learned edge: finishing rate",
        "finish_loss_rate_diff": "learned edge: finish-loss durability",
        "ko_loss_rate_diff": "learned edge: KO-loss durability",
        "submission_loss_rate_diff": "learned edge: submission-loss durability",
        "win_streak_diff": "learned edge: current win momentum",
        "loss_streak_diff": "learned edge: current losing skid",
        "age_vs_peak_diff": "learned edge: age vs prime window",
        "days_since_last_fight_diff": "learned edge: activity rhythm",
        "log_ring_rust_diff": "learned edge: ring-rust gap",
        "long_layoff_diff": "learned edge: long layoff risk",
        "short_turnaround_diff": "learned edge: short turnaround",
        "same_weight_class_experience_diff": "learned edge: weight-class experience",
        "weight_class_kg": "learned edge: weight class",
    }
    return labels.get(key, f"learned edge: {key.replace('_', ' ')}")


def _evaluate_model(model: Pipeline, x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    probabilities = model.predict_proba(x)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    return {
        "accuracy": round(float(accuracy_score(y, predictions)), 4),
        "log_loss": round(float(log_loss(y, probabilities, labels=[0, 1])), 4),
        "brier_score": round(float(brier_score_loss(y, probabilities)), 4),
    }


def _dataset_summary(examples: list[TrainingExample]) -> dict[str, Any]:
    dates = [example.event_date for example in examples]
    return {
        "examples": len(examples),
        "source_fights": len({example.fight_id for example in examples}),
        "positive_labels": sum(1 for example in examples if example.label == 1),
        "negative_labels": sum(1 for example in examples if example.label == 0),
        "start_date": min(dates).isoformat() if dates else None,
        "end_date": max(dates).isoformat() if dates else None,
    }


def _artifact_path(artifact_dir: Path | None = None, algorithm: str = "logistic_regression") -> Path:
    root = artifact_dir or Path(__file__).resolve().parents[3] / "ml" / "artifacts"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return root / f"winner_{algorithm}_{stamp}.joblib"


def _current_experience_count(fighter: FighterRead) -> float:
    raw_count = _number(fighter.stats.get("raw_fight_count"))
    if raw_count:
        return raw_count
    return float(len(fighter.recent_fights))


def _profile_stat(fighter: FighterRead, key: str, default: float) -> float:
    value = fighter.stats.get(key)
    if value in {None, "", "--"}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _division_median_prior(weight_class: str | None) -> dict[str, float]:
    prior = dict(DIVISION_MEDIAN_PRIOR)
    normalized = (weight_class or "").lower()
    if "heavyweight" in normalized:
        prior.update({"height_cm": 190.0, "reach_cm": 196.0, "finish_win_rate": 0.58, "ko_loss_rate": 0.18})
    elif "flyweight" in normalized or "bantamweight" in normalized:
        prior.update({"height_cm": 168.0, "reach_cm": 170.0, "finish_win_rate": 0.38, "ko_loss_rate": 0.08})
    elif "strawweight" in normalized:
        prior.update({"height_cm": 163.0, "reach_cm": 164.0, "finish_win_rate": 0.30, "ko_loss_rate": 0.06})
    elif "lightweight" in normalized or "welterweight" in normalized:
        prior.update({"height_cm": 178.0, "reach_cm": 182.0, "finish_win_rate": 0.46, "ko_loss_rate": 0.12})
    return prior


def _age_on(birthdate: date, target_date: date) -> int:
    years = target_date.year - birthdate.year
    if (target_date.month, target_date.day) < (birthdate.month, birthdate.day):
        years -= 1
    return years


def _current_recent_ko_loss_flag(records: list[FightHistoryItem]) -> bool:
    return any(record.result.lower().startswith("loss") and _is_ko(record.method) for record in records[:2])


def _same_weight_class_estimate(fighter: FighterRead, weight_class: str | None) -> float:
    if not weight_class:
        return 0.0
    raw = _number(fighter.stats.get("raw_fight_count"))
    if raw:
        return raw
    return float(len(fighter.recent_fights))


def _winsorize(value: float, low: float = -50.0, high: float = 50.0) -> float:
    return max(low, min(high, value))


def _time_decay_weight(event_date: date, target_date: date, half_life_days: float = 730.0) -> float:
    days_ago = max(0, (target_date - event_date).days)
    return 2.0 ** (-days_ago / half_life_days)


def _time_decayed_rate(
    records: list[FighterStatRecord],
    target_date: date,
    predicate,
    default: float = 0.0,
) -> float:
    if not records:
        return default
    weights = [_time_decay_weight(record.event_date, target_date) for record in records]
    hits = [_time_decay_weight(record.event_date, target_date) for record in records if predicate(record)]
    total_weight = sum(weights)
    if total_weight <= 0:
        return default
    return sum(hits) / total_weight


def _time_decayed_average(
    records: list[FighterStatRecord],
    target_date: date,
    value_fn,
) -> float:
    if not records:
        return 0.0
    weights = [_time_decay_weight(record.event_date, target_date) for record in records]
    total_weight = sum(weights)
    if total_weight <= 0:
        return 0.0
    return sum(w * value_fn(r) for w, r in zip(weights, records)) / total_weight


def _time_decayed_history_rate(
    records: list[FightHistoryItem],
    target_date: date,
    predicate,
    default: float = 0.0,
) -> float:
    if not records:
        return default
    dated = [(r, _parse_history_date(r.date)) for r in records]
    dated = [(r, d) for r, d in dated if d is not None]
    if not dated:
        return default
    weights = [_time_decay_weight(d, target_date) for _, d in dated]
    total_weight = sum(weights)
    if total_weight <= 0:
        return default
    hit_weight = sum(w for (r, _), w in zip(dated, weights) if predicate(r))
    return hit_weight / total_weight


def _time_decayed_history_average(
    records: list[FightHistoryItem],
    target_date: date,
    value_fn,
) -> float:
    if not records:
        return 0.0
    dated = [(r, _parse_history_date(r.date)) for r in records]
    dated = [(r, d) for r, d in dated if d is not None]
    if not dated:
        return 0.0
    weights = [_time_decay_weight(d, target_date) for _, d in dated]
    total_weight = sum(weights)
    if total_weight <= 0:
        return 0.0
    return sum(w * value_fn(r) for (r, _), w in zip(dated, weights)) / total_weight


def _current_streak(records: list[FighterStatRecord], result: str) -> int:
    sorted_records = sorted(records, key=lambda r: r.event_date, reverse=True)
    count = 0
    for record in sorted_records:
        if record.result == result:
            count += 1
        else:
            break
    return count


def _current_history_streak(records: list[FightHistoryItem], win: bool) -> int:
    count = 0
    for record in records:
        is_win = record.result.lower().startswith("win")
        if is_win == win:
            count += 1
        else:
            break
    return count


def _most_recent_history_date(records: list[FightHistoryItem]) -> date | None:
    parsed_dates = [_parse_history_date(record.date) for record in records]
    available_dates = [item for item in parsed_dates if item is not None]
    return max(available_dates) if available_dates else None


def _parse_history_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip().replace(".", "")
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _number(value: Any) -> float:
    if value in {None, "", "--"}:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_number(stats: dict[str, Any], keys: list[str], fallback: float = 0.0) -> float:
    for key in keys:
        if key in stats and stats.get(key) not in {None, "", "--"}:
            return _number(stats.get(key))
    return fallback


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _rate(records: list[FighterStatRecord], predicate, default: float = 0.0) -> float:
    if not records:
        return default
    return sum(1 for record in records if predicate(record)) / len(records)


def _history_rate(records: list[FightHistoryItem], predicate, default: float = 0.0) -> float:
    if not records:
        return default
    return sum(1 for record in records if predicate(record)) / len(records)


def _is_finish(method: str | None) -> bool:
    if not method:
        return False
    normalized = method.lower()
    return "ko" in normalized or "tko" in normalized or "sub" in normalized


def _is_ko(method: str | None) -> bool:
    if not method:
        return False
    normalized = method.lower()
    return "ko" in normalized or "tko" in normalized


def _is_submission(method: str | None) -> bool:
    if not method:
        return False
    return "sub" in method.lower()


def _weight_class_to_kg(weight_class: str | None) -> float:
    """Map UFC weight class name to approximate competition weight in kg."""
    if not weight_class:
        return 70.3  # welterweight median fallback
    normalized = weight_class.lower()
    if "atomweight" in normalized:
        return 48.0
    if "strawweight" in normalized:
        return 52.2
    if "flyweight" in normalized:
        return 56.7
    if "bantamweight" in normalized:
        return 61.2
    if "featherweight" in normalized:
        return 65.8
    if "lightweight" in normalized:
        return 70.3
    if "welterweight" in normalized:
        return 77.1
    if "middleweight" in normalized:
        return 83.9
    if "light heavyweight" in normalized or "light_heavyweight" in normalized:
        return 93.0
    if "heavyweight" in normalized:
        return 120.2
    if "super heavyweight" in normalized:
        return 135.0
    return 70.3
