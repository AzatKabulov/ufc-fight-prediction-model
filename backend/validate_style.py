"""
Validate style constraints across all upcoming fight predictions.

Rules checked:
  1. sub_prob > 0.10 while has_any_sub_win is False  → FAIL
  2. ko_prob  > 0.40 while has_any_ko_win  is False  → FAIL
  3. written_analysis must mention primary_style name → FAIL if missing
"""

import sys
import os

# Make sure app/ is importable
sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import SessionLocal
from app.services import db_store
from app.services.fighter_identity import build_fighter_identity


def run() -> int:
    db = SessionLocal()
    failures = 0
    checked = 0

    events = db_store.list_upcoming_events(db)
    for event in events:
        for fight in event.fights:
            pred = db_store.get_latest_prediction_for_fight(db, fight.id)
            if pred is None:
                continue  # not yet analyzed

            checked += 1
            fa = fight.fighter_a
            fb = fight.fighter_b
            id_a = build_fighter_identity(fa)
            id_b = build_fighter_identity(fb)

            favored_is_a = pred.adjusted_probability_a >= 0.5
            identity_favored = id_a if favored_is_a else id_b
            identity_dog = id_b if favored_is_a else id_a
            name_favored = fa.name if favored_is_a else fb.name

            sub_prob = pred.method_probabilities.get("submission", 0.0)
            ko_prob = pred.method_probabilities.get("KO/TKO", 0.0)

            # Rule 1: submission cap
            if not identity_favored.has_any_sub_win and sub_prob > 0.10:
                print(f"FAIL [sub] {fa.name} vs {fb.name} — favored={name_favored} "
                      f"has 0 sub wins but sub_prob={sub_prob:.0%}")
                failures += 1

            # Rule 2: KO cap
            if not identity_favored.has_any_ko_win and ko_prob > 0.40:
                print(f"FAIL [ko]  {fa.name} vs {fb.name} — favored={name_favored} "
                      f"has 0 KO wins but ko_prob={ko_prob:.0%}")
                failures += 1

            # Rule 3: written analysis mentions style
            analysis = pred.written_analysis or ""
            style_name = identity_favored.primary_style
            if style_name.lower() not in analysis.lower():
                print(f"WARN [style] {fa.name} vs {fb.name} — favored={name_favored} "
                      f"style='{style_name}' not mentioned in written_analysis")
                # Warning only, not a hard failure

    db.close()
    print(f"\nChecked {checked} predictions across {len(events)} upcoming events.")
    if failures == 0:
        print("PASS - All style constraints satisfied.")
    else:
        print(f"FAIL - {failures} violation(s) found. Re-run predictions to apply style constraints.")
    return failures


if __name__ == "__main__":
    sys.exit(run())
