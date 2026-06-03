from __future__ import annotations

import hashlib
import logging
from typing import Any

log = logging.getLogger(__name__)


def define_signal_taxonomy() -> dict[str, Any]:
    """Single source of truth for fight-week signal tiers."""
    return {
        "TIER_1": {
            "description": "Game-changing - moves prediction 10-15%",
            "probability_impact_range": (0.10, 0.15),
            "confidence_impact": "maintain_or_increase",
            "signals": {
                "hospitalization": {
                    "keywords": ["hospitalized", "hospital", "emergency room", "medical emergency", "rushed to hospital"],
                    "direction": "negative",
                    "impact": 0.14,
                    "description": "Fighter hospitalized during fight week",
                },
                "medical_withdrawal_risk": {
                    "keywords": ["medical withdrawal", "pulled from", "fight in jeopardy", "doctor cleared", "medically cleared", "health scare"],
                    "direction": "negative",
                    "impact": 0.13,
                    "description": "Medical issue threatening fight status",
                },
                "missed_weight_official": {
                    "keywords": ["missed weight", "came in over", "failed to make weight", "overweight", "weight miss"],
                    "direction": "negative",
                    "impact": 0.12,
                    "description": "Official weight miss at weigh-ins",
                },
                "confirmed_injury": {
                    "keywords": ["confirmed injury", "fracture", "torn", "surgery required", "broken", "dislocation", "confirmed broken"],
                    "direction": "negative",
                    "impact": 0.11,
                    "description": "Confirmed significant injury",
                },
                "severe_weight_cut": {
                    "keywords": ["severely drained", "barely made weight", "iv drip", "couldn't stand", "had to be helped", "looked dead", "gaunt", "frighteningly thin"],
                    "direction": "negative",
                    "impact": 0.10,
                    "description": "Severe visible weight cut distress",
                },
            },
        },
        "TIER_2": {
            "description": "Significant - moves prediction 5-9%",
            "probability_impact_range": (0.05, 0.09),
            "confidence_impact": "reduce_if_conflicting",
            "signals": {
                "weight_cut_difficulty": {
                    "keywords": ["difficult cut", "tough cut", "hard weight cut", "struggling to make weight", "weight cut concern", "cutting a lot", "heavy cut"],
                    "direction": "negative",
                    "impact": 0.07,
                    "description": "Reported weight cut difficulty",
                },
                "drained_appearance": {
                    "keywords": ["looked drained", "appeared drained", "looked depleted", "low energy", "looked tired", "looked weak", "under the weather", "not himself", "not herself"],
                    "direction": "negative",
                    "impact": 0.07,
                    "description": "Fighter appeared drained at public appearance",
                },
                "camp_change": {
                    "keywords": ["changed camps", "left the team", "new trainer", "switched gyms", "new corner", "left his gym", "no longer training", "parted ways"],
                    "direction": "negative",
                    "impact": 0.06,
                    "description": "Recent camp or coach change",
                },
                "injury_rumor": {
                    "keywords": ["reportedly injured", "injury rumor", "sources say injury", "banged up", "nursing an injury", "dealing with", "managing an injury", "injury concern"],
                    "direction": "negative",
                    "impact": 0.06,
                    "description": "Unconfirmed injury report",
                },
                "sharp_line_movement": {
                    "keywords": ["sharp money", "line movement", "odds moved", "line shifted", "steam move", "professional money"],
                    "direction": "contextual",
                    "impact": 0.07,
                    "description": "Significant betting line movement detected",
                },
                "coach_concern": {
                    "keywords": ["coach worried", "corner concerned", "trainer unsure", "camp expressing concern", "team worried about"],
                    "direction": "negative",
                    "impact": 0.05,
                    "description": "Fighter's own camp expressing unusual concern",
                },
                "strong_camp_report": {
                    "keywords": ["best camp of career", "incredible camp", "sharpest ever", "best he's looked", "best she's looked", "perfect camp", "great camp", "excellent preparation"],
                    "direction": "positive",
                    "impact": 0.05,
                    "description": "Strongly positive camp report",
                },
            },
        },
        "TIER_3": {
            "description": "Contextual - moves prediction 1-4%",
            "probability_impact_range": (0.01, 0.04),
            "confidence_impact": "no_change",
            "signals": {
                "open_workout_sharp": {
                    "keywords": ["looked sharp", "impressive workout", "great shape", "looked in great shape", "crisp", "explosive", "moved well", "looked hungry"],
                    "direction": "positive",
                    "impact": 0.02,
                    "description": "Sharp performance at open workouts",
                },
                "easy_weight_cut": {
                    "keywords": ["came in light", "easy cut", "no weight issues", "looked full", "healthy at weigh-ins", "looked great", "comfortable at the weight"],
                    "direction": "positive",
                    "impact": 0.02,
                    "description": "Easy weight cut reported",
                },
                "single_source_injury": {
                    "keywords": ["possible injury", "might be injured", "some reports", "one source says", "heard he is", "heard she is"],
                    "direction": "negative",
                    "impact": 0.02,
                    "description": "Single unconfirmed injury source",
                },
                "personal_issue": {
                    "keywords": ["personal issue", "family issue", "dealing with personal", "off the mats", "distracted", "not focused", "outside issues"],
                    "direction": "negative",
                    "impact": 0.02,
                    "description": "Personal life issue mentioned",
                },
                "motivation_concern": {
                    "keywords": ["not motivated", "checked out", "just fighting for money", "unhappy with ufc", "wants to leave", "contract dispute"],
                    "direction": "negative",
                    "impact": 0.02,
                    "description": "Motivation concern flagged",
                },
                "chip_on_shoulder": {
                    "keywords": ["disrespected", "overlooked", "prove wrong", "chip on shoulder", "has something to prove", "motivated by doubters", "revenge", "redemption"],
                    "direction": "positive",
                    "impact": 0.02,
                    "description": "Fighter motivated by perceived disrespect",
                },
            },
        },
        "TIER_4": {
            "description": "Noise - no prediction adjustment",
            "probability_impact_range": (0.0, 0.0),
            "confidence_impact": "no_change",
            "signals": {
                "trash_talk": {
                    "keywords": ["trash talk", "called out", "going to knock out", "i will finish", "he has no chance", "she has no chance", "guaranteed finish", "easy fight"],
                    "direction": "neutral",
                    "impact": 0.0,
                    "description": "Pre-fight trash talk - no predictive value",
                },
                "self_prediction": {
                    "keywords": ["predicts victory", "i will win", "confident i win", "no doubt in my mind", "i'm going to win"],
                    "direction": "neutral",
                    "impact": 0.0,
                    "description": "Fighter predicting own victory - standard behavior",
                },
                "general_hype": {
                    "keywords": ["exciting fight", "can't wait", "looking forward to", "great matchup", "fans are excited", "big fight"],
                    "direction": "neutral",
                    "impact": 0.0,
                    "description": "General event hype - no predictive value",
                },
            },
        },
    }


