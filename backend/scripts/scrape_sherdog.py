"""
Sherdog fighter scraper — gets full career fight history for fighters
on upcoming UFC cards who are missing that data.

Run from backend/ folder:
    .venv/Scripts/python.exe scripts/scrape_sherdog.py [--force] [--all]

--force: re-scrape even fighters that already have fight history
--all:   scrape ALL fighters in DB, not just upcoming card fighters
"""
from __future__ import annotations

import re
import sys
import time
import logging
from difflib import SequenceMatcher
from typing import Any

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
DELAY = 1.8


# ── Sherdog search + scrape ───────────────────────────────────────────────────

def search_sherdog(name: str) -> str | None:
    url = f"https://www.sherdog.com/stats/fightfinder?SearchTxt={requests.utils.quote(name)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
    except Exception as e:
        log.warning("  Sherdog search failed: %s", e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.find_all("a", href=re.compile(r"/fighter/"))
    name_norm = _norm(name)

    best_score = 0.0
    best_href = None
    for a in links:
        text = _norm(a.get_text(strip=True))
        score = SequenceMatcher(None, name_norm, text).ratio()
        if score > best_score:
            best_score = score
            best_href = a.get("href", "")

    if best_score >= 0.55 and best_href:
        if not best_href.startswith("http"):
            best_href = "https://www.sherdog.com" + best_href
        return best_href
    return None


def fetch_sherdog_profile(url: str) -> dict[str, Any] | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
    except Exception as e:
        log.warning("  Sherdog profile fetch failed: %s", e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    profile = _parse_sherdog_bio(soup)
    fights = _parse_sherdog_fights(soup)

    if not fights:
        return None

    return {**profile, "recent_fights": fights}


def _parse_sherdog_bio(soup: BeautifulSoup) -> dict[str, Any]:
    result: dict[str, Any] = {}
    text = soup.get_text(" ", strip=True)

    # Record: "25-5-0" from win/loss summary block
    m = re.search(r"Wins\s+(\d+).*?Losses\s+(\d+)", text, re.DOTALL)
    if m:
        total_wins = int(m.group(1))
        total_losses = int(m.group(2))
        result["record"] = f"{total_wins}-{total_losses}-0"

    # Height: 5'10"
    m = re.search(r"(\d)'(\d{1,2})\"", text)
    if m:
        result["height_cm"] = round((int(m.group(1)) * 12 + int(m.group(2))) * 2.54, 1)

    # Reach
    m = re.search(r'(\d{2,3}(?:\.\d)?)".*?reach', text, re.IGNORECASE)
    if m:
        result["reach_cm"] = round(float(m.group(1)) * 2.54, 1)

    # Weight
    m = re.search(r"(\d{3})\s*lbs", text)
    if m:
        result["weight_lbs"] = float(m.group(1))

    # Nationality
    m = re.search(r"Nationality\s+([A-Za-z\s]+?)(?:\n|$|Height)", text)
    if m:
        result["nationality"] = m.group(1).strip()

    # Stance
    for stance in ("Orthodox", "Southpaw", "Switch"):
        if stance in text:
            result["stance"] = stance
            break

    return result


def _parse_sherdog_fights(soup: BeautifulSoup) -> list[dict]:
    """Parse the PRO fight history table from Sherdog."""
    fights = []

    # Sherdog has multiple fight tables — first is pro record, then amateur
    tables = soup.find_all("table", class_=re.compile(r"fight|result", re.I))
    if not tables:
        # fallback: any table with fight headers
        for t in soup.find_all("table"):
            headers_row = t.find("tr")
            if headers_row:
                headers_text = headers_row.get_text(" ", strip=True)
                if "Result" in headers_text and "Fighter" in headers_text:
                    tables.append(t)

    if not tables:
        return []

    # Use first table = pro record
    table = tables[0]
    rows = table.find_all("tr")[1:]  # skip header row

    for row in rows[:15]:
        cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
        if not cells or len(cells) < 4:
            continue

        result_raw = cells[0].strip().lower()
        if result_raw not in ("win", "loss", "draw", "nc", "no contest"):
            continue

        result = "win" if result_raw == "win" else "loss" if result_raw == "loss" else "draw" if result_raw == "draw" else "NC"
        opponent = cells[1].strip()
        event_date_raw = cells[2].strip() if len(cells) > 2 else ""
        method_raw = cells[3].strip() if len(cells) > 3 else ""
        round_str = cells[4].strip() if len(cells) > 4 else ""
        time_str = cells[5].strip() if len(cells) > 5 else ""

        # Parse event and date
        event_name = ""
        date_str = ""
        dm = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*/\s*(\d{1,2})\s*/\s*(\d{4})', event_date_raw)
        if dm:
            date_str = f"{dm.group(1)} {dm.group(2)} {dm.group(3)}"
            event_name = event_date_raw[:event_date_raw.find(dm.group(0))].strip()
        else:
            event_name = event_date_raw

        # Clean event name (remove trailing numbers/dates)
        event_name = re.sub(r'\s*\d{4}\s*$', '', event_name).strip()

        # Normalize method
        method = _normalize_method(method_raw)

        # Clean round
        round_clean = re.search(r'\d', round_str)
        round_clean = round_clean.group(0) if round_clean else None

        # Skip obvious non-fights
        if not opponent or opponent in ("Fighter", "Result"):
            continue

        fights.append({
            "result": result,
            "opponent": opponent,
            "event": event_name[:80] if event_name else None,
            "date": date_str or None,
            "method": method,
            "round": round_clean,
            "time": time_str if re.match(r'\d:\d{2}', time_str) else None,
            "knockdowns_for": None,
            "knockdowns_against": None,
            "sig_strikes_for": None,
            "sig_strikes_against": None,
            "takedowns_for": None,
            "takedowns_against": None,
            "sub_attempts_for": None,
            "sub_attempts_against": None,
        })

    return fights


def _normalize_method(raw: str) -> str:
    r = raw.upper()
    if "TKO" in r or "TECHNICAL" in r:
        return "TKO"
    if "KO" in r or "KNOCK" in r:
        return "KO/TKO"
    if "SUBMISSION" in r or "CHOKE" in r or "LOCK" in r or "BAR" in r or "TRIANGLE" in r:
        return "Submission"
    if "SPLIT" in r:
        return "Decision/Split"
    if "MAJORITY" in r:
        return "Decision/Majority"
    if "DECISION" in r or "UNANIMOUS" in r:
        return "Decision/Unanimous"
    if "DQ" in r or "DISQUALIF" in r:
        return "DQ"
    if "NO CONTEST" in r or "NC" == r.strip():
        return "NC"
    if "DRAW" in r:
        return "Draw"
    return raw[:40] if raw else "Unknown"


def _compute_derived_stats(fights: list[dict]) -> dict[str, Any]:
    wins = [f for f in fights if f["result"] == "win"]
    losses = [f for f in fights if f["result"] == "loss"]
    total_wins = len(wins)
    total = max(len(fights), 1)

    ko_wins = sum(1 for f in wins if f["method"] in ("KO/TKO", "TKO"))
    sub_wins = sum(1 for f in wins if f["method"] == "Submission")
    dec_wins = sum(1 for f in wins if "Decision" in (f["method"] or ""))
    ko_losses = sum(1 for f in losses if f["method"] in ("KO/TKO", "TKO"))
    sub_losses = sum(1 for f in losses if f["method"] == "Submission")

    return {
        "raw_fight_count": len(fights),
        "finish_rate": round((ko_wins + sub_wins) / max(total_wins, 1), 3),
        "ko_win_rate": round(ko_wins / max(total_wins, 1), 3),
        "sub_win_rate": round(sub_wins / max(total_wins, 1), 3),
        "decision_win_rate": round(dec_wins / max(total_wins, 1), 3),
        "ko_loss_rate": round(ko_losses / max(total, 1), 3),
        "sub_loss_rate": round(sub_losses / max(total, 1), 3),
        "total_wins": total_wins,
        "total_losses": len(losses),
    }


def _norm(s: str) -> str:
    s = re.sub(r'"[^"]+"', '', s)  # strip nicknames
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


# ── Main loop ─────────────────────────────────────────────────────────────────

def scrape_fighters(force: bool = False, scrape_all: bool = False) -> None:
    db = SessionLocal()
    try:
        if scrape_all:
            fighter_rows = db.execute(select(models.Fighter)).scalars().all()
        else:
            events = db_store.list_upcoming_events(db)
            seen_ids: set[str] = set()
            fighter_rows = []
            for event in events:
                for fight in event.fights:
                    for fs in [fight.fighter_a, fight.fighter_b]:
                        if fs.id not in seen_ids:
                            seen_ids.add(fs.id)
                            row = db.get(models.Fighter, fs.id)
                            if row:
                                fighter_rows.append(row)

        to_scrape = []
        for row in fighter_rows:
            existing = row.profile_stats or {}
            has_history = bool(existing.get("recent_fights"))
            if force or not has_history:
                to_scrape.append(row)

        log.info("Fighters to scrape: %d", len(to_scrape))

        passed = failed = 0
        failed_names = []

        for idx, fighter in enumerate(to_scrape):
            log.info("[%d/%d] %s", idx + 1, len(to_scrape), fighter.name)
            time.sleep(DELAY)

            sherdog_url = search_sherdog(fighter.name)
            if not sherdog_url:
                log.info("  NOT FOUND on Sherdog")
                failed += 1
                failed_names.append(fighter.name)
                continue

            log.info("  Found: %s", sherdog_url)
            time.sleep(DELAY)

            data = fetch_sherdog_profile(sherdog_url)
            if not data or not data.get("recent_fights"):
                log.info("  Could not parse fight history")
                failed += 1
                failed_names.append(fighter.name)
                continue

            fights = data["recent_fights"]
            derived = _compute_derived_stats(fights)

            existing = dict(fighter.profile_stats or {})
            existing["recent_fights"] = fights[:10]
            existing.update({k: v for k, v in derived.items()
                             if k not in existing or existing[k] in (None, 0, 0.0)})

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
            losses = sum(1 for f in fights if f["result"] == "loss")
            log.info(
                "  Saved %d fights: %dW(%dKO/%dSub/%dDec) %dL",
                len(fights),
                len(fights) - losses,
                ko_wins, sub_wins, dec_wins,
                losses,
            )
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
    scrape_all = "--all" in sys.argv
    scrape_fighters(force=force, scrape_all=scrape_all)
