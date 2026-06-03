from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.core.config import get_settings


@dataclass(frozen=True)
class FighterCandidate:
    name: str
    url: str
    record: str | None = None


@dataclass(frozen=True)
class CompletedEventCandidate:
    name: str
    url: str
    event_date: str | None
    location: str | None


class UfcStatsClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.ufcstats_base_url.rstrip("/")
        self.timeout = settings.scraper_timeout_seconds
        self.request_delay = max(0.0, settings.scraper_request_delay_seconds)
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "FightIQ/0.1 personal analytics app "
                    "(compatible; respectful on-demand requests)"
                )
            }
        )

    def find_fighters(self, query: str) -> list[FighterCandidate]:
        normalized = _normalize_name(query)
        chars = _candidate_index_chars(query)
        candidates: list[FighterCandidate] = []
        seen_urls: set[str] = set()

        for char in chars:
            html = self._get(f"/statistics/fighters?char={char}&page=all")
            soup = BeautifulSoup(html, "html.parser")
            for row in soup.select("tr.b-statistics__table-row"):
                links = row.select("a.b-link.b-link_style_black")
                if len(links) < 2:
                    continue
                first = links[0].get_text(" ", strip=True)
                last = links[1].get_text(" ", strip=True)
                name = " ".join(part for part in [first, last] if part).strip()
                url = links[0].get("href", "")
                if not name or not url or url in seen_urls:
                    continue
                if normalized not in _normalize_name(name):
                    continue
                cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
                record = _record_from_cells(cells)
                candidates.append(FighterCandidate(name=name, url=url, record=record))
                seen_urls.add(url)

        return candidates

    def fetch_fighter_by_name(self, name: str) -> dict[str, Any]:
        candidates = self.find_fighters(name)
        if not candidates:
            raise LookupError(f"No UFCStats fighter match for {name}")

        exact = next((candidate for candidate in candidates if _normalize_name(candidate.name) == _normalize_name(name)), None)
        candidate = exact or candidates[0]
        profile = self.fetch_fighter_profile(candidate.url)
        profile["search_match"] = candidate.__dict__
        return profile

    def fetch_fighter_profile(self, url: str) -> dict[str, Any]:
        url = _normalize_ufcstats_url(url)
        response = self._request(url)
        soup = BeautifulSoup(response.text, "html.parser")

        name = soup.select_one(".b-content__title-highlight")
        profile = _parse_profile_facts(soup)
        stats = _parse_career_stats(soup)
        fighter_name = name.get_text(" ", strip=True) if name else profile.get("name")
        fights = _parse_fight_history(soup, fighter_name or "")

        return {
            "source": "ufcstats",
            "source_url": url,
            "name": fighter_name,
            "record": profile.get("record"),
            "height_cm": _height_to_cm(profile.get("height")),
            "reach_cm": _reach_to_cm(profile.get("reach")),
            "weight_lbs": _weight_to_lbs(profile.get("weight")),
            "stance": profile.get("stance"),
            "date_of_birth": profile.get("dob"),
            "profile": profile,
            "stats": stats,
            "recent_fights": fights[:8],
            "raw_fight_count": len(fights),
        }

    def list_completed_events(self, limit: int = 1) -> list[CompletedEventCandidate]:
        html = self._get("/statistics/events/completed?page=all")
        soup = BeautifulSoup(html, "html.parser")
        events: list[CompletedEventCandidate] = []

        for row in soup.select("tr.b-statistics__table-row"):
            link = row.select_one("a.b-link.b-link_style_black")
            if not link:
                continue
            cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
            if len(cells) < 2:
                continue
            name = link.get_text(" ", strip=True)
            url = link.get("href", "")
            if not name or not url:
                continue
            event_date = _extract_event_date(" ".join(cells))
            location = cells[-1] if cells else None
            if location and _looks_like_date(location):
                location = None
            events.append(
                CompletedEventCandidate(
                    name=name,
                    url=_normalize_ufcstats_url(url),
                    event_date=event_date,
                    location=location,
                )
            )
            if len(events) >= limit:
                break

        return events

    def list_upcoming_events(self, limit: int = 12) -> list[CompletedEventCandidate]:
        html = self._get("/statistics/events/upcoming")
        soup = BeautifulSoup(html, "html.parser")
        events: list[CompletedEventCandidate] = []

        for row in soup.select("tr.b-statistics__table-row"):
            link = row.select_one("a.b-link.b-link_style_black")
            if not link:
                continue
            cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
            if len(cells) < 2:
                continue
            name = link.get_text(" ", strip=True)
            url = link.get("href", "")
            if not name or not url:
                continue
            event_date = _extract_event_date(" ".join(cells))
            location = cells[-1] if cells else None
            if location and _looks_like_date(location):
                location = None
            events.append(
                CompletedEventCandidate(
                    name=name,
                    url=_normalize_ufcstats_url(url),
                    event_date=event_date,
                    location=location,
                )
            )
            if len(events) >= limit:
                break

        return events

    def fetch_completed_event(self, event: CompletedEventCandidate) -> dict[str, Any]:
        response = self._request(event.url)
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.select_one(".b-content__title-highlight")

        return {
            "source": "ufcstats",
            "source_url": event.url,
            "name": title.get_text(" ", strip=True) if title else event.name,
            "event_date": event.event_date,
            "location": event.location,
            "status": "completed",
            "fights": _parse_event_fight_rows(soup),
        }

    def fetch_upcoming_event(self, event: CompletedEventCandidate) -> dict[str, Any]:
        response = self._request(event.url)
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.select_one(".b-content__title-highlight")

        return {
            "source": "ufcstats",
            "source_url": event.url,
            "name": title.get_text(" ", strip=True) if title else event.name,
            "event_date": event.event_date,
            "location": event.location,
            "status": "upcoming",
            "fights": _parse_upcoming_event_fight_rows(soup),
        }

    def _get(self, path: str) -> str:
        response = self._request(urljoin(f"{self.base_url}/", path.lstrip("/")))
        return response.text

    def _request(self, url: str) -> requests.Response:
        if self.request_delay and self._last_request_at:
            elapsed = time.monotonic() - self._last_request_at
            wait_time = self.request_delay - elapsed
            if wait_time > 0:
                time.sleep(wait_time)

        response = self.session.get(url, timeout=self.timeout)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response


