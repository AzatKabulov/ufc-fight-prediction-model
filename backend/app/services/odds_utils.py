from __future__ import annotations


def american_to_decimal(american_odds: int | float) -> float:
    """Convert American odds to decimal odds."""
    odds = float(american_odds)
    if odds > 0:
        return (odds / 100.0) + 1.0
    return (100.0 / abs(odds)) + 1.0


def american_to_implied_raw(american_odds: int | float) -> float:
    """Convert American odds to raw, vig-included implied probability."""
    odds = float(american_odds)
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def remove_vig(implied_a: float, implied_b: float) -> tuple[float, float]:
    """Normalize two implied probabilities so they sum to exactly 1.0."""
    total = float(implied_a) + float(implied_b)
    if total <= 0:
        return 0.5, 0.5
    return float(implied_a) / total, float(implied_b) / total


def implied_to_american(probability: float) -> int:
    """Convert win probability back to American odds for display."""
    probability = min(0.999, max(0.001, float(probability)))
    if probability >= 0.5:
        return round(-probability / (1.0 - probability) * 100.0)
    return round((1.0 - probability) / probability * 100.0)


def compute_edge(model_prob: float, market_implied_prob: float) -> float:
    """Positive edge means the model is higher than the vig-free market."""
    return float(model_prob) - float(market_implied_prob)


def compute_kelly_fraction(edge: float, decimal_odds: float, fraction: float = 0.25) -> float:
    """Quarter-Kelly informational fraction. This is not a bet-sizing recommendation."""
    b = float(decimal_odds) - 1.0
    if b <= 0:
        return 0.0
    p = float(edge) + (1.0 / float(decimal_odds))
    p = min(1.0, max(0.0, p))
    q = 1.0 - p
    full_kelly = (b * p - q) / b
    return max(0.0, full_kelly * float(fraction))


def edge_magnitude_label(edge: float | None) -> str:
    if edge is None:
        return "unavailable"
    magnitude = abs(edge)
    if magnitude < 0.03:
        return "negligible"
    if magnitude < 0.07:
        return "small"
    if magnitude < 0.12:
        return "meaningful"
    return "significant"
