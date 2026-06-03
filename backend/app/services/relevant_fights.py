from __future__ import annotations

import re

from app.schemas import FightHistoryItem, FightRead, FighterRead


def build_relevant_fight_notes(fight: FightRead, vector: dict[str, float] | None = None, limit: int = 7) -> list[str]:
    notes: list[str] = []
    notes.extend(_common_opponent_notes(fight))
    notes.extend(_style_sample_notes(fight, vector or {}))
    notes.extend(_key_result_notes(fight.fighter_a))
    notes.extend(_key_result_notes(fight.fighter_b))

    if not notes:
        notes.append(f"{fight.fighter_a.name}: refresh fight history for stronger context")
        notes.append(f"{fight.fighter_b.name}: refresh fight history for stronger context")
        notes.append(f"{fight.weight_class} matchup context will improve after UFCStats history is cached")

    return _dedupe(notes)[:limit]


def build_style_summary(fight: FightRead, adjusted_probability: float, vector: dict[str, float] | None = None) -> str:
    v = vector or {}
    favored = fight.fighter_a if adjusted_probability >= 0.5 else fight.fighter_b
    underdog = fight.fighter_b if adjusted_probability >= 0.5 else fight.fighter_a
    favored_tags = _style_tags(favored)
    underdog_tags = _style_tags(underdog)

    paragraphs = []

    # Para 1: how the fight is likely to start / where it takes place
    location = _where_fight_takes_place(favored_tags, underdog_tags, favored, underdog)
    paragraphs.append(location)

    # Para 2: favored fighter's path to winning
    favored_path = _path_to_victory(favored, underdog, favored_tags, underdog_tags, v, is_favored=True)
    if favored_path:
        paragraphs.append(favored_path)

    # Para 3: underdog's route back into it
    underdog_path = _path_to_victory(underdog, favored, underdog_tags, favored_tags, v, is_favored=False)
    if underdog_path:
        paragraphs.append(underdog_path)

    # Para 4: the key X-factor or physical edge
    xfactor = _x_factor(favored, underdog, v)
    if xfactor:
        paragraphs.append(xfactor)

    return " ".join(paragraphs)


def _where_fight_takes_place(favored_tags, underdog_tags, favored, underdog) -> str:
    favored_wants_ground = "grappling threat" in favored_tags
    underdog_wants_ground = "grappling threat" in underdog_tags
    favored_striker = "high-volume striker" in favored_tags or "pressure striker" in favored_tags
    underdog_striker = "high-volume striker" in underdog_tags or "pressure striker" in underdog_tags

    if favored_wants_ground and not underdog_wants_ground:
        return (
            f"Expect {favored.name} to push for the takedown early -- that's where the stats say the edge is biggest. "
            f"{underdog.name} will need to keep this on the feet to have the best chance."
        )
    if underdog_wants_ground and not favored_wants_ground:
        return (
            f"{favored.name} wants this fight on the feet -- that's where the numbers look best. "
            f"The key question is whether {underdog.name} can drag it to the mat, where they're the more dangerous fighter."
        )
    if favored_striker and underdog_striker:
        return (
            f"Both fighters want to stand and trade -- this could be a striking war from the opening bell. "
            f"{favored.name} has the edge in output, but {underdog.name} isn't afraid to exchange."
        )
    if favored_wants_ground and underdog_wants_ground:
        return (
            f"Two fighters who like to grapple -- the wrestling battle in the first round could set the tone for everything that follows. "
            f"Whoever establishes top control early is in a strong position."
        )
    # Neither fighter has strong style tags -- use raw output numbers to differentiate
    favored_landed = _number((favored.stats or {}).get("strikes_landed_per_min"))
    underdog_landed = _number((underdog.stats or {}).get("strikes_landed_per_min"))
    if favored_landed >= underdog_landed + 0.8:
        return (
            f"{favored.name} is the busier fighter on paper -- higher striking output per minute "
            f"and the stats give them the advantage in a stand-up fight. "
            f"{underdog.name} will need to change the location or absorb a lot of volume to compete."
        )
    return (
        f"This is a well-matched fight on paper. "
        f"The stats don't show a big stylistic edge either way, which means execution and fight-week condition "
        f"could matter more than the numbers in this one."
    )