def _parse_profile_facts(soup: BeautifulSoup) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for item in soup.select(".b-list__box-list-item"):
        text = " ".join(item.get_text(" ", strip=True).split())
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        normalized_key = key.strip().lower().replace(".", "").replace(" ", "_")
        facts[normalized_key] = value.strip() or None

    if "dob" in facts and facts["dob"]:
        facts["dob"] = _parse_date(facts["dob"])
    return facts


def _parse_career_stats(soup: BeautifulSoup) -> dict[str, float]:
    stats: dict[str, float] = {}
    label_map = {
        "slpm": "strikes_landed_per_min",
        "str acc": "sig_str_acc",
        "sapm": "strikes_absorbed_per_min",
        "str def": "sig_str_def",
        "td avg": "td_avg_per_15",
        "td acc": "td_acc",
        "td def": "td_def",
        "sub avg": "sub_avg_per_15",
    }

    for item in soup.select(".b-list__box-list-item"):
        text = " ".join(item.get_text(" ", strip=True).split())
        if ":" not in text:
            continue
        label, value = text.split(":", 1)
        cleaned_label = label.strip().lower().replace(".", "")
        mapped = label_map.get(cleaned_label)
        if not mapped:
            continue
        stats[mapped] = _number_or_percent(value)

    return stats


def _parse_fight_history(soup: BeautifulSoup, fighter_name: str = "") -> list[dict[str, Any]]:
    fights: list[dict[str, Any]] = []
    for row in soup.select("tr.b-fight-details__table-row.b-fight-details__table-row__hover"):
        columns = row.select("td")
        cells = [cell.get_text(" ", strip=True) for cell in columns]
        if len(cells) < 10:
            continue
        fighters = _fighters_from_history_column(columns[1]) if len(columns) > 1 else []
        opponent = _opponent_from_fighters(fighters, fighter_name)
        knockdowns = _parse_stat_pair(cells[2])
        significant_strikes = _parse_stat_pair(cells[3])
        takedowns = _parse_stat_pair(cells[4])
        submission_attempts = _parse_stat_pair(cells[5])
        event_name, event_date, method, round_number, time = _parse_history_result_columns(cells)
        fights.append(
            {
                "result": cells[0],
                "fighters": cells[1],
                "opponent": opponent,
                "knockdowns": cells[2],
                "knockdowns_for": knockdowns[0],
                "knockdowns_against": knockdowns[1],
                "significant_strikes": cells[3],
                "sig_strikes_for": significant_strikes[0],
                "sig_strikes_against": significant_strikes[1],
                "takedowns": cells[4],
                "takedowns_for": takedowns[0],
                "takedowns_against": takedowns[1],
                "submission_attempts": cells[5],
                "sub_attempts_for": submission_attempts[0],
                "sub_attempts_against": submission_attempts[1],
                "event": event_name,
                "date": event_date,
                "method": method,
                "round": round_number,
                "time": time,
                "fight_url": row.get("data-link"),
            }
        )
    return fights


