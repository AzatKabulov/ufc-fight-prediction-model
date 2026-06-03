from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import models
from app.schemas import FightRead
from app.services import db_store
from app.services.ufcstats import UfcStatsClient

try:
    from scripts.scrape_fighter_stats import (
        DEFAULT_MIN_MATCH_SCORE,
        FighterRow,
        HttpClient,
        SourceResult,
        build_profile_stats,
        fetch_sherdog_profile,
        fetch_tapology_profile,
        fetch_wikipedia_profile,
    )
except ImportError:  # pragma: no cover - supports running from project root
    from backend.scripts.scrape_fighter_stats import (
        DEFAULT_MIN_MATCH_SCORE,
        FighterRow,
        HttpClient,
        SourceResult,
        build_profile_stats,
        fetch_sherdog_profile,
        fetch_tapology_profile,
        fetch_wikipedia_profile,
    )

log = logging.getLogger(__name__)


def refresh_fight_data(fight: FightRead, db: Session | None = None) -> dict:
    return refresh_fighter_stats_fallback(fight, db)


def refresh_fighter_stats_fallback(fight: FightRead, db: Session | None = None) -> dict:
    if db is None:
        return _seed_refresh(fight)

    refreshed = []
    errors = []
    sources_used: set[str] = set()
    ufcstats_client = UfcStatsClient()
    fallback_client = HttpClient()

    for fighter in [fight.fighter_a, fight.fighter_b]:
        payload = None
        try:
            ufc_payload = ufcstats_client.fetch_fighter_by_name(fighter.name)
            if _payload_has_profile_data(ufc_payload):
                payload = {**ufc_payload, "source": "ufcstats"}
        except Exception as exc:
            log.warning("UFCStats refresh failed for %s: %s", fighter.name, exc)

        if payload is None:
            try:
                payload = _fallback_payload_for_fighter(fighter, fallback_client)
            except Exception as exc:
                log.error("Fallback fighter refresh failed for %s: %s", fighter.name, exc)
                errors.append({"fighter_id": fighter.id, "name": fighter.name, "error": str(exc)})

        if payload is None:
            log.warning("No external fighter stats source matched %s; continuing with cached/median priors", fighter.name)
            errors.append(
                {
                    "fighter_id": fighter.id,
                    "name": fighter.name,
                    "error": "No confident Tapology/Sherdog/Wikipedia match found",
                }
            )
            continue

        try:
            updated = db_store.update_fighter_from_scrape(db, fighter.id, payload)
            sources_used.add(payload.get("source") or "unknown")
            refreshed.append(
                {
                    "fighter_id": fighter.id,
                    "name": updated.name if updated else fighter.name,
                    "source": payload.get("source"),
                    "source_url": payload.get("source_url"),
                    "stats_found": sorted((payload.get("stats") or {}).keys()),
                    "recent_fights_found": len(payload.get("recent_fights") or []),
                }
            )
        except Exception as exc:
            log.error("Could not save refreshed fighter stats for %s: %s", fighter.name, exc)
            errors.append({"fighter_id": fighter.id, "name": fighter.name, "error": str(exc)})

    return {
        "fight_id": fight.id,
        "refreshed": bool(refreshed),
        "sources": sorted(sources_used) or ["fallback_attempted"],
        "fighters": refreshed,
        "errors": errors,
        "message": (
            "Fighter stats refresh completed for matched fighters."
            if refreshed
            else "No external fighter stats source matched. Analysis will use cached data and conservative priors."
        ),
    }


