from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, classification_report, log_loss, roc_auc_score
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app import models
from app.schemas import PredictionRead
from app.services import db_store
from app.services.analyzer import analyze_fight, _apply_finish_constraints
from app.services.fighter_identity import build_fighter_identity
from app.services.explainability import compute_shap_values, shap_to_key_factors
from app.services.feature_builder import build_feature_vector, compute_data_quality_score, get_feature_names
from app.services.features import build_current_matchup_features
from app.services.training_pipeline import build_X, build_y, compute_sample_weights, examples_to_matrix

try:
    from xgboost import XGBClassifier  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    XGBClassifier = None

try:
    from lightgbm import LGBMClassifier  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    LGBMClassifier = None

log = logging.getLogger(__name__)

FEATURE_VERSION_V6 = "v6"
ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "ml" / "artifacts"


def train_win_prediction_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    weights_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
    db: Session,
) -> tuple[Any, dict[str, float], str]:
    estimators = _winner_estimators()
    stack = StackingClassifier(
        estimators=estimators,
        final_estimator=LogisticRegression(C=0.1, random_state=42, max_iter=1000),
        cv=min(5, _safe_cv_folds(y_train)),
        passthrough=True,
        n_jobs=-1,
    )

    try:
        stack.fit(X_train, y_train, sample_weight=weights_train)
    except TypeError:
        log.warning("StackingClassifier rejected sample_weight; fitting base stack without it.")
        stack.fit(X_train, y_train)

    calibration_cv = min(5, _safe_cv_folds(y_train))
    calibrated = CalibratedClassifierCV(stack, method="isotonic", cv=calibration_cv)
    try:
        calibrated.fit(X_train, y_train, sample_weight=weights_train)
    except TypeError:
        calibrated.fit(X_train, y_train)

    y_prob_val = calibrated.predict_proba(X_val)[:, 1]
    y_pred_val = (y_prob_val >= 0.5).astype(int)
    val_metrics = {
        "accuracy": round(float(accuracy_score(y_val, y_pred_val)), 4),
        "auc_roc": round(float(_safe_auc(y_val, y_prob_val)), 4),
        "brier_score": round(float(brier_score_loss(y_val, y_prob_val)), 4),
        "log_loss": round(float(log_loss(y_val, y_prob_val, labels=[0, 1])), 4),
    }
    print("TASK 2 WIN MODEL VALIDATION METRICS")
    print(val_metrics)
    print(classification_report(y_val, y_pred_val, zero_division=0))

    artifact_path = _artifact_path("win_model_v6")
    artifact = {
        "model": calibrated,
        "algorithm": "StackingEnsemble_XGB_LGBM_ET",
        "feature_names": feature_names,
        "feature_keys": feature_names,
        "feature_version": FEATURE_VERSION_V6,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "optional_estimators": [name for name, _estimator in estimators],
    }
    joblib.dump(artifact, artifact_path)

    source_fights = int(
        db.scalar(select(func.count(models.Fight.id)).where(models.Fight.result_winner_id.is_not(None))) or int(len(X_train) / 2)
    )
    metrics = {
        "validation": "rolling_year_holdout",
        "accuracy": val_metrics["accuracy"],
        "auc_roc": val_metrics["auc_roc"],
        "brier_score": val_metrics["brier_score"],
        "log_loss": val_metrics["log_loss"],
        "feature_version": FEATURE_VERSION_V6,
        "feature_count": len(feature_names),
        "dataset": {
            "source_fights": source_fights,
            "train_examples": int(len(X_train)),
            "validation_examples": int(len(X_val)),
        },
        "optional_estimators": artifact["optional_estimators"],
    }
    model_version_id = db_store.save_model_version(
        db,
        name=f"winner-stacking-v6-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        algorithm="StackingEnsemble_XGB_LGBM_ET",
        artifact_uri=str(artifact_path),
        metrics=metrics,
        trained_until=date.today(),
    )
    print(f"TASK 2 COMPLETE - registered model {model_version_id} at {artifact_path}")
    return calibrated, val_metrics, model_version_id