def _parse_event_fight_rows(soup: BeautifulSoup) -> list[dict[str, Any]]:
    fights: list[dict[str, Any]] = []
    for order, row in enumerate(soup.select("tr.b-fight-details__table-row.b-fight-details__table-row__hover"), start=1):
        columns = row.select("td")
        cells = [cell.get_text(" ", strip=True) for cell in columns]
        if len(cells) < 10:
            continue
        fighter_links = columns[1].select("a") if len(columns) > 1 else []
        fighters = [
            {
                "name": link.get_text(" ", strip=True),
                "source_url": _normalize_ufcstats_url(link.get("href", "")),
            }
            for link in fighter_links
            if link.get_text(" ", strip=True)
        ]
        if len(fighters) < 2:
            continue

        knockdowns = _parse_stat_pair(cells[2])
        sig_strikes = _parse_stat_pair(cells[3])
        takedowns = _parse_stat_pair(cells[4])
        submissions = _parse_stat_pair(cells[5])
        result = cells[0].lower()

        fights.append(
            {
                "source_url": _normalize_ufcstats_url(row.get("data-link", "")),
                "bout_order": order,
                "fighter_a": fighters[0],
                "fighter_b": fighters[1],
                "winner_name": fighters[0]["name"] if result == "win" else None,
                "result": result,
                "weight_class": cells[6],
                "method": cells[7],
                "round": _safe_int(cells[8]),
                "time": cells[9],
                "scheduled_rounds": _scheduled_rounds(cells[8]),
                "fighter_stats": [
                    {
                        "fighter_name": fighters[0]["name"],
                        "opponent_name": fighters[1]["name"],
                        "knockdowns": knockdowns[0],
                        "knockdowns_absorbed": knockdowns[1],
                        "sig_strikes_landed": sig_strikes[0],
                        "sig_strikes_absorbed": sig_strikes[1],
                        "takedowns_landed": takedowns[0],
                        "takedowns_allowed": takedowns[1],
                        "submission_attempts": submissions[0],
                        "submission_attempts_allowed": submissions[1],
                    },
                    {
                        "fighter_name": fighters[1]["name"],
                        "opponent_name": fighters[0]["name"],
                        "knockdowns": knockdowns[1],
                        "knockdowns_absorbed": knockdowns[0],
                        "sig_strikes_landed": sig_strikes[1],
                        "sig_strikes_absorbed": sig_strikes[0],
                        "takedowns_landed": takedowns[1],
                        "takedowns_allowed": takedowns[0],
                        "submission_attempts": submissions[1],
                        "submission_attempts_allowed": submissions[0],
                    },
                ],
            }
        )
    return fights


def _parse_upcoming_event_fight_rows(soup: BeautifulSoup) -> list[dict[str, Any]]:
    fights: list[dict[str, Any]] = []
    rows = soup.select("tr.b-fight-details__table-row.b-fight-details__table-row__hover")
    for order, row in enumerate(rows, start=1):
        columns = row.select("td")
        cells = [cell.get_text(" ", strip=True) for cell in columns]
        if len(cells) < 2:
            continue
        fighter_links = columns[1].select("a") if len(columns) > 1 else []
        fighters = [
            {
                "name": link.get_text(" ", strip=True),
                "source_url": _normalize_ufcstats_url(link.get("href", "")),
            }
            for link in fighter_links
            if link.get_text(" ", strip=True)
        ]
        if len(fighters) < 2:
            continue

        weight_class = cells[6] if len(cells) > 6 and cells[6] else _weight_class_from_cells(cells)
        fights.append(
            {
                "source_url": _normalize_ufcstats_url(row.get("data-link", "")),
                "bout_order": order,
                "fighter_a": fighters[0],
                "fighter_b": fighters[1],
                "weight_class": weight_class or "Unknown",
                "scheduled_rounds": 5 if order == 1 else 3,
                "status": "scheduled",
            }
        )
    return fights


