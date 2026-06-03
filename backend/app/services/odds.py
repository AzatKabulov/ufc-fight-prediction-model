from __future__ import annotations

from app.schemas import OddsSnapshotRead, PredictionRead

VALUE_EDGE_THRESHOLD = 0.04
MIN_VALUE_DATA_QUALITY = 70


def american_to_decimal(american_odds: float | None) -> float | None:
    if american_odds is None or american_odds == 0:
        return None
    if american_odds > 0:
        return round(1 + american_odds / 100, 4)
    return round(1 + 100 / abs(american_odds), 4)


def american_to_implied_probability(american_odds: float | None) -> float | None:
    if american_odds is None or american_odds == 0:
        return None
    if american_odds > 0:
        return round(100 / (american_odds + 100), 6)
    return round(abs(american_odds) / (abs(american_odds) + 100), 6)


def decimal_to_american(decimal_odds: float | None) -> float | None:
    if decimal_odds is None or decimal_odds <= 1:
        return None
    if decimal_odds >= 2:
        return round((decimal_odds - 1) * 100)
    return round(-100 / (decimal_odds - 1))


def no_vig_probability(probability_a: float | None, probability_b: float | None) -> float | None:
    if probability_a is None or probability_b is None:
        return probability_a
    total = probability_a + probability_b
    if total <= 0:
        return None
    return round(probability_a / total, 6)


def line_movement(
    current_american_odds: float | None,
    opening_american_odds: float | None,
) -> float | None:
    current = american_to_implied_probability(current_american_odds)
    opening = american_to_implied_probability(opening_american_odds)
    if current is None or opening is None:
        return None
    return round(current - opening, 6)


def decorate_odds_snapshot(snapshot: OddsSnapshotRead) -> OddsSnapshotRead:
    implied_a = american_to_implied_probability(snapshot.fighter_a_american_odds)
    implied_b = american_to_implied_probability(snapshot.fighter_b_american_odds)
    no_vig_a = no_vig_probability(implied_a, implied_b)
    movement_a = line_movement(snapshot.fighter_a_american_odds, snapshot.opening_fighter_a_american_odds)
    return snapshot.model_copy(
        update={
            "implied_probability_a": implied_a,
            "no_vig_probability_a": no_vig_a,
            "line_movement_a": movement_a,
        }
    )


def apply_market_context(
    prediction: PredictionRead,
    odds_snapshot: OddsSnapshotRead | None,
) -> PredictionRead:
    if odds_snapshot is None:
        return prediction

    snapshot = decorate_odds_snapshot(odds_snapshot)
    market_probability_a = snapshot.no_vig_probability_a or snapshot.implied_probability_a
    decimal_a = american_to_decimal(snapshot.fighter_a_american_odds)
    decimal_b = american_to_decimal(snapshot.fighter_b_american_odds)

    favored_is_a = prediction.adjusted_probability_a >= 0.5
    fightiq_pick_probability = (
        prediction.adjusted_probability_a if favored_is_a else 1 - prediction.adjusted_probability_a
    )
    market_pick_probability = None
    decimal_pick_odds = decimal_a if favored_is_a else decimal_b
    if market_probability_a is not None:
        market_pick_probability = market_probability_a if favored_is_a else 1 - market_probability_a

    edge = None
    if market_pick_probability is not None:
        edge = round(fightiq_pick_probability - market_pick_probability, 4)

    expected_value = None
    if decimal_pick_odds is not None:
        expected_value = round((fightiq_pick_probability * decimal_pick_odds) - 1, 4)

    value_flag = (
        edge is not None
        and expected_value is not None
        and edge >= VALUE_EDGE_THRESHOLD
        and expected_value >= VALUE_EDGE_THRESHOLD
        and prediction.data_quality >= MIN_VALUE_DATA_QUALITY
        and prediction.pick_grade != "no_pick"
        and prediction.confidence != "low"
    )

    no_pick_reason = prediction.no_pick_reason
    pick_grade = prediction.pick_grade
    confidence = prediction.confidence
    trust_warnings = list(prediction.trust_warnings)
    market_gap = (
        abs(prediction.adjusted_probability_a - market_probability_a)
        if market_probability_a is not None
        else None
    )
    if market_gap is not None and market_gap >= 0.20 and prediction.data_quality < 70:
        pick_grade = "no_pick"
        confidence = "low"
        no_pick_reason = "Large gap vs market odds with incomplete data"
        warning = "Large gap vs market odds - data may be incomplete"
        if warning not in trust_warnings:
            trust_warnings.append(warning)

    if (
        odds_snapshot.fighter_a_american_odds is not None
        and odds_snapshot.fighter_b_american_odds is not None
        and not value_flag
        and pick_grade != "no_pick"
    ):
        no_pick_reason = _market_no_value_reason(edge, expected_value)

    return prediction.model_copy(
        update={
            "market_probability_a": round(market_probability_a, 3) if market_probability_a is not None else None,
            "edge": edge,
            "expected_value": expected_value,
            "value_flag": value_flag,
            "pick_grade": pick_grade,
            "no_pick_reason": no_pick_reason,
            "confidence": confidence,
            "trust_warnings": trust_warnings[:6],
            "odds_summary": _odds_summary(prediction, snapshot, edge, expected_value, value_flag),
            "odds_snapshot": snapshot.model_dump(mode="json"),
        }
    )


def _market_no_value_reason(edge: float | None, expected_value: float | None) -> str:
    if edge is None or expected_value is None:
        return "Market odds are incomplete; no value call available"
    if edge < 0:
        return "Market is stronger than the Fight IQ read; no value"
    if edge < VALUE_EDGE_THRESHOLD:
        return "Fight IQ edge is below the 4% value threshold"
    return "Expected value is below the 4% value threshold"


def _odds_summary(
    prediction: PredictionRead,
    snapshot: OddsSnapshotRead,
    edge: float | None,
    expected_value: float | None,
    value_flag: bool,
) -> str:
    source = snapshot.sportsbook or snapshot.source or "market"
    odds_a = _format_american(snapshot.fighter_a_american_odds)
    odds_b = _format_american(snapshot.fighter_b_american_odds)
    if edge is None or expected_value is None:
        return f"{source}: {prediction.fighter_a} {odds_a}, {prediction.fighter_b} {odds_b}. Market read incomplete."
    verdict = "possible value" if value_flag else "no value"
    favored = prediction.fighter_a if prediction.adjusted_probability_a >= 0.5 else prediction.fighter_b
    return (
        f"{source}: {prediction.fighter_a} {odds_a}, {prediction.fighter_b} {odds_b}. "
        f"Fight IQ pick side: {favored}; edge {edge * 100:.1f}%, EV {expected_value * 100:.1f}% ({verdict})."
    )


def _format_american(value: float | None) -> str:
    if value is None:
        return "N/A"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{int(value)}"