def run_walk_forward_validation(all_examples: list[dict[str, Any]], feature_names: list[str], step_size: int = 100) -> list[dict[str, Any]]:
    sorted_examples = sorted(all_examples, key=lambda item: (item["event_date"], item["fight_id"], item["is_mirror"]))
    results: list[dict[str, Any]] = []
    min_train_size = min(500, max(100, len(sorted_examples) // 3))

    for split_idx in range(min_train_size, len(sorted_examples) - step_size, step_size):
        train = sorted_examples[:split_idx]
        test = sorted_examples[split_idx : split_idx + step_size]
        test_clean = [e for e in test if not e.get("is_mirror", False)]
        if len(test_clean) < 10 or len(set(e["label"] for e in test_clean)) < 2:
            continue

        X_train = build_X(train, feature_names)
        y_train = build_y(train)
        w_train = compute_sample_weights(train)
        X_test = build_X(test_clean, feature_names)
        y_test = build_y(test_clean)

        model = _walk_forward_model()
        try:
            model.fit(X_train, y_train, sample_weight=w_train)
        except TypeError:
            model.fit(X_train, y_train)
        y_prob = model.predict_proba(X_test)[:, 1]
        y_pred = (y_prob >= 0.5).astype(int)
        results.append(
            {
                "train_size": split_idx,
                "test_start": test_clean[0]["event_date"].isoformat(),
                "test_end": test_clean[-1]["event_date"].isoformat(),
                "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
                "brier": round(float(brier_score_loss(y_test, y_prob)), 4),
                "n_test": len(test_clean),
            }
        )
        if len(results) % 10 == 0:
            print(f"Walk-forward progress: {len(results)} windows evaluated")

    print("TASK 2 WALK-FORWARD RESULTS")
    for row in results:
        print(row)
    if results:
        accuracies = [row["accuracy"] for row in results]
        trend = "stable"
        if accuracies[-1] - accuracies[0] > 0.03:
            trend = "improving"
        elif accuracies[-1] - accuracies[0] < -0.03:
            trend = "degrading"
        print(
            {
                "average_accuracy": round(float(np.mean(accuracies)), 4),
                "accuracy_trend": trend,
                "min_accuracy": min(accuracies),
                "max_accuracy": max(accuracies),
            }
        )
    return results


def prepare_method_training_data(db: Session) -> tuple[np.ndarray, np.ndarray, list[str]]:
    feature_names = get_feature_names()
    method_feature_names = feature_names + [
        "weight_class_ko_prior",
        "weight_class_sub_prior",
        "combined_slpm",
    ]
    rows = db.execute(
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Fight.result_method.is_not(None))
        .options(joinedload(models.Fight.fighter_a), joinedload(models.Fight.fighter_b))
        .order_by(models.Event.event_date.asc(), models.Fight.id.asc())
    ).all()

    X: list[list[float]] = []
    y: list[int] = []
    for fight, _event in rows:
        label = _method_label(fight.result_method)
        if label is None:
            continue
        try:
            features = build_feature_vector(fight.fighter_a, fight.fighter_b, fight, db)
        except Exception as exc:
            log.warning("Skipping method example %s: %s", fight.id, exc)
            continue
        features["weight_class_ko_prior"] = get_weight_class_ko_rate(fight.weight_class, db)
        features["weight_class_sub_prior"] = get_weight_class_sub_rate(fight.weight_class, db)
        features["combined_slpm"] = _fighter_slpm(fight.fighter_a) + _fighter_slpm(fight.fighter_b)
        X.append([float(features.get(name, 0.0)) for name in method_feature_names])
        y.append(label)

    y_array = np.array(y, dtype=int)
    total = len(y_array)
    print("TASK 3 METHOD CLASS DISTRIBUTION")
    for label, name in [(0, "KO/TKO"), (1, "Submission"), (2, "Decision")]:
        count = int(np.sum(y_array == label))
        pct = round(count / total * 100, 2) if total else 0.0
        print(f"{name}: {count} ({pct}%)")
    return np.array(X, dtype=float), y_array, method_feature_names


def train_method_prediction_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
    db: Session,
) -> Any:
    if LGBMClassifier is not None:
        method_model = LGBMClassifier(
            objective="multiclass",
            num_class=3,
            class_weight={0: 1.3, 1: 2.2, 2: 1.0},
            n_estimators=500,
            max_depth=6,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.7,
            min_child_samples=20,
            reg_alpha=0.1,
            reg_lambda=0.1,
            verbose=-1,
            random_state=42,
            n_jobs=-1,
        )
    else:
        method_model = RandomForestClassifier(
            n_estimators=350,
            max_depth=8,
            min_samples_leaf=4,
            class_weight={0: 1.3, 1: 2.2, 2: 1.0},
            random_state=42,
            n_jobs=-1,
        )
    method_model.fit(X_train, y_train)
    y_pred = method_model.predict(X_val)
    print("TASK 3 METHOD MODEL VALIDATION RESULTS")
    print(classification_report(y_val, y_pred, labels=[0, 1, 2], target_names=["KO/TKO", "Submission", "Decision"], zero_division=0))
    print(f"Overall accuracy: {accuracy_score(y_val, y_pred):.4f}")

    artifact_path = _artifact_path("method_model_v1")
    joblib.dump(
        {
            "model": method_model,
            "feature_names": feature_names,
            "feature_version": "method-v1",
            "trained_at": datetime.now(timezone.utc).isoformat(),
        },
        artifact_path,
    )
    db_store.save_model_version(
        db,
        name=f"method-model-v1-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        algorithm="MethodClassifier_LGBM" if LGBMClassifier is not None else "MethodClassifier_RF",
        artifact_uri=str(artifact_path),
        metrics={
            "validation": "rolling_year_holdout",
            "accuracy": round(float(accuracy_score(y_val, y_pred)), 4),
            "dataset": {"source_fights": int(len(X_train))},
            "feature_version": "method-v1",
            "feature_count": len(feature_names),
        },
        trained_until=date.today(),
    )
    print(f"TASK 3 COMPLETE - method model saved to {artifact_path}")
    return method_model


