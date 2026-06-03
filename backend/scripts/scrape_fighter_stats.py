from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "fightiq.db"
FAILURE_LOG = Path(__file__).resolve().parent / "scrape_failed.txt"
REQUEST_DELAY_SECONDS = 1.0
RATE_LIMIT_SLEEP_SECONDS = 30
DEFAULT_MIN_MATCH_SCORE = 0.8

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

OFFICIAL_RATE_KEYS = [
    "sig_str_acc",
    "sig_str_def",
    "td_acc",
    "td_def",
    "strikes_landed_per_min",
    "strikes_absorbed_per_min",
    "sub_avg_per_15",
    "td_avg_per_15",
]

RECENT_FIGHT_KEYS = [
    "result",
    "opponent",
    "event",
    "date",
    "method",
    "round",
    "time",
    "knockdowns_for",
    "knockdowns_against",
    "sig_strikes_for",
    "sig_strikes_against",
    "takedowns_for",
    "takedowns_against",
    "sub_attempts_for",
    "sub_attempts_against",
]


@dataclass
class FighterRow:
    id: str
    name: str
    record: str | None
    stance: str | None
    height_cm: float | None
    reach_cm: float | None
    date_of_birth: str | None
    profile_stats: dict[str, Any]


@dataclass
class SourceResult:
    source: str
    source_url: str | None = None
    matched_name: str | None = None
    match_score: float = 0.0
    record: str | None = None
    stance: str | None = None
    height_cm: float | None = None
    reach_cm: float | None = None
    date_of_birth: str | None = None
    nationality: str | None = None
    weight_lbs: float | None = None
    recent_fights: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(
            self.record
            or self.height_cm
            or self.reach_cm
            or self.date_of_birth
            or self.nationality
            or self.weight_lbs
            or self.recent_fights
            or self.stats
        )


class HttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._last_request_at = 0.0

    def get(self, url: str, *, params: dict[str, Any] | None = None, timeout: int = 20) -> requests.Response:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)

        for attempt in range(3):
            response = self.session.get(url, params=params, timeout=timeout)
            self._last_request_at = time.monotonic()
            if response.status_code != 429:
                response.raise_for_status()
                return response
            wait = RATE_LIMIT_SLEEP_SECONDS * (attempt + 1)
            print(f"Rate limited by {url}. Sleeping {wait}s before retry...")
            time.sleep(wait)

        response.raise_for_status()
        return response


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill FightIQ fighter profile_stats without UFCStats.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--limit", type=int, default=None, help="Maximum fighters to process")
    parser.add_argument("--only", default=None, help="Only process fighters whose name contains this text")
    parser.add_argument("--event-name", default=None, help="Only process fighters booked on matching event name")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_MATCH_SCORE, help="Minimum name match score")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse, but do not update SQLite")
    parser.add_argument("--audit-only", action="store_true", help="Print missing profile count and exit")
    parser.add_argument("--force", action="store_true", help="Process matching fighters even if profile_stats already exists")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if args.audit_only:
        print_audit(conn)
        return 0

    fighters = load_target_fighters(conn, force=args.force)
    if args.only:
        needle = args.only.lower()
        fighters = [fighter for fighter in fighters if needle in fighter.name.lower()]
    if args.event_name:
        event_fighter_ids = load_event_fighter_ids(conn, args.event_name)
        fighters = [fighter for fighter in fighters if fighter.id in event_fighter_ids]
    if args.limit is not None:
        fighters = fighters[: max(0, args.limit)]

    print(f"Targets: {len(fighters)} missing fighter profile(s)")
    client = HttpClient()
    failures: list[str] = []
    updated = 0

    for index, fighter in enumerate(fighters, start=1):
        try:
            result = fetch_best_profile(client, fighter.name, args.min_score)
            if result is None:
                reason = "no confident match"
                failures.append(f"{fighter.name}\t{reason}")
                print(f"[{index}/{len(fighters)}] {fighter.name} - skipped ({reason}).")
                continue

            merged = build_profile_stats(fighter, result)
            if not merged:
                reason = f"{result.source} match had no usable stats"
                failures.append(f"{fighter.name}\t{reason}\t{result.source_url or ''}")
                print(f"[{index}/{len(fighters)}] {fighter.name} - skipped ({reason}).")
                continue

            if not args.dry_run:
                update_fighter(conn, fighter, result, merged)
                conn.commit()
            updated += 1
            fight_count = len(merged.get("recent_fights") or [])
            suffix = "dry-run" if args.dry_run else "updated"
            print(f"[{index}/{len(fighters)}] {fighter.name} - found {fight_count} fights, {suffix}.")
        except Exception as exc:  # noqa: BLE001 - scraper should continue per fighter
            failures.append(f"{fighter.name}\t{type(exc).__name__}: {exc}")
            print(f"[{index}/{len(fighters)}] {fighter.name} - failed: {exc}")

    write_failures(failures)
    print(f"Done. Updated: {updated}. Failed/skipped: {len(failures)}. Failure log: {FAILURE_LOG}")
    print_audit(conn)
    return 0