def _path_to_victory(fighter, opponent, my_tags, opp_tags, v: dict, is_favored: bool) -> str | None:
    parts = []

    if "high-volume striker" in my_tags or "pressure striker" in my_tags:
        parts.append(f"{'The gameplan for' if is_favored else 'For'} {fighter.name}: get the jab going, walk {opponent.name} down, and let the volume do the work.")
    if "grappling threat" in my_tags and "strong takedown defense" not in opp_tags:
        parts.append(f"{'The path is clear' if is_favored else 'The upset route'}: get the fight to the mat, where {fighter.name} is the much more dangerous fighter.")
    if "finishing threat" in my_tags and is_favored:
        parts.append(f"And if {fighter.name} gets the finish? Don't be shocked -- the finishing rate in recent fights is well above average.")
    if "finishing threat" in my_tags and not is_favored:
        parts.append(f"{fighter.name} only needs one clean shot or one submission attempt to flip this fight. Don't write them off.")

    if not parts and is_favored:
        # Generic favored path when no strong style tag fires
        parts.append(
            f"For {fighter.name}, the path is straightforward: control the pace, stay disciplined, "
            f"and let the stat advantages compound over the rounds. No need to force anything."
        )

    if not parts and not is_favored:
        # Generic underdog path
        parts.append(
            f"{fighter.name}'s best shot is an early statement -- either a big shot on the feet or getting the fight to an uncomfortable place for {opponent.name}. "
            f"Letting this go to the scorecards probably doesn't end well."
        )

    return " ".join(parts) if parts else None


def _x_factor(favored, underdog, v: dict) -> str | None:
    reach_diff = v.get("reach_cm_diff", 0)
    damage_delta = v.get("recent_damage_absorbed_delta", 0)
    kd_delta = v.get("recent_knockdown_absorbed_delta", 0)

    if abs(reach_diff) >= 7:
        longer = favored if reach_diff > 0 else underdog
        shorter = underdog if reach_diff > 0 else favored
        return (
            f"Reach is the physical wild card here -- {longer.name} has a meaningful inch advantage, "
            f"which matters in a striking fight. {shorter.name} will need to get inside to neutralize it."
        )
    if abs(damage_delta) >= 30:
        more_hit = favored if damage_delta > 0 else underdog
        return (
            f"Something to watch: {more_hit.name} has been eating more shots than usual in recent fights. "
            f"If the chin gets tested tonight, that's a data point worth remembering."
        )
    if abs(kd_delta) >= 0.5:
        dropped = favored if kd_delta > 0 else underdog
        return (
            f"{dropped.name} has been dropped in recent outings -- the durability question is real, "
            f"especially against a fighter with finishing ability."
        )
    strongest_edge = _strongest_edge(v)
    if strongest_edge:
        return f"The cleanest model signal is {strongest_edge}, so that is the first matchup lever to track."
    return None


def _style_description(tags: list[str]) -> str:
    mapping = {
        "high-volume striker": "heavy hands and constant striking output",
        "pressure striker": "a relentless forward pressure game",
        "grappling threat": "dangerous grappling and takedown offense",
        "strong takedown defense": "solid takedown defense",
        "finishing threat": "a high fight-ending rate",
        "positive striking differential": "a positive striking differential",
    }
    phrases = [mapping.get(tag) for tag in tags[:2] if mapping.get(tag)]
    if not phrases:
        return ""
    return " and ".join(phrases)


def _key_question(
    favored,
    underdog,
    favored_tags: list[str],
    underdog_tags: list[str],
    vector: dict[str, float],
) -> str:
    if "grappling threat" in underdog_tags and "strong takedown defense" not in favored_tags:
        return f"The big question: can {favored.name} keep this standing, or does {underdog.name} drag it to the mat?"
    if "grappling threat" in favored_tags and "strong takedown defense" in underdog_tags:
        return f"Watch for the wrestling battle — {underdog.name}'s takedown defense will be tested early."
    if "finishing threat" in underdog_tags:
        return f"{underdog.name} only needs one moment to change the outcome, so the margin for error is slim."
    if abs(vector.get("reach_cm_diff", 0)) >= 5:
        longer = favored if vector.get("reach_cm_diff", 0) > 0 else underdog
        return f"Reach could be the deciding factor — {longer.name} will look to use that distance edge all night."
    return f"Execution matters more than the numbers here — whoever imposes their game plan early likely takes it."