def _fighters_from_history_column(column) -> list[str]:
    names = [link.get_text(" ", strip=True) for link in column.select("a")]
    return [name for name in names if name]


def _opponent_from_fighters(fighters: list[str], fighter_name: str) -> str | None:
    normalized = _normalize_name(fighter_name)
    for fighter in fighters:
        if _normalize_name(fighter) != normalized:
            return fighter
    return fighters[1] if len(fighters) > 1 else None


def _parse_stat_pair(value: str) -> tuple[int | None, int | None]:
    numbers = []
    for part in value.split():
        try:
            numbers.append(int(part))
        except ValueError:
            continue
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        return numbers[0], None
    return None, None


def _parse_history_result_columns(cells: list[str]) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    if len(cells) >= 11:
        return cells[6], cells[7], cells[8], cells[9], cells[10]

    event_name, event_date = _split_event_and_date(cells[6] if len(cells) > 6 else "")
    method = cells[7] if len(cells) > 7 else None
    round_number = cells[8] if len(cells) > 8 else None
    time = cells[9] if len(cells) > 9 else None
    return event_name, event_date, method, round_number, time


def _split_event_and_date(value: str) -> tuple[str | None, str | None]:
    match = re.match(r"^(?P<event>.+?)\s+(?P<date>[A-Z][a-z]{2}\.?\s+\d{1,2},\s+\d{4})$", value.strip())
    if not match:
        return value or None, None
    return match.group("event"), match.group("date")


def _normalize_name(value: str) -> str:
    return " ".join(value.lower().replace("-", " ").split())


def _candidate_index_chars(query: str) -> list[str]:
    parts = [part for part in _normalize_name(query).split() if part]
    chars = [part[0] for part in reversed(parts)]
    chars.extend(part[0] for part in parts)
    chars.append("all")
    return list(dict.fromkeys(chars))


def _normalize_ufcstats_url(url: str) -> str:
    if url.startswith("https://ufcstats.com"):
        return url.replace("https://", "http://", 1)
    return url


def _record_from_cells(cells: list[str]) -> str | None:
    numbers = [cell for cell in cells if cell.isdigit()]
    if len(numbers) >= 3:
        return "-".join(numbers[-3:])
    return None


def _parse_event_date(value: str) -> str | None:
    cleaned = value.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_event_date(value: str) -> str | None:
    match = re.search(r"([A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})", value)
    return _parse_event_date(match.group(1)) if match else None


def _looks_like_date(value: str) -> bool:
    return _parse_event_date(value) is not None


def _weight_class_from_cells(cells: list[str]) -> str | None:
    classes = [
        "Heavyweight",
        "Light Heavyweight",
        "Middleweight",
        "Welterweight",
        "Lightweight",
        "Featherweight",
        "Bantamweight",
        "Flyweight",
        "Strawweight",
        "Catch Weight",
    ]
    for cell in cells:
        normalized = " ".join(cell.split())
        for weight_class in classes:
            if weight_class.lower() in normalized.lower():
                return weight_class
    return None


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _scheduled_rounds(round_value: str | None) -> int:
    parsed = _safe_int(round_value)
    return 5 if parsed and parsed > 3 else 3


def _number_or_percent(value: str) -> float:
    cleaned = value.strip().replace("%", "")
    if cleaned in {"", "--", "---"}:
        return 0.0
    number = float(cleaned)
    return number / 100 if "%" in value else number


def _height_to_cm(value: str | None) -> float | None:
    if not value or value == "--":
        return None
    parts = value.replace('"', "").split("'")
    if len(parts) != 2:
        return None
    feet = int(parts[0].strip())
    inches = int(parts[1].strip())
    return round((feet * 12 + inches) * 2.54, 1)


def _reach_to_cm(value: str | None) -> float | None:
    if not value or value == "--":
        return None
    return round(float(value.replace('"', "").strip()) * 2.54, 1)


def _weight_to_lbs(value: str | None) -> float | None:
    if not value or value == "--":
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", value)
    return float(match.group(1)) if match else None


def _parse_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def iso_to_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)