def print_audit(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT name, profile_stats FROM fighters ORDER BY name").fetchall()
    empty = [row["name"] for row in rows if not parse_profile_stats(row["profile_stats"])]
    no_history = []
    for row in rows:
        stats = parse_profile_stats(row["profile_stats"])
        if stats and not stats.get("recent_fights"):
            no_history.append(row["name"])
    print(f"Empty: {len(empty)}/{len(rows)}")
    print(f"With stats but no recent_fights: {len(no_history)}/{len(rows)}")


def load_target_fighters(conn: sqlite3.Connection, *, force: bool = False) -> list[FighterRow]:
    rows = conn.execute(
        """
        SELECT id, name, record, stance, height_cm, reach_cm, date_of_birth, profile_stats
        FROM fighters
        ORDER BY name
        """
    ).fetchall()
    fighters: list[FighterRow] = []
    for row in rows:
        stats = parse_profile_stats(row["profile_stats"])
        if stats and not force:
            continue
        fighters.append(
            FighterRow(
                id=row["id"],
                name=row["name"],
                record=row["record"],
                stance=row["stance"],
                height_cm=row["height_cm"],
                reach_cm=row["reach_cm"],
                date_of_birth=row["date_of_birth"],
                profile_stats=stats,
            )
        )
    return fighters


def load_event_fighter_ids(conn: sqlite3.Connection, event_name: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT f.fighter_a_id, f.fighter_b_id
        FROM fights f
        JOIN events e ON e.id = f.event_id
        WHERE lower(e.name) LIKE ?
        ORDER BY f.bout_order
        """,
        (f"%{event_name.lower()}%",),
    ).fetchall()
    ids: set[str] = set()
    for row in rows:
        ids.add(row["fighter_a_id"])
        ids.add(row["fighter_b_id"])
    return ids


def parse_profile_stats(value: Any) -> dict[str, Any]:
    if value in (None, "", "null"):
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def fetch_best_profile(client: HttpClient, fighter_name: str, min_score: float) -> SourceResult | None:
    source_attempts = [
        fetch_tapology_profile,
        fetch_sherdog_profile,
        fetch_wikipedia_profile,
    ]
    candidates: list[SourceResult] = []
    for fetcher in source_attempts:
        try:
            result = fetcher(client, fighter_name, min_score)
        except Exception as exc:  # noqa: BLE001 - fallback to next source
            result = SourceResult(source=fetcher.__name__, notes=[f"source failed: {exc}"])
        if result and result.match_score >= min_score and result.usable:
            candidates.append(result)

    if not candidates:
        return None
    return merge_source_results(candidates)


def merge_source_results(candidates: list[SourceResult]) -> SourceResult:
    primary = sorted(candidates, key=lambda item: (len(item.recent_fights), item.match_score), reverse=True)[0]
    by_profile_priority = sorted(candidates, key=lambda item: source_profile_priority(item.source))
    by_history_priority = sorted(candidates, key=lambda item: (len(item.recent_fights), item.match_score), reverse=True)

    merged = SourceResult(
        source="+".join(source.source for source in candidates),
        source_url=primary.source_url,
        matched_name=primary.matched_name,
        match_score=max(source.match_score for source in candidates),
        recent_fights=by_history_priority[0].recent_fights,
    )
    for attr in ["record", "stance", "height_cm", "reach_cm", "date_of_birth", "nationality", "weight_lbs"]:
        setattr(merged, attr, first_attr(by_profile_priority, attr))

    stats: dict[str, Any] = {}
    for source in candidates:
        stats.update({key: value for key, value in source.stats.items() if value not in (None, "", [])})
    if merged.recent_fights:
        stats.update(derive_history_rates(merged.record, merged.recent_fights))
    merged.stats = stats
    return merged


def source_profile_priority(source: str) -> int:
    order = {"wikipedia": 0, "tapology": 1, "sherdog": 2}
    return order.get(source, 99)


def first_attr(candidates: list[SourceResult], attr: str) -> Any:
    for candidate in candidates:
        value = getattr(candidate, attr)
        if value not in (None, "", []):
            return value
    return None


def fetch_wikipedia_profile(client: HttpClient, fighter_name: str, min_score: float) -> SourceResult | None:
    search = client.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": f"{fighter_name} mixed martial artist",
            "format": "json",
            "srlimit": 5,
        },
    ).json()
    best_title = None
    best_score = 0.0
    for item in search.get("query", {}).get("search", []):
        title = item.get("title") or ""
        score = name_score(fighter_name, strip_disambiguation(title))
        snippet = clean_text(item.get("snippet") or "")
        if "mixed martial" in snippet.lower() or "ufc" in snippet.lower() or score >= 0.92:
            if score > best_score:
                best_title = title
                best_score = score

    if not best_title or best_score < min_score:
        return None

    parse_response = client.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "parse",
            "page": best_title,
            "prop": "wikitext",
            "format": "json",
            "formatversion": 2,
        },
    ).json()
    wikitext = parse_response.get("parse", {}).get("wikitext") or ""
    fields = parse_infobox_fields(wikitext)
    result = SourceResult(
        source="wikipedia",
        source_url=f"https://en.wikipedia.org/wiki/{quote_plus(best_title).replace('+', '_')}",
        matched_name=strip_disambiguation(best_title),
        match_score=best_score,
    )
    result.height_cm = parse_height_cm(first_value(fields, ["height"]))
    result.reach_cm = parse_length_cm(first_value(fields, ["reach"]))
    result.weight_lbs = parse_weight_lbs(first_value(fields, ["weight"]))
    result.date_of_birth = parse_wiki_birth_date(first_value(fields, ["birth_date", "birth date"]))
    result.nationality = clean_wiki_value(first_value(fields, ["nationality"]))
    if not result.nationality:
        result.nationality = country_from_birth_place(clean_wiki_value(first_value(fields, ["birth_place", "birth place"])))
    result.stance = clean_wiki_value(first_value(fields, ["stance"]))
    result.record = parse_record_from_fields(fields) or parse_record_from_text(wikitext)
    result.stats = career_rate_stats_from_fields(fields)
    return result


def fetch_tapology_profile(client: HttpClient, fighter_name: str, min_score: float) -> SourceResult | None:
    response = client.get(
        "https://www.tapology.com/search",
        params={"term": fighter_name, "context": "fighters"},
    )
    soup = BeautifulSoup(response.text, "html.parser")
    profile_url, matched_name, score = pick_anchor_match(
        soup,
        fighter_name,
        href_contains="/fightcenter/fighters/",
        base_url="https://www.tapology.com",
    )
    if not profile_url or score < min_score:
        return None

    profile = client.get(profile_url)
    profile_soup = BeautifulSoup(profile.text, "html.parser")
    text = text_blocks(profile_soup)
    joined = "\n".join(text)
    result = SourceResult(
        source="tapology",
        source_url=profile_url,
        matched_name=matched_name,
        match_score=score,
        record=parse_record_from_text(joined),
        height_cm=parse_labeled_length(joined, ["Height"]),
        reach_cm=parse_labeled_length(joined, ["Reach"]),
        weight_lbs=parse_labeled_weight(joined, ["Weight", "Weight Class"]),
        nationality=parse_labeled_value(joined, ["Born", "Fighting out of", "Nationality"]),
        stance=parse_labeled_value(joined, ["Stance"]),
    )
    result.recent_fights = parse_tapology_recent_fights(profile_soup)[:8]
    result.stats = derive_history_rates(result.record, result.recent_fights)
    return result


def fetch_sherdog_profile(client: HttpClient, fighter_name: str, min_score: float) -> SourceResult | None:
    response = client.get(
        "https://www.sherdog.com/stats/fightfinder",
        params={"SearchTxt": fighter_name},
    )
    soup = BeautifulSoup(response.text, "html.parser")
    profile_url, matched_name, score = pick_anchor_match(
        soup,
        fighter_name,
        href_contains="/fighter/",
        base_url="https://www.sherdog.com",
    )
    if not profile_url or score < min_score:
        return None

    profile = client.get(profile_url)
    profile_soup = BeautifulSoup(profile.text, "html.parser")
    joined = "\n".join(text_blocks(profile_soup))
    result = SourceResult(
        source="sherdog",
        source_url=profile_url,
        matched_name=matched_name,
        match_score=score,
        record=parse_record_from_text(joined),
        height_cm=parse_labeled_length(joined, ["HEIGHT", "Height"]),
        reach_cm=parse_labeled_length(joined, ["REACH", "Reach"]),
        weight_lbs=parse_labeled_weight(joined, ["WEIGHT", "Weight"]),
        nationality=parse_labeled_value(joined, ["NATIONALITY", "Nationality", "FIGHTING OUT OF"]),
        stance=parse_labeled_value(joined, ["STANCE", "Stance"]),
    )
    result.recent_fights = parse_sherdog_recent_fights(profile_soup)[:8]
    result.stats = derive_history_rates(result.record, result.recent_fights)
    return result


def pick_anchor_match(
    soup: BeautifulSoup,
    fighter_name: str,
    *,
    href_contains: str,
    base_url: str,
) -> tuple[str | None, str | None, float]:
    best: tuple[str | None, str | None, float] = (None, None, 0.0)
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        if href_contains not in href:
            continue
        candidate_name = clean_text(anchor.get_text(" ", strip=True))
        if not candidate_name or candidate_name in seen:
            continue
        seen.add(candidate_name)
        score = name_score(fighter_name, candidate_name)
        if score > best[2]:
            best = (urljoin(base_url, href), candidate_name, score)
    return best


def parse_tapology_recent_fights(soup: BeautifulSoup) -> list[dict[str, Any]]:
    fights: list[dict[str, Any]] = []
    for row in soup.select("tr, li, div"):
        classes = " ".join(row.get("class") or [])
        if not any(token in classes.lower() for token in ["fight", "result", "bout"]):
            continue
        fight = parse_fight_from_text(row.get_text(" ", strip=True))
        if fight and fight not in fights:
            fights.append(fight)
        if len(fights) >= 8:
            break
    return fights


def parse_sherdog_recent_fights(soup: BeautifulSoup) -> list[dict[str, Any]]:
    fights: list[dict[str, Any]] = []
    tables = soup.find_all("table")
    for table in tables:
        table_text = table.get_text(" ", strip=True).lower()
        if "result" not in table_text or "method" not in table_text:
            continue
        for row in table.find_all("tr"):
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
            if not cells or cells[0].lower() in {"result", "res."}:
                continue
            fight = parse_fight_from_cells(cells)
            if fight and fight not in fights:
                fights.append(fight)
            if len(fights) >= 8:
                return fights
    return fights


def parse_fight_from_cells(cells: list[str]) -> dict[str, Any] | None:
    result = normalize_result(cells[0])
    if result is None:
        return None

    opponent = None
    event = None
    method = None
    fight_date = None
    fight_round = None
    fight_time = None

    for item in cells[1:]:
        if not item:
            continue
        if opponent is None and not looks_like_method(item) and not parse_date_fragment(item):
            opponent = cleanup_opponent_name(item)
            continue
        if method is None and looks_like_method(item):
            method = normalize_method(item)
            continue
        if fight_date is None:
            fight_date = parse_date_fragment(item)
        if fight_time is None:
            time_match = re.search(r"\b\d{1,2}:\d{2}\b", item)
            if time_match:
                fight_time = time_match.group(0)
        if fight_round is None:
            round_match = re.search(r"\b(?:round|r)\s*(\d)\b|\b([1-5])\b", item, re.I)
            if round_match and len(item) <= 12:
                fight_round = round_match.group(1) or round_match.group(2)
        if event is None and item != opponent and not looks_like_method(item):
            event = item

    return clean_fight(
        {
            "result": result,
            "opponent": opponent,
            "event": event,
            "date": fight_date,
            "method": method,
            "round": fight_round,
            "time": fight_time,
        }
    )


def parse_fight_from_text(text: str) -> dict[str, Any] | None:
    cleaned = clean_text(text)
    if len(cleaned) < 12:
        return None
    result_match = re.search(r"\b(W|L|NC|No Contest|Draw)\b", cleaned, re.I)
    if not result_match:
        return None
    result = normalize_result(result_match.group(1))
    if result is None:
        return None

    method = normalize_method(cleaned)
    fight_date = parse_date_fragment(cleaned)
    time_match = re.search(r"\b\d{1,2}:\d{2}\b", cleaned)
    round_match = re.search(r"\b(?:Round|R)\s*([1-5])\b", cleaned, re.I)
    opponent = guess_opponent_from_text(cleaned)
    event = guess_event_from_text(cleaned)

    return clean_fight(
        {
            "result": result,
            "opponent": opponent,
            "event": event,
            "date": fight_date,
            "method": method,
            "round": round_match.group(1) if round_match else None,
            "time": time_match.group(0) if time_match else None,
        }
    )


def clean_fight(fight: dict[str, Any]) -> dict[str, Any] | None:
    if not fight.get("result"):
        return None
    if not fight.get("opponent") and not fight.get("event"):
        return None
    cleaned = {key: fight.get(key) for key in RECENT_FIGHT_KEYS}
    if cleaned.get("event"):
        cleaned["event"] = clean_event_name(str(cleaned["event"]))
    for stat_key in [
        "knockdowns_for",
        "knockdowns_against",
        "sig_strikes_for",
        "sig_strikes_against",
        "takedowns_for",
        "takedowns_against",
        "sub_attempts_for",
        "sub_attempts_against",
    ]:
        cleaned[stat_key] = None
    return cleaned


def build_profile_stats(fighter: FighterRow, result: SourceResult) -> dict[str, Any]:
    stats: dict[str, Any] = dict(fighter.profile_stats or {})
    source_stats = {key: value for key, value in result.stats.items() if value not in (None, "", [])}

    for key in OFFICIAL_RATE_KEYS:
        if key in source_stats:
            stats[key] = source_stats[key]
    for derived_key in ["finish_rate", "ko_win_rate", "sub_win_rate", "decision_win_rate", "finish_loss_rate"]:
        if derived_key in source_stats:
            stats[derived_key] = source_stats[derived_key]

    recent_fights = [fight for fight in result.recent_fights if fight.get("result")]
    if recent_fights:
        stats["recent_fights"] = recent_fights[:8]

    stats["raw_fight_count"] = infer_fight_count(result.record, recent_fights)
    if result.nationality:
        stats["nationality"] = result.nationality
    if result.weight_lbs:
        stats["weight_lbs"] = result.weight_lbs

    stats["scrape_source"] = result.source
    stats["scrape_source_url"] = result.source_url
    stats["scrape_match_score"] = round(result.match_score, 3)
    stats["scraped_at"] = utc_now_iso()

    return {key: value for key, value in stats.items() if value not in (None, "", [])}


def update_fighter(
    conn: sqlite3.Connection,
    fighter: FighterRow,
    result: SourceResult,
    profile_stats: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE fighters
        SET
            record = COALESCE(NULLIF(record, ''), ?),
            stance = COALESCE(NULLIF(stance, ''), ?),
            height_cm = COALESCE(height_cm, ?),
            reach_cm = COALESCE(reach_cm, ?),
            date_of_birth = COALESCE(date_of_birth, ?),
            profile_stats = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            result.record,
            result.stance,
            result.height_cm,
            result.reach_cm,
            result.date_of_birth,
            json.dumps(profile_stats, sort_keys=True),
            utc_now_iso(),
            fighter.id,
        ),
    )


def derive_history_rates(record: str | None, fights: list[dict[str, Any]]) -> dict[str, float]:
    wins, losses, _draws = parse_record_tuple(record)
    if wins <= 0:
        wins = sum(1 for fight in fights if fight.get("result") == "win")
    losses = losses or sum(1 for fight in fights if fight.get("result") == "loss")
    if wins <= 0:
        return {}

    ko_wins = sum(1 for fight in fights if fight.get("result") == "win" and is_ko_method(fight.get("method")))
    sub_wins = sum(1 for fight in fights if fight.get("result") == "win" and is_sub_method(fight.get("method")))
    dec_wins = sum(1 for fight in fights if fight.get("result") == "win" and is_decision_method(fight.get("method")))
    finish_losses = sum(1 for fight in fights if fight.get("result") == "loss" and is_finish_method(fight.get("method")))

    known_recent_wins = max(1, sum(1 for fight in fights if fight.get("result") == "win"))
    scale = min(1.0, known_recent_wins / wins)
    ko_rate = min(1.0, (ko_wins / wins) / max(scale, 0.2))
    sub_rate = min(1.0, (sub_wins / wins) / max(scale, 0.2))
    decision_rate = min(1.0, (dec_wins / wins) / max(scale, 0.2))

    rates = {
        "finish_rate": round(min(1.0, ko_rate + sub_rate), 3),
        "ko_win_rate": round(ko_rate, 3),
        "sub_win_rate": round(sub_rate, 3),
        "decision_win_rate": round(decision_rate, 3),
    }
    if losses > 0:
        rates["finish_loss_rate"] = round(finish_losses / max(losses, 1), 3)
    return rates


def career_rate_stats_from_fields(fields: dict[str, str]) -> dict[str, float]:
    ko_wins = parse_int(first_value(fields, ["mma_kowin", "ko_win", "kowin"]))
    sub_wins = parse_int(first_value(fields, ["mma_subwin", "sub_win", "submission_win"]))
    dec_wins = parse_int(first_value(fields, ["mma_decwin", "dec_win", "decision_win"]))
    wins = parse_int(first_value(fields, ["mma_win", "wins", "win"]))
    losses = parse_int(first_value(fields, ["mma_loss", "losses", "loss"]))
    if wins <= 0:
        wins = ko_wins + sub_wins + dec_wins
    if wins <= 0:
        return {}
    finish_losses = parse_int(first_value(fields, ["mma_koloss", "mma_subloss"]))
    return {
        "finish_rate": round((ko_wins + sub_wins) / wins, 3),
        "ko_win_rate": round(ko_wins / wins, 3),
        "sub_win_rate": round(sub_wins / wins, 3),
        "decision_win_rate": round(dec_wins / wins, 3),
        "finish_loss_rate": round(finish_losses / max(losses, 1), 3) if losses else 0.0,
    }


def parse_infobox_fields(wikitext: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in wikitext.splitlines():
        match = re.match(r"\|\s*([^=]+?)\s*=\s*(.*)$", line)
        if not match:
            continue
        key = normalize_key(match.group(1))
        value = match.group(2).strip()
        if key and value:
            fields[key] = value
    return fields


def first_value(fields: dict[str, str], keys: list[str]) -> str | None:
    for key in keys:
        normalized = normalize_key(key)
        if normalized in fields and fields[normalized]:
            return fields[normalized]
    return None


def parse_record_from_fields(fields: dict[str, str]) -> str | None:
    wins = parse_int(first_value(fields, ["mma_win", "wins", "win"]))
    losses = parse_int(first_value(fields, ["mma_loss", "losses", "loss"]))
    draws = parse_int(first_value(fields, ["mma_draw", "draws", "draw"]))
    if wins or losses or draws:
        return f"{wins}-{losses}-{draws}"
    return None


def parse_record_from_text(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = clean_text(text)
    patterns = [
        r"(?:pro mma record|mma record|record)\s*:?\s*(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})",
        r"\b(\d{1,2})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.I)
        if match:
            return f"{int(match.group(1))}-{int(match.group(2))}-{int(match.group(3))}"
    return None


def parse_record_tuple(record: str | None) -> tuple[int, int, int]:
    if not record:
        return 0, 0, 0
    match = re.search(r"(\d+)\s*-\s*(\d+)(?:\s*-\s*(\d+))?", record)
    if not match:
        return 0, 0, 0
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def infer_fight_count(record: str | None, recent_fights: list[dict[str, Any]]) -> int:
    wins, losses, draws = parse_record_tuple(record)
    total = wins + losses + draws
    return total if total > 0 else len(recent_fights)


def parse_wiki_birth_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"birth date(?: and age)?\|(\d{4})\|(\d{1,2})\|(\d{1,2})", value, re.I)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return parse_date_fragment(clean_wiki_value(value))


def parse_height_cm(value: str | None) -> float | None:
    return parse_length_cm(value)


def parse_length_cm(value: str | None) -> float | None:
    if not value:
        return None
    text = clean_wiki_value(value)
    cm_match = re.search(r"(\d{2,3}(?:\.\d+)?)\s*cm", text, re.I)
    if cm_match:
        return round(float(cm_match.group(1)), 1)
    ft_match = re.search(r"(\d)\s*(?:ft|'|feet)\s*(\d{1,2})?", text, re.I)
    if ft_match:
        feet = int(ft_match.group(1))
        inches = int(ft_match.group(2) or 0)
        return round((feet * 12 + inches) * 2.54, 1)
    inch_match = re.search(r"(\d{2,3}(?:\.\d+)?)\s*(?:in|\"|inches)", text, re.I)
    if inch_match:
        return round(float(inch_match.group(1)) * 2.54, 1)
    return None


def parse_weight_lbs(value: str | None) -> float | None:
    if not value:
        return None
    text = clean_wiki_value(value)
    lb_match = re.search(r"(\d{2,3}(?:\.\d+)?)\s*(?:lb|lbs|pounds)", text, re.I)
    if lb_match:
        return round(float(lb_match.group(1)), 1)
    kg_match = re.search(r"(\d{2,3}(?:\.\d+)?)\s*kg", text, re.I)
    if kg_match:
        return round(float(kg_match.group(1)) * 2.20462, 1)
    weight_class = text.lower()
    class_weights = {
        "flyweight": 125,
        "bantamweight": 135,
        "featherweight": 145,
        "lightweight": 155,
        "welterweight": 170,
        "middleweight": 185,
        "light heavyweight": 205,
        "heavyweight": 265,
        "strawweight": 115,
    }
    for label, lbs in class_weights.items():
        if label in weight_class:
            return float(lbs)
    return None


def parse_labeled_length(text: str, labels: list[str]) -> float | None:
    value = parse_labeled_value(text, labels)
    return parse_length_cm(value)


def parse_labeled_weight(text: str, labels: list[str]) -> float | None:
    value = parse_labeled_value(text, labels)
    return parse_weight_lbs(value)


def parse_labeled_value(text: str, labels: list[str]) -> str | None:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for index, line in enumerate(lines):
        for label in labels:
            label_pattern = re.escape(label)
            inline = re.search(rf"\b{label_pattern}\b\s*:?\s*(.+)$", line, re.I)
            if inline and clean_text(inline.group(1)).lower() != label.lower():
                return clean_text(inline.group(1))
            if line.lower() == label.lower() and index + 1 < len(lines):
                return lines[index + 1]
    return None


def normalize_method(value: str | None) -> str | None:
    if not value:
        return None
    text = clean_text(value).lower()
    if "submission" in text or re.search(r"\bsub\b", text) or any(
        token in text for token in ["rear naked", "armbar", "triangle", "guillotine", "kimura", "choke"]
    ):
        return "Submission"
    if "split" in text:
        return "Decision/Split"
    if "majority" in text:
        return "Decision/Majority"
    if "decision" in text or re.search(r"\bdec\b", text):
        return "Decision/Unanimous"
    if "tko" in text or "ko" in text or "doctor stoppage" in text or "corner" in text:
        return "KO/TKO"
    return None


def normalize_result(value: str | None) -> str | None:
    if not value:
        return None
    text = clean_text(value).lower()
    if text in {"w", "win"} or text.startswith("win"):
        return "win"
    if text in {"l", "loss"} or text.startswith("loss"):
        return "loss"
    if "no contest" in text or text in {"nc", "n/c"}:
        return "nc"
    if "draw" in text:
        return "nc"
    return None


def looks_like_method(value: str) -> bool:
    return normalize_method(value) is not None


def parse_date_fragment(value: str | None) -> str | None:
    if not value:
        return None
    text = clean_text(value)
    slash_date = re.search(r"\b([A-Z][a-z]{2,8})\.?\s*/\s*(\d{1,2})\s*/\s*(20\d{2}|19\d{2})\b", text)
    if slash_date:
        month = month_number(slash_date.group(1))
        if month:
            return f"{int(slash_date.group(3)):04d}-{month:02d}-{int(slash_date.group(2)):02d}"
    iso = re.search(r"\b(20\d{2}|19\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if iso:
        return f"{iso.group(1)}-{int(iso.group(2)):02d}-{int(iso.group(3)):02d}"
    for fmt in [
        r"\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),?\s+(20\d{2}|19\d{2})\b",
        r"\b(\d{1,2})\s+([A-Z][a-z]{2,8})\.?\s+(20\d{2}|19\d{2})\b",
    ]:
        match = re.search(fmt, text)
        if not match:
            continue
        if match.group(1).isdigit():
            day = int(match.group(1))
            month_name = match.group(2)
            year = int(match.group(3))
        else:
            month_name = match.group(1)
            day = int(match.group(2))
            year = int(match.group(3))
        month = month_number(month_name)
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"
    year_only = re.search(r"\b(20\d{2}|19\d{2})\b", text)
    return f"{year_only.group(1)}-01-01" if year_only else None


def month_number(name: str) -> int | None:
    names = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    return names.get(name.lower().strip("."))


def is_ko_method(method: str | None) -> bool:
    return bool(method and "ko" in method.lower())


def is_sub_method(method: str | None) -> bool:
    return bool(method and "sub" in method.lower())


def is_decision_method(method: str | None) -> bool:
    return bool(method and "decision" in method.lower())


def is_finish_method(method: str | None) -> bool:
    return is_ko_method(method) or is_sub_method(method)


def parse_int(value: str | None) -> int:
    if not value:
        return 0
    match = re.search(r"\d+", clean_wiki_value(value))
    return int(match.group(0)) if match else 0


def name_score(expected: str, candidate: str) -> float:
    expected_norm = normalize_name(expected)
    candidate_norm = normalize_name(candidate)
    seq = SequenceMatcher(None, expected_norm, candidate_norm).ratio()
    expected_tokens = set(expected_norm.split())
    candidate_tokens = set(candidate_norm.split())
    if not expected_tokens or not candidate_tokens:
        return seq
    token_overlap = len(expected_tokens & candidate_tokens) / max(len(expected_tokens), len(candidate_tokens))
    return max(seq, token_overlap)


def normalize_name(value: str) -> str:
    value = strip_disambiguation(value).lower()
    value = re.sub(r"\bnicknamed\b.*$", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_disambiguation(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", value or "").strip()


def clean_wiki_value(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\{\{[^{}]*\}\}", " ", value)
    text = re.sub(r"\[\[([^|\]]*\|)?([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    return clean_text(text)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def text_blocks(soup: BeautifulSoup) -> list[str]:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return [line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()]


def cleanup_opponent_name(value: str | None) -> str | None:
    if not value:
        return None
    text = clean_text(value)
    text = re.sub(r"\b(W|L|NC|No Contest|Draw)\b", "", text, flags=re.I)
    text = re.sub(r"\s+vs\.?\s+", " ", text, flags=re.I)
    return clean_text(text) or None


def guess_opponent_from_text(value: str) -> str | None:
    match = re.search(r"\b(?:vs\.?|def\.?|lost to)\s+([A-Z][A-Za-z' .-]{3,45})", value)
    if match:
        return cleanup_opponent_name(match.group(1))
    return None


def guess_event_from_text(value: str) -> str | None:
    match = re.search(r"\b(UFC[^|,]{2,60}|Bellator[^|,]{2,60}|PFL[^|,]{2,60})", value, re.I)
    return clean_event_name(match.group(1)) if match else None


def clean_event_name(value: str) -> str:
    text = clean_text(value)
    month_names = "Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December"
    text = re.sub(rf"\b(?:{month_names})\.?\s*/\s*\d{{1,2}}\s*/\s*(?:20\d{{2}}|19\d{{2}})\b", "", text, flags=re.I)
    text = re.sub(rf"\b(?:{month_names})\.?\s+\d{{1,2}},?\s*(?:20\d{{2}}|19\d{{2}})\b", "", text, flags=re.I)
    return clean_text(text)


def country_from_birth_place(value: str | None) -> str | None:
    if not value:
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return parts[-1] if parts else value


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def write_failures(failures: list[str]) -> None:
    if not failures:
        FAILURE_LOG.write_text("", encoding="utf-8")
        return
    FAILURE_LOG.write_text("\n".join(failures) + "\n", encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