def _common_opponent_notes(fight: FightRead) -> list[str]:
    a_history = _history_by_opponent(fight.fighter_a)
    b_history = _history_by_opponent(fight.fighter_b)
    common = sorted(set(a_history) & set(b_history))
    notes = []
    for opponent_key in common[:2]:
        a_item = a_history[opponent_key]
        b_item = b_history[opponent_key]
        opponent = a_item.opponent or b_item.opponent or "a common opponent"
        a_won = a_item.result.lower().startswith("win")
        b_won = b_item.result.lower().startswith("win")
        if a_won and not b_won:
            note = (
                f"Both have fought {opponent} — {fight.fighter_a.name} beat them ({_method_text(a_item)}), "
                f"{fight.fighter_b.name} lost ({_method_text(b_item)}). Edge to {fight.fighter_a.name} on shared tape."
            )
        elif b_won and not a_won:
            note = (
                f"Both have fought {opponent} — {fight.fighter_b.name} beat them ({_method_text(b_item)}), "
                f"{fight.fighter_a.name} lost ({_method_text(a_item)}). Edge to {fight.fighter_b.name} on shared tape."
            )
        else:
            note = (
                f"Both have fought {opponent}. {fight.fighter_a.name} went {a_item.result} ({_method_text(a_item)}), "
                f"{fight.fighter_b.name} went {b_item.result} ({_method_text(b_item)})."
            )
        notes.append(f"Common opponent: {note}")
    return notes


def _style_sample_notes(fight: FightRead, vector: dict[str, float]) -> list[str]:
    notes = [
        _style_note_for(fight.fighter_a, fight.fighter_b),
        _style_note_for(fight.fighter_b, fight.fighter_a),
    ]

    if abs(vector.get("recent_damage_absorbed_delta", 0.0)) >= 25:
        damaged = fight.fighter_a if vector["recent_damage_absorbed_delta"] > 0 else fight.fighter_b
        notes.append(f"{damaged.name} has been absorbing more punishment than usual in recent fights — the chin is a factor to watch.")

    if abs(vector.get("recent_knockdown_absorbed_delta", 0.0)) >= 0.5:
        exposed = fight.fighter_a if vector["recent_knockdown_absorbed_delta"] > 0 else fight.fighter_b
        notes.append(f"{exposed.name} has been dropped more recently — durability is a real question against a finisher.")

    return [_label_style_note(note) for note in notes if note]


def _label_style_note(note: str) -> str:
    if note.startswith(("Grappling sample:", "Volume-striking sample:", "Finish signal:")):
        return note
    if "taken down" in note:
        return f"Grappling sample: {note}"
    if "absorbed" in note or "absorbing more punishment" in note:
        return f"Volume-striking sample: {note}"
    if "finished before" in note:
        return f"Finish signal: {note}"
    return note


def _style_note_for(fighter: FighterRead, opponent: FighterRead) -> str | None:
    opponent_tags = _style_tags(opponent)
    if "grappling threat" in opponent_tags:
        sample = _highest(fighter.recent_fights, "takedowns_against")
        if sample and (sample.takedowns_against or 0) >= 2:
            return (
                f"{fighter.name} was taken down {sample.takedowns_against} times by "
                f"{sample.opponent or 'a recent opponent'} ({_result_text(sample)}). "
                f"Grappling defense is something to watch against {opponent.name}."
            )

    if "high-volume striker" in opponent_tags or "pressure striker" in opponent_tags:
        sample = _highest(fighter.recent_fights, "sig_strikes_against")
        if sample and (sample.sig_strikes_against or 0) >= 70:
            return (
                f"{fighter.name} absorbed {sample.sig_strikes_against} significant strikes against "
                f"{sample.opponent or 'a recent opponent'} ({_result_text(sample)}). "
                f"Their ability to take volume is relevant here."
            )

    if "finishing threat" in opponent_tags:
        loss = next((item for item in fighter.recent_fights if item.result.lower().startswith("loss") and _is_finish(item.method)), None)
        if loss:
            return (
                f"{fighter.name} has been finished before — {loss.opponent or 'a recent opponent'} "
                f"got them via {_method_text(loss)}. Worth keeping in mind against a finisher."
            )

    return None


