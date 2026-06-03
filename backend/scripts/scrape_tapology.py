"""
Tapology fighter scraper — fills profile_stats + recent_fights for fighters
on upcoming UFC cards who are missing that data.

Run from backend/ folder:
    .venv/Scripts/python.exe scripts/scrape_tapology.py

Writes results to SQLite directly. Safe to re-run — skips fighters that
already have fight history unless --force is passed.
"""
from __future__ import annotations

import re
import sys
import time
import json
import logging
from difflib import SequenceMatcher
from typing import Any

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

# make sure app/ is importable
sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app import models
from app.services import db_store

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
DELAY = 1.5  # seconds between requests


# ── Tapology search + profile fetch ──────────────────────────────────────────

def search_tapology(name: str) -> str | None:
    """Return the best-matching Tapology fighter URL for a given name."""
    url = f"https://www.tapology.com/search?term={requests.utils.quote(name)}&context=fighters"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
    except Exception as e:
        log.warning("  Search failed for %s: %s", name, e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.select('a[href*="/fightcenter/fighters/"]')

    best_score = 0.0
    best_href = None
    name_norm = _norm(name)

    for a in links:
        href = a.get("href", "")
        text = _norm(a.get_text(strip=True))
        # Strip nickname in quotes from text
        text = re.sub(r'"[^"]+"', '', text).strip()
        score = SequenceMatcher(None, name_norm, text).ratio()
        if score > best_score:
            best_score = score
            best_href = href

    if best_score >= 0.55 and best_href:
        return "https://www.tapology.com" + best_href
    return None


def fetch_tapology_profile(url: str, fighter_name: str) -> dict[str, Any] | None:
    """Scrape a Tapology fighter profile page and return structured data."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
    except Exception as e:
        log.warning("  Profile fetch failed: %s", e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    profile = _parse_profile_details(lines, soup)
    fights = _parse_fight_history(lines, fighter_name)

    if not fights:
        return None

    return {**profile, "recent_fights": fights[:10]}


def _parse_profile_details(lines: list[str], soup: BeautifulSoup) -> dict[str, Any]:
    """Extract height, reach, weight, record from page text."""
    full_text = "\n".join(lines)
    result: dict[str, Any] = {}

    # Height: "5'10\"" or "170cm"
    m = re.search(r"(\d)'(\d{1,2})\"", full_text)
    if m:
        feet, inches = int(m.group(1)), int(m.group(2))
        result["height_cm"] = round((feet * 12 + inches) * 2.54, 1)

    # Reach
    m = re.search(r'(\d{2,3})\.?\d*"\s*(?:reach|\n)', full_text, re.IGNORECASE)
    if m:
        result["reach_cm"] = round(float(m.group(1)) * 2.54, 1)

    # Weight
    m = re.search(r"(\d{3})\.?\d*\s*(?:lbs?|Lightweight|Welterweight|Heavyweight|Middleweight|Featherweight|Bantamweight|Flyweight|Strawweight)", full_text)
    if m:
        result["weight_lbs"] = float(m.group(1))

    # Record
    m = re.search(r"(\d{1,3})-(\d{1,3})-(\d{1,2})\s*\(Win-Loss", full_text)
    if m:
        result["record"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Nationality / country
    m = re.search(r"Born:\s*(.+?)(?:\n|$)", full_text)
    if m:
        born = m.group(1).strip()
        # Extract country from "City, State, Country" pattern
        parts = [p.strip() for p in born.split(",")]
        if parts:
            result["nationality"] = parts[-1]

    # Stance
    for stance in ("Orthodox", "Southpaw", "Switch"):
        if stance in full_text:
            result["stance"] = stance
            break

    return result


def _parse_fight_history(lines: list[str], fighter_name: str) -> list[dict]:
    """Parse fight history from Tapology page lines. Skips amateur bouts."""
    fights = []
    fighter_norm = _norm(fighter_name)

    # Find pro record section — skip past "amateur bouts" label if present
    start_idx = 0
    for idx, line in enumerate(lines):
        if "Pro MMA" in line:
            start_idx = idx
            break

    i = start_idx
    in_amateur = False

    while i < len(lines) and len(fights) < 10:
        line = lines[i]

        # Track amateur section to skip it
        if "amateur" in line.lower() and "bout" in line.lower():
            in_amateur = True
        if in_amateur and line in ("W", "L", "D", "NC"):
            i += 6
            continue
        # Reset amateur flag when we see a real promoter name
        if in_amateur and any(p in line for p in ("UFC", "Bellator", "PFL", "ONE ", "Invicta", "LFA", "CFFC")):
            in_amateur = False

        if line not in ("W", "L", "D", "NC"):
            i += 1
            continue

        result = {"W": "win", "L": "loss", "D": "draw", "NC": "NC"}.get(line, line)

        method_short = ""
        opponent = ""
        method_detail = ""
        event_name = ""
        date_str = ""
        round_str = ""
        time_str = ""

        window = lines[i+1:i+22] if i+22 < len(lines) else lines[i+1:]

        for wline in window:
            if wline in ("TKO", "KO", "DEC", "SUB", "NC", "DQ") and not method_short:
                method_short = wline
            elif (not opponent
                  and len(wline) > 3
                  and not re.search(r'^\d', wline)
                  and wline not in ("TKO","KO","DEC","SUB","NC","DQ","Win","Loss","Draw",
                                    "Upcoming","Cancelled","Strikes","Ground","Clinch")
                  and len(wline.split()) >= 2
                  and _norm(wline) != fighter_norm
                  and not re.match(r'\d+-\d+', wline)):
                opponent = wline.split("\n")[0]
            elif re.search(r'R\d|Round\s*\d|Decision|Unanimous|Split|Majority|Overtime', wline) and not method_detail:
                method_detail = wline
                rm = re.search(r'R(\d)', wline)
                if rm:
                    round_str = rm.group(1)
                tm = re.search(r'(\d:\d{2})', wline)
                if tm:
                    time_str = tm.group(1)
            elif any(p in wline for p in ("UFC","Bellator","PFL","ONE ","Invicta","LFA","CFFC","DWCS","Road to UFC")) and len(wline) > 5 and not event_name:
                event_name = wline
            elif not date_str:
                dm = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{4}', wline)
                if dm:
                    date_str = dm.group(0)
                else:
                    dm2 = re.search(r'(\d{4})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})', wline)
                    if dm2:
                        date_str = f"{dm2.group(2)} {dm2.group(3)} {dm2.group(1)}"

        method = _normalize_method(method_short, method_detail)

        if opponent:
            fights.append({
                "result": result,
                "opponent": opponent,
                "event": event_name or None,
                "date": date_str or None,
                "method": method,
                "round": round_str or None,
                "time": time_str or None,
                "knockdowns_for": None,
                "knockdowns_against": None,
                "sig_strikes_for": None,
                "sig_strikes_against": None,
                "takedowns_for": None,
                "takedowns_against": None,
                "sub_attempts_for": None,
                "sub_attempts_against": None,
            })

        i += 6

    return fights


def _normalize_method(short: str, detail: str) -> str:
    combined = (short + " " + detail).upper()
    if "TKO" in combined or "KO" in combined:
        if "TKO" in combined:
            return "TKO"
        return "KO/TKO"
    if "SUB" in combined or "CHOKE" in detail.upper() or "LOCK" in detail.upper() or "BAR" in detail.upper():
        return "Submission"
    if "DEC" in combined or "UNANIMOUS" in combined or "SPLIT" in combined or "MAJORITY" in combined:
        if "SPLIT" in combined:
            return "Decision/Split"
        if "MAJORITY" in combined:
            return "Decision/Majority"
        return "Decision/Unanimous"
    if "DQ" in combined:
        return "DQ"
    if "NC" in combined:
        return "NC"
    if detail:
        return detail[:40]
    return short or "Unknown"


def _compute_derived_stats(fights: list[dict]) -> dict[str, Any]:
    """Compute finish rate, ko_win_rate etc from fight history."""
    wins = [f for f in fights if f["result"] == "win"]
    losses = [f for f in fights if f["result"] == "loss"]
    total_wins = len(wins)
    total_losses = len(losses)
    total = max(len(fights), 1)

    ko_wins = sum(1 for f in wins if f["method"] in ("KO/TKO", "TKO"))
    sub_wins = sum(1 for f in wins if f["method"] == "Submission")
    dec_wins = sum(1 for f in wins if "Decision" in (f["method"] or ""))
    ko_losses = sum(1 for f in losses if f["method"] in ("KO/TKO", "TKO"))

    return {
        "raw_fight_count": len(fights),
        "finish_rate": round((ko_wins + sub_wins) / max(total_wins, 1), 3),
        "ko_win_rate": round(ko_wins / max(total_wins, 1), 3),
        "sub_win_rate": round(sub_wins / max(total_wins, 1), 3),
        "decision_win_rate": round(dec_wins / max(total_wins, 1), 3),
        "ko_loss_rate": round(ko_losses / max(total, 1), 3),
        "total_wins": total_wins,
        "total_losses": total_losses,
    }


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


# ── Main scrape loop ──────────────────────────────────────────────────────────

def scrape_upcoming_card_fighters(force: bool = False) -> None:
    db = SessionLocal()
    try:
        events = db_store.list_upcoming_events(db)

        # Collect unique fighters missing data
        to_scrape: list[models.Fighter] = []
        seen_ids: set[str] = set()

        for event in events:
            for fight in event.fights:
                for fighter_schema in [fight.fighter_a, fight.fighter_b]:
                    if fighter_schema.id in seen_ids:
                        continue
                    seen_ids.add(fighter_schema.id)

                    fighter_row = db.get(models.Fighter, fighter_schema.id)
                    if not fighter_row:
                        continue

                    existing_stats = fighter_row.profile_stats or {}
                    has_history = bool(existing_stats.get("recent_fights"))
                    has_stats = len(existing_stats) >= 4

                    if force or not has_history:
                        to_scrape.append(fighter_row)

        log.info("Fighters to scrape: %d", len(to_scrape))

        passed = 0
        failed = 0
        failed_names = []

        for idx, fighter in enumerate(to_scrape):
            log.info("[%d/%d] %s", idx+1, len(to_scrape), fighter.name)
            time.sleep(DELAY)

            profile_url = search_tapology(fighter.name)
            if not profile_url:
                log.info("  NOT FOUND on Tapology")
                failed += 1
                failed_names.append(fighter.name)
                continue

            log.info("  Found: %s", profile_url)
            time.sleep(DELAY)

            data = fetch_tapology_profile(profile_url, fighter.name)
            if not data or not data.get("recent_fights"):
                log.info("  Could not parse fight history")
                failed += 1
                failed_names.append(fighter.name)
                continue

            fights = data["recent_fights"]
            derived = _compute_derived_stats(fights)

            # Merge into existing profile_stats
            existing = dict(fighter.profile_stats or {})
            existing["recent_fights"] = fights
            existing.update({k: v for k, v in derived.items() if k not in existing or not existing[k]})

            # Update physical attributes only if missing
            if data.get("height_cm") and not fighter.height_cm:
                fighter.height_cm = data["height_cm"]
            if data.get("reach_cm") and not fighter.reach_cm:
                fighter.reach_cm = data["reach_cm"]
            if data.get("record") and not fighter.record:
                fighter.record = data["record"]
            if data.get("stance") and not fighter.stance:
                fighter.stance = data["stance"]
            if data.get("nationality"):
                existing["nationality"] = data["nationality"]
            if data.get("weight_lbs"):
                existing["weight_lbs"] = data["weight_lbs"]

            fighter.profile_stats = existing
            db.add(fighter)
            db.commit()

            ko_wins = sum(1 for f in fights if f["method"] in ("KO/TKO", "TKO"))
            sub_wins = sum(1 for f in fights if f["method"] == "Submission")
            dec_wins = sum(1 for f in fights if "Decision" in (f["method"] or ""))
            log.info("  Saved %d fights (KO:%d Sub:%d Dec:%d)", len(fights), ko_wins, sub_wins, dec_wins)
            passed += 1

        log.info("")
        log.info("=== DONE: %d scraped, %d failed ===", passed, failed)
        if failed_names:
            log.info("Failed: %s", ", ".join(failed_names))
            with open("scripts/scrape_failed.txt", "w") as f:
                f.write("\n".join(failed_names))

    finally:
        db.close()


if __name__ == "__main__":
    force = "--force" in sys.argv
    scrape_upcoming_card_fighters(force=force)