def apply_style_method_caps_to_predictions(
    method_probabilities: dict[str, float],
    fighter_a: models.Fighter,
    fighter_b: models.Fighter | None = None,
) -> tuple[dict[str, float], bool, list[str]]:
    style_method_caps = {
        ("kickboxer", "Submission"): 0.08,
        ("kickboxer", "KO_TKO"): 1.00,
        ("boxer", "Submission"): 0.06,
        ("boxer", "KO_TKO"): 1.00,
        ("pressure_striker", "Submission"): 0.08,
        ("counter_striker", "Submission"): 0.10,
        ("wrestler", "KO_TKO"): 0.25,
        ("wrestler", "Submission"): 0.40,
        ("bjj_specialist", "KO_TKO"): 0.15,
        ("wrestler_bjj", "KO_TKO"): 0.20,
        ("muay_thai", "Submission"): 0.10,
    }
    capped = dict(method_probabilities)
    style = (fighter_a.primary_style or "unknown").lower()
    cap_applied = False
    details: list[str] = []

    for method, probability in list(capped.items()):
        cap = style_method_caps.get((style, method))
        if cap is None or probability <= cap:
            continue
        excess = probability - cap
        capped[method] = cap
        cap_applied = True
        details.append(f"{method} capped from {probability:.2f} to {cap:.2f} ({style} style constraint)")
        other_methods = [item for item in capped if item != method]
        if other_methods:
            addition = excess / len(other_methods)
            for other in other_methods:
                capped[other] = min(1.0, capped[other] + addition)

    total = sum(capped.values())
    if total > 0:
        capped = {key: round(value / total, 4) for key, value in capped.items()}
    return capped, cap_applied, details