def _key_result_notes(fighter: FighterRead) -> list[str]:
    notes = []
    finish_win = next((item for item in fighter.recent_fights if item.result.lower().startswith("win") and _is_finish(item.method)), None)
    if finish_win:
        notes.append(
            f"{fighter.name} has real finishing ability — last stoppage win was over "
            f"{finish_win.opponent or 'a recent opponent'} by {_method_text(finish_win)}."
        )

    loss = next((item for item in fighter.recent_fights if item.result.lower().startswith("loss")), None)
    if loss:
        notes.append(
            f"{fighter.name}'s most recent loss came against "
            f"{loss.opponent or 'a recent opponent'} by {_method_text(loss)}."
        )
    return [f"finish signal: {note}" if "finishing ability" in note else note for note in notes]


def _style_tags(fighter: FighterRead) -> list[str]:
    stats = fighter.stats or {}
    tags = []
    landed = _number(stats.get("strikes_landed_per_min"))
    absorbed = _number(stats.get("strikes_absorbed_per_min"))
    td_avg = _number(stats.get("td_avg_per_15"))
    sub_avg = _number(stats.get("sub_avg_per_15"))
    td_def = _number(stats.get("td_def"))
    finish_rate = _recent_finish_rate(fighter.recent_fights) or _number(stats.get("finish_rate"))

    if landed >= 5.0:
        tags.append("high-volume striker")
    if landed >= 4.0 and absorbed >= 4.0:
        tags.append("pressure striker")
    if td_avg >= 1.25 or sub_avg >= 0.6:
        tags.append("grappling threat")
    if td_def >= 0.75:
        tags.append("strong takedown defense")
    if finish_rate >= 0.45:
        tags.append("finishing threat")
    if not tags and landed > absorbed:
        tags.append("positive striking differential")
    return tags


def _strongest_edge(vector: dict[str, float]) -> str | None:
    labels = {
        "striking_output_diff": "striking differential",
        "takedown_defense_diff": "takedown defense",
        "recent_win_rate_diff": "recent form",
        "recent_finish_rate_diff": "recent finishing form",
        "recent_sig_strike_diff_delta": "recent significant-strike margin",
        "reach_cm_diff": "reach profile",
    }
    ranked = sorted(
        ((abs(value), labels.get(key)) for key, value in vector.items() if labels.get(key)),
        reverse=True,
    )
    return next((label for value, label in ranked if value > 0), None)


def _history_by_opponent(fighter: FighterRead) -> dict[str, FightHistoryItem]:
    rows: dict[str, FightHistoryItem] = {}
    for item in fighter.recent_fights:
        key = _normalize_name(item.opponent or "")
        if key and key not in rows:
            rows[key] = item
    return rows


def _method_text(item: FightHistoryItem) -> str:
    method = item.method or "method unknown"
    date = f", {item.date}" if item.date else ""
    return f"{method}{date}"


def _result_text(item: FightHistoryItem) -> str:
    return f"{item.result} by {_method_text(item)}"


def _highest(items: list[FightHistoryItem], key: str) -> FightHistoryItem | None:
    if not items:
        return None
    return max(items, key=lambda item: float(getattr(item, key) or 0))


def _recent_finish_rate(items: list[FightHistoryItem]) -> float:
    if not items:
        return 0.0
    return sum(1 for item in items if _is_finish(item.method)) / len(items)


def _is_finish(method: str | None) -> bool:
    if not method:
        return False
    normalized = method.lower()
    return "ko" in normalized or "tko" in normalized or "sub" in normalized


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