def refresh_single_fighter_stats_fallback(db: Session, fighter_id: str) -> dict:
    fighter = db.get(models.Fighter, fighter_id)
    if fighter is None:
        return {
            "fighter_id": fighter_id,
            "refreshed": False,
            "source": None,
            "error": "fighter_not_found",
        }

    fighter_read = db_store._fighter_to_schema(fighter)
    payload = None
    try:
        ufc_payload = UfcStatsClient().fetch_fighter_by_name(fighter.name)
        if _payload_has_profile_data(ufc_payload):
            payload = {**ufc_payload, "source": "ufcstats"}
    except Exception as exc:
        log.warning("UFCStats single fighter refresh failed for %s: %s", fighter.name, exc)

    if payload is None:
        try:
            payload = _fallback_payload_for_fighter(fighter_read, HttpClient())
        except Exception as exc:
            log.error("Fallback single fighter refresh failed for %s: %s", fighter.name, exc)
            return {
                "fighter_id": fighter.id,
                "name": fighter.name,
                "refreshed": False,
                "source": None,
                "error": str(exc),
            }

    if payload is None:
        return {
            "fighter_id": fighter.id,
            "name": fighter.name,
            "refreshed": False,
            "source": None,
            "error": "no_confident_fallback_match",
        }

    updated = db_store.update_fighter_from_scrape(db, fighter.id, payload)
    return {
        "fighter_id": fighter.id,
        "name": updated.name if updated else fighter.name,
        "refreshed": True,
        "source": payload.get("source"),
        "source_url": payload.get("source_url"),
        "recent_fights_found": len(payload.get("recent_fights") or []),
        "stats_found": sorted((payload.get("stats") or {}).keys()),
    }


def _payload_has_profile_data(payload: dict | None) -> bool:
    if not payload:
        return False
    return bool(
        payload.get("record")
        or payload.get("height_cm")
        or payload.get("reach_cm")
        or payload.get("stats")
        or payload.get("recent_fights")
    )


def _fallback_payload_for_fighter(fighter, client: HttpClient) -> dict | None:
    result = _fetch_ordered_fallback_profile(client, fighter.name, DEFAULT_MIN_MATCH_SCORE)
    if result is None:
        return None

    fighter_row = FighterRow(
        id=fighter.id,
        name=fighter.name,
        record=fighter.record,
        stance=fighter.stance,
        height_cm=fighter.height_cm,
        reach_cm=fighter.reach_cm,
        date_of_birth=fighter.date_of_birth.isoformat() if fighter.date_of_birth else None,
        profile_stats={**(fighter.stats or {}), "recent_fights": [item.model_dump() for item in fighter.recent_fights]},
    )
    profile_stats = build_profile_stats(fighter_row, result)
    return {
        "source": result.source,
        "source_url": result.source_url,
        "name": fighter.name,
        "record": result.record,
        "stance": result.stance,
        "height_cm": result.height_cm,
        "reach_cm": result.reach_cm,
        "date_of_birth": result.date_of_birth,
        "nationality": profile_stats.get("nationality"),
        "weight_lbs": profile_stats.get("weight_lbs"),
        "raw_fight_count": profile_stats.get("raw_fight_count", 0),
        "recent_fights": profile_stats.get("recent_fights") or [],
        "stats": {
            key: value
            for key, value in profile_stats.items()
            if key not in {"recent_fights", "nationality", "weight_lbs"}
        },
        "profile": {
            "matched_name": result.matched_name,
            "match_score": result.match_score,
            "notes": result.notes,
        },
    }


def _fetch_ordered_fallback_profile(
    client: HttpClient,
    fighter_name: str,
    min_score: float,
) -> SourceResult | None:
    for fetcher in (fetch_tapology_profile, fetch_sherdog_profile, fetch_wikipedia_profile):
        try:
            result = fetcher(client, fighter_name, min_score)
        except Exception as exc:
            log.warning("%s failed for %s: %s", fetcher.__name__, fighter_name, exc)
            continue
        if result and result.match_score >= min_score and result.usable:
            return result
    return None


def _seed_refresh(fight: FightRead) -> dict:
    return {
        "fight_id": fight.id,
        "refreshed": True,
        "sources": ["seed-store"],
        "message": (
            "Seed refresh completed. Real implementation should fetch UFC/UFCStats profiles, "
            "write raw snapshots, then normalize changed fighter/fight rows."
        ),
    }