def classify_text_signal(text: str, fighter_name: str, taxonomy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Detect tiered signals with keyword proximity to a fighter name."""
    taxonomy = taxonomy or define_signal_taxonomy()
    text_lower = (text or "").lower()
    fighter_name_lower = (fighter_name or "").lower()
    detected: list[dict[str, Any]] = []

    for tier_key, tier_data in taxonomy.items():
        tier_num = int(tier_key.split("_")[1])
        for signal_key, signal_data in tier_data["signals"].items():
            for keyword in signal_data["keywords"]:
                kw_pos = text_lower.find(keyword.lower())
                if kw_pos == -1:
                    continue
                if signal_key == "severe_weight_cut" and keyword.lower() == "gaunt":
                    severe_context = (
                        "severely drained",
                        "barely made weight",
                        "couldn't stand",
                        "had to be helped",
                        "looked dead",
                        "frighteningly thin",
                    )
                    if not any(marker in text_lower for marker in severe_context):
                        continue

                name_positions = _find_name_positions(text_lower, fighter_name_lower)
                is_relevant = False
                if name_positions:
                    is_relevant = any(abs(kw_pos - name_pos) <= 300 for name_pos in name_positions)
                else:
                    is_relevant = True

                if is_relevant:
                    detected.append(
                        {
                            "tier": tier_num,
                            "signal_type": signal_key,
                            "signal_direction": signal_data["direction"],
                            "probability_impact": signal_data["impact"],
                            "description": signal_data["description"],
                            "matched_keyword": keyword,
                            "confidence": "high" if name_positions else "medium",
                        }
                    )
                    break

    seen: set[str] = set()
    deduped = []
    for item in detected:
        if item["signal_type"] in seen:
            continue
        deduped.append(item)
        seen.add(item["signal_type"])
    deduped.sort(key=lambda item: item["tier"])
    return deduped


def compute_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def resolve_conflicting_signals(signals_for_fighter: list[dict[str, Any]]) -> dict[str, Any]:
    if not signals_for_fighter:
        return {"net_impact": 0.0, "confidence_effect": "no_change", "conflict_flag": False}

    tier1_negative = [s for s in signals_for_fighter if s.get("tier") == 1 and s.get("signal_direction") == "negative"]
    tier1_positive = [s for s in signals_for_fighter if s.get("tier") == 1 and s.get("signal_direction") == "positive"]
    tier2_negative = [s for s in signals_for_fighter if s.get("tier") == 2 and s.get("signal_direction") == "negative"]
    tier2_positive = [s for s in signals_for_fighter if s.get("tier") == 2 and s.get("signal_direction") == "positive"]

    if tier1_negative:
        net_impact = -sum(float(s.get("probability_impact") or 0.0) for s in tier1_negative)
        return {
            "net_impact": max(-0.18, min(0.18, net_impact)),
            "confidence_effect": "maintain",
            "conflict_flag": len(tier1_positive) > 0,
            "dominant_signal": tier1_negative[0].get("signal_type"),
            "override_reason": "Tier 1 negative signal dominates",
        }

    if tier2_negative and tier2_positive:
        net_impact = (
            sum(float(s.get("probability_impact") or 0.0) for s in tier2_positive)
            - sum(float(s.get("probability_impact") or 0.0) for s in tier2_negative)
        ) * 0.3
        return {
            "net_impact": max(-0.18, min(0.18, net_impact)),
            "confidence_effect": "reduce_to_medium",
            "conflict_flag": True,
            "override_reason": "Conflicting tier 2 signals - reduced impact",
        }

    impacts = []
    for signal in signals_for_fighter:
        if int(signal.get("tier") or 4) > 2:
            continue
        impact = float(signal.get("probability_impact") or 0.0)
        if signal.get("signal_direction") == "negative":
            impacts.append(-impact)
        elif signal.get("signal_direction") == "positive":
            impacts.append(impact)

    net_impact = max(-0.18, min(0.18, sum(impacts)))
    return {
        "net_impact": net_impact,
        "confidence_effect": "no_change",
        "conflict_flag": False,
        "override_reason": None,
    }


def _find_name_positions(text_lower: str, fighter_name_lower: str) -> list[int]:
    if not fighter_name_lower:
        return []
    positions = []
    search_start = 0
    while True:
        pos = text_lower.find(fighter_name_lower, search_start)
        if pos == -1:
            break
        positions.append(pos)
        search_start = pos + 1
    return positions