def apply_method_model_to_prediction(db: Session, prediction: PredictionRead) -> PredictionRead:
    method_artifact = _latest_method_artifact(db)
    if not method_artifact:
        return prediction

    fight = db.scalar(
        select(models.Fight)
        .where(models.Fight.id == prediction.fight_id)
        .options(joinedload(models.Fight.fighter_a), joinedload(models.Fight.fighter_b))
    )
    if fight is None:
        return prediction

    try:
        vector = build_feature_vector(fight.fighter_a, fight.fighter_b, fight, db)
        favored = fight.fighter_a if prediction.adjusted_probability_a >= 0.5 else fight.fighter_b
        raw_probs, raw_copy = _predict_method_probs(method_artifact, vector, fight.fighter_a, fight.fighter_b)
        if not raw_probs:
            return prediction
        capped, cap_applied, cap_details = apply_style_method_caps_to_predictions(raw_probs, favored)
        normalized = {
            "KO/TKO": round(capped.get("KO_TKO", 0.0), 4),
            "submission": round(capped.get("Submission", 0.0), 4),
            "decision": round(capped.get("Decision", 0.0), 4),
            "other": 0.0,
        }
        # Apply fighter history constraints AFTER method model — prevents bypassing sub/KO caps
        favored_is_a = prediction.adjusted_probability_a >= 0.5
        from app.services.db_store import _fighter_to_schema
        identity_a = build_fighter_identity(_fighter_to_schema(fight.fighter_a))
        identity_b = build_fighter_identity(_fighter_to_schema(fight.fighter_b))
        normalized = _apply_finish_constraints(normalized, identity_a, identity_b, favored_is_a)
        likely_method = max(normalized, key=normalized.get)
        return prediction.model_copy(
            update={
                "method_probabilities_raw": raw_copy,
                "method_probabilities": normalized,
                "likely_method": likely_method,
                "style_method_cap_applied": cap_applied,
                "style_cap_details": "; ".join(cap_details) if cap_details else None,
            }
        )
    except Exception as exc:
        log.warning("Method model override failed for fight %s: %s", prediction.fight_id, exc)
        return prediction


def verify_calibration(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    X_val: np.ndarray | None = None,
    y_val: np.ndarray | None = None,
) -> dict[str, Any]:
    y_prob = model.predict_proba(X_test)[:, 1]
    fraction_pos, mean_pred = calibration_curve(y_test, y_prob, n_bins=min(10, max(2, len(y_test) // 10)), strategy="quantile")
    print("TASK 5 CALIBRATION VERIFICATION")
    print(f"{'Predicted':>12} {'Actual':>10} {'Difference':>12}")
    max_error = 0.0
    for pred, actual in zip(mean_pred, fraction_pos):
        diff = abs(float(pred) - float(actual))
        max_error = max(max_error, diff)
        flag = "WARN" if diff > 0.05 else "OK"
        print(f"{pred:>12.3f} {actual:>10.3f} {diff:>12.3f} {flag}")
    brier = float(brier_score_loss(y_test, y_prob))
    print(f"Brier score: {brier:.4f}")
    print(f"Max calibration error: {max_error:.3f}")
    return {
        "brier_score": round(brier, 4),
        "max_calibration_error": round(max_error, 4),
        "status": "PASS" if brier < 0.21 and max_error < 0.08 else "NEEDS_IMPROVEMENT",
        "mean_predicted": [round(float(x), 4) for x in mean_pred],
        "fraction_positive": [round(float(x), 4) for x in fraction_pos],
    }


def compare_and_deploy_model(new_model_version_id: str, current_active_model_id: str | None, db: Session) -> bool:
    new_model = db.get(models.ModelVersion, new_model_version_id)
    current_model = db.get(models.ModelVersion, current_active_model_id) if current_active_model_id else None
    if new_model is None:
        raise ValueError(f"New model version {new_model_version_id} not found")

    current_metrics = current_model.metrics if current_model else {}
    new_brier = _metric(new_model, "brier_score", default=1.0)
    current_brier = _metric(current_model, "brier_score", default=1.0) if current_model else 1.0
    current_accuracy = float(current_model.accuracy or 0.0) if current_model else 0.0
    print("TASK 6 MODEL COMPARISON")
    print(
        {
            "current": {
                "id": current_model.id if current_model else None,
                "algorithm": current_model.algorithm if current_model else None,
                "accuracy": current_model.accuracy if current_model else None,
                "source_fights": current_model.source_fights if current_model else None,
                "validation": current_model.validation_type if current_model else None,
                "brier_score": current_brier,
            },
            "new": {
                "id": new_model.id,
                "algorithm": new_model.algorithm,
                "accuracy": new_model.accuracy,
                "source_fights": new_model.source_fights,
                "validation": new_model.validation_type,
                "brier_score": new_brier,
            },
        }
    )

    criteria = [
        new_model.validation_type == "rolling_year_holdout",
        (new_model.source_fights or 0) >= (current_model.source_fights or 0 if current_model else 0),
        float(new_model.accuracy or 0.0) >= current_accuracy - 0.005,
        new_brier <= current_brier + 0.005,
    ]
    calibration_status = ((new_model.metrics or {}).get("test_calibration") or {}).get("status")
    if calibration_status:
        criteria.append(calibration_status == "PASS")
    deployed = all(criteria)
    if deployed:
        for model_version in db.scalars(select(models.ModelVersion).where(models.ModelVersion.is_active == True)):  # noqa: E712
            model_version.is_active = False
        new_model.is_active = True
        reason = "New model met deployment criteria."
        print("New model deployed as active.")
    else:
        failed = [idx for idx, ok in enumerate(criteria) if not ok]
        reason = f"Deployment blocked. Failed criteria: {failed}"
        print(reason)

    db.add(
        models.ModelComparison(
            id=str(uuid4()),
            new_model_version_id=new_model.id,
            current_model_version_id=current_model.id if current_model else None,
            metrics={
                "new_accuracy": new_model.accuracy,
                "current_accuracy": current_model.accuracy if current_model else None,
                "new_brier": new_brier,
                "current_brier": current_brier,
                "criteria": criteria,
                "current_metrics": current_metrics,
                "new_metrics": new_model.metrics or {},
            },
            decision="deployed" if deployed else "blocked",
            reason=reason,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    print(f"TASK 6 COMPLETE - deployment {'completed' if deployed else 'blocked'}")
    return deployed


def regenerate_upcoming_predictions(db: Session) -> dict[str, Any]:
    active_model = db.scalar(
        select(models.ModelVersion)
        .where(models.ModelVersion.is_active == True)  # noqa: E712
        .where(models.ModelVersion.algorithm == "StackingEnsemble_XGB_LGBM_ET")
        .order_by(models.ModelVersion.created_at.desc())
        .limit(1)
    )
    if not active_model or not active_model.artifact_uri:
        print("TASK 7 SKIPPED - no active v6 stacking model deployed.")
        return {"status": "skipped", "reason": "no_active_v6_model"}

    artifact = joblib.load(active_model.artifact_uri)
    win_model = artifact["model"]
    feature_names = artifact.get("feature_names") or get_feature_names()
    method_artifact = _latest_method_artifact(db)

    rows = db.execute(
        select(models.Fight, models.Event)
        .join(models.Event, models.Fight.event_id == models.Event.id)
        .where(models.Event.status == "upcoming")
        .where(models.Event.event_date >= date.today())
        .where(models.Fight.status == "scheduled")
        .options(joinedload(models.Fight.fighter_a), joinedload(models.Fight.fighter_b))
        .order_by(models.Event.event_date.asc(), models.Fight.bout_order.asc())
    ).all()

    run = db_store.create_run(
        db,
        "phase4_regenerate_predictions",
        "Regenerated upcoming predictions after Phase 4 model upgrade",
        {"model_version_id": active_model.id},
    )
    summary = {"total_upcoming_fights": len(rows), "generated": 0, "high": 0, "medium": 0, "low": 0, "failed": []}

    for fight, _event in rows:
        try:
            fighter_a = fight.fighter_a
            fighter_b = fight.fighter_b
            v6_vector = build_feature_vector(fighter_a, fighter_b, fight, db)
            X_single = np.array([[v6_vector.get(name, 0.0) for name in feature_names]], dtype=float)
            model_probability = float(win_model.predict_proba(X_single)[0, 1])
            shap_df = compute_shap_values(win_model, X_single, feature_names)
            key_factors = shap_to_key_factors(shap_df, fighter_a.name, fighter_b.name)
            method_probs, raw_method_probs = _predict_method_probs(method_artifact, v6_vector, fighter_a, fighter_b)
            cap_applied, cap_details = False, []
            if method_probs:
                method_probs, cap_applied, cap_details = apply_style_method_caps_to_predictions(method_probs, fighter_a, fighter_b)

            fight_read = db_store.get_fight(db, fight.id)
            if fight_read is None:
                raise ValueError("FightRead conversion failed")
            legacy_vector = build_current_matchup_features(fight_read)
            prediction = analyze_fight(
                fight_read,
                db_store.list_fight_risks(db, fight.id),
                legacy_vector,
                model_probability_a=model_probability,
                model_context={
                    "model_source": "winner_StackingEnsemble_XGB_LGBM_ET",
                    "model_version_id": active_model.id,
                    "model_feature_version": FEATURE_VERSION_V6,
                    "model_feature_vector": v6_vector,
                    "model_top_factors": [factor["factor"] for factor in key_factors[:3]],
                    "raw_model_probability_a": model_probability,
                    "calibration_method": "isotonic",
                },
                odds_snapshot=db_store.get_latest_odds_snapshot(db, fight.id),
            )
            updates: dict[str, Any] = {
                "feature_version": FEATURE_VERSION_V6,
                "model_feature_version": FEATURE_VERSION_V6,
                "model_feature_vector": v6_vector,
                "key_signals": [f"{f['factor']} {f['direction']}" for f in key_factors[:5]],
            }
            if method_probs:
                normalized = {
                    "KO/TKO": round(method_probs.get("KO_TKO", 0.0), 4),
                    "submission": round(method_probs.get("Submission", 0.0), 4),
                    "decision": round(method_probs.get("Decision", 0.0), 4),
                    "other": 0.0,
                }
                likely_method = max(normalized, key=normalized.get)
                updates.update(
                    {
                        "method_probabilities_raw": raw_method_probs,
                        "method_probabilities": normalized,
                        "likely_method": likely_method,
                        "style_method_cap_applied": cap_applied,
                        "style_cap_details": "; ".join(cap_details) if cap_details else None,
                    }
                )
            prediction = prediction.model_copy(update=updates)
            feature_set_id = db_store.save_feature_set(db, fight_read, v6_vector, FEATURE_VERSION_V6)
            db_store.save_prediction(
                db,
                prediction,
                run.id,
                feature_set_id,
                active_model.id,
                auto_refresh_trigger="phase4_model_upgrade",
            )
            summary["generated"] += 1
            confidence = prediction.confidence.lower()
            if confidence in summary:
                summary[confidence] += 1
        except Exception as exc:
            log.exception("Failed to regenerate prediction for fight %s", fight.id)
            summary["failed"].append({"fight_id": fight.id, "error": str(exc)})

    print("TASK 7 COMPLETE - regeneration summary")
    print(summary)
    return summary


def _winner_estimators():
    estimators = []
    if XGBClassifier is not None:
        estimators.append(
            (
                "xgb",
                XGBClassifier(
                    n_estimators=500,
                    max_depth=5,
                    learning_rate=0.03,
                    subsample=0.8,
                    colsample_bytree=0.7,
                    min_child_weight=3,
                    gamma=0.1,
                    reg_alpha=0.1,
                    reg_lambda=1.0,
                    objective="binary:logistic",
                    eval_metric="auc",
                    random_state=42,
                    n_jobs=-1,
                ),
            )
        )
    else:
        estimators.append(
            (
                "rf_fallback",
                RandomForestClassifier(
                    n_estimators=300,
                    max_depth=8,
                    min_samples_leaf=5,
                    class_weight="balanced",
                    random_state=42,
                    n_jobs=-1,
                ),
            )
        )
    if LGBMClassifier is not None:
        estimators.append(
            (
                "lgbm",
                LGBMClassifier(
                    n_estimators=500,
                    max_depth=6,
                    learning_rate=0.03,
                    subsample=0.8,
                    colsample_bytree=0.7,
                    min_child_samples=20,
                    reg_alpha=0.1,
                    reg_lambda=0.1,
                    verbose=-1,
                    random_state=42,
                    n_jobs=-1,
                ),
            )
        )
    estimators.append(
        (
            "et",
            ExtraTreesClassifier(
                n_estimators=400,
                max_depth=8,
                min_samples_leaf=5,
                random_state=42,
                n_jobs=-1,
            ),
        )
    )
    return estimators


def _walk_forward_model():
    if XGBClassifier is not None:
        return XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            random_state=42,
            eval_metric="auc",
        )
    return ExtraTreesClassifier(n_estimators=180, max_depth=7, min_samples_leaf=5, random_state=42, n_jobs=-1)


def _safe_cv_folds(y: np.ndarray) -> int:
    _, counts = np.unique(y, return_counts=True)
    if len(counts) < 2:
        raise ValueError("Training labels must include both classes")
    return max(2, min(5, int(counts.min())))


def _safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    if len(set(y_true.tolist())) < 2:
        return 0.5
    return float(roc_auc_score(y_true, y_prob))


def _artifact_path(prefix: str) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return ARTIFACT_DIR / f"{prefix}_{stamp}.joblib"


def _method_label(method: str | None) -> int | None:
    normalized = (method or "").lower()
    if "ko" in normalized or "tko" in normalized:
        return 0
    if "sub" in normalized:
        return 1
    if "decision" in normalized or "split" in normalized or "majority" in normalized or "dec" in normalized:
        return 2
    return None


def _fighter_slpm(fighter: models.Fighter) -> float:
    if fighter.slpm_rw is not None:
        return float(fighter.slpm_rw)
    try:
        return float((fighter.profile_stats or {}).get("strikes_landed_per_min") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def get_weight_class_ko_rate(weight_class: str | None, db: Session) -> float:
    rows = db.scalars(
        select(models.Fight.result_method)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Fight.weight_class == weight_class)
    ).all()
    if not rows:
        return 0.32
    return sum(1 for method in rows if method and ("ko" in method.lower() or "tko" in method.lower())) / len(rows)


def get_weight_class_sub_rate(weight_class: str | None, db: Session) -> float:
    rows = db.scalars(
        select(models.Fight.result_method)
        .where(models.Fight.result_winner_id.is_not(None))
        .where(models.Fight.weight_class == weight_class)
    ).all()
    if not rows:
        return 0.18
    return sum(1 for method in rows if method and "sub" in method.lower()) / len(rows)


def _latest_method_artifact(db: Session) -> dict[str, Any] | None:
    model_version = db.scalar(
        select(models.ModelVersion)
        .where(models.ModelVersion.algorithm.in_(["MethodClassifier_LGBM", "MethodClassifier_RF"]))
        .order_by(models.ModelVersion.created_at.desc())
        .limit(1)
    )
    if not model_version or not model_version.artifact_uri:
        return None
    path = Path(model_version.artifact_uri)
    if not path.exists():
        return None
    return joblib.load(path)


def _predict_method_probs(
    method_artifact: dict[str, Any] | None,
    vector: dict[str, float],
    fighter_a: models.Fighter,
    fighter_b: models.Fighter,
) -> tuple[dict[str, float], dict[str, float]]:
    if not method_artifact:
        return {}, {}
    model = method_artifact["model"]
    feature_names = method_artifact["feature_names"]
    features = dict(vector)
    fight_proxy = None
    features.setdefault("weight_class_ko_prior", 0.32)
    features.setdefault("weight_class_sub_prior", 0.18)
    features.setdefault("combined_slpm", _fighter_slpm(fighter_a) + _fighter_slpm(fighter_b))
    X = np.array([[features.get(name, 0.0) for name in feature_names]], dtype=float)
    probs = model.predict_proba(X)[0]
    raw = {"KO_TKO": round(float(probs[0]), 4), "Submission": round(float(probs[1]), 4), "Decision": round(float(probs[2]), 4)}
    return dict(raw), raw


def _metric(model_version: models.ModelVersion | None, key: str, default: float) -> float:
    if model_version is None:
        return default
    value = (model_version.metrics or {}).get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
