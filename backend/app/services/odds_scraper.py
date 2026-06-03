from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from pathlib import Path
import random
import re
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

from app.services.odds_utils import american_to_implied_raw, remove_vig

LOG_PATH = Path(__file__).resolve().parents[2] / "odds_scraper.log"
logger = logging.getLogger("fightiq.odds_scraper")
if not any(isinstance(handler, logging.FileHandler) and handler.baseFilename == str(LOG_PATH) for handler in logger.handlers):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
logger.setLevel(logging.INFO)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]


class OddsSource(ABC):
    name: str
    base_url: str
    search_paths: tuple[str, ...] = ("/",)

    def scrape_fight(self, fighter_a_name: str, fighter_b_name: str) -> dict[str, int] | None:
        for path in self.search_paths:
            html = self._fetch(path)
            if not html:
                continue
            parsed = self._parse_matchup_from_html(html, fighter_a_name, fighter_b_name)
            if parsed:
                logger.info("Successful odds scrape source=%s matchup=%s vs %s", self.name, fighter_a_name, fighter_b_name)
                return parsed
        return None

    def _fetch(self, path: str) -> str | None:
        url = path if path.startswith("http") else f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        for attempt in range(2):
            try:
                response = requests.get(
                    url,
                    headers={
                        "User-Agent": random.choice(USER_AGENTS),
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    timeout=20,
                    allow_redirects=True,
                )
                if response.status_code in {429, 503} and attempt == 0:
                    logger.warning("Odds source %s returned %s; backing off once", self.name, response.status_code)
                    time.sleep(60)
                    continue
                if response.status_code != 200:
                    logger.warning("Odds source %s returned HTTP %s for %s", self.name, response.status_code, url)
                    return None
                return response.text
            except requests.Timeout:
                logger.warning("Odds source %s timed out for %s", self.name, url)
                return None
            except Exception as exc:
                logger.warning("Odds source %s failed for %s: %s", self.name, url, exc)
                return None
        return None

    def _parse_american_odds(self, raw_string: str) -> int | None:
        raw = (raw_string or "").strip().upper()
        if raw in {"EVEN", "EV", "PK", "PICK"}:
            return 100
        match = re.search(r"([+-]?\d{3,4})", raw)
        if not match:
            return None
        value = int(match.group(1))
        if abs(value) in {2024, 2025, 2026, 2027}:
            return None
        if value == 0 or abs(value) > 2500:
            return None
        return value

    def _parse_matchup_from_html(self, html: str, fighter_a_name: str, fighter_b_name: str) -> dict[str, int] | None:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        window = _matchup_window(text, fighter_a_name, fighter_b_name)
        if not window:
            return None
        odds = self._extract_candidate_odds(window)
        if len(odds) < 2:
            return None
        return {"fighter_a_odds": odds[0], "fighter_b_odds": odds[1]}

    def _extract_candidate_odds(self, text: str) -> list[int]:
        candidates: list[int] = []
        for raw in re.findall(r"(?:[+-]\s?\d{3,4}\b|\bEVEN\b|\bEV\b|\bPK\b|\bPICK\b|\b[1-9]\d{2}\b)", text, flags=re.IGNORECASE):
            parsed = self._parse_american_odds(raw.replace(" ", ""))
            if parsed is None:
                continue
            if parsed not in candidates:
                candidates.append(parsed)
            if len(candidates) >= 2:
                break
        return candidates


class BestFightOddsSource(OddsSource):
    name = "bestfightodds"
    base_url = "https://www.bestfightodds.com"
    search_paths = ("/", "/events/")


class FightOddsIOSource(OddsSource):
    name = "fightodds_io"
    base_url = "https://fightodds.io"
    search_paths = ("/", "/mma", "/ufc")


def scrape_all_sources(fight_id: str, fighter_a_name: str, fighter_b_name: str, *, sleep_between: bool = True) -> dict[str, Any]:
    sources: list[OddsSource] = [BestFightOddsSource(), FightOddsIOSource()]
    results: dict[str, dict[str, int]] = {}
    failures: dict[str, str] = {}

    for index, source in enumerate(sources):
        if sleep_between and index > 0:
            time.sleep(random.uniform(2.0, 4.0))
        try:
            result = source.scrape_fight(fighter_a_name, fighter_b_name)
            if result:
                results[source.name] = result
            else:
                failures[source.name] = "matchup_not_found"
                logger.warning("No odds found source=%s fight_id=%s matchup=%s vs %s", source.name, fight_id, fighter_a_name, fighter_b_name)
        except Exception as exc:
            failures[source.name] = str(exc)
            logger.warning("Odds scrape failed source=%s fight_id=%s error=%s", source.name, fight_id, exc)

    if not results:
        return {"success": False, "sources": {}, "averaged": None, "failures": failures}

    implied_a_values = []
    implied_b_values = []
    for source_name, data in results.items():
        raw_a = american_to_implied_raw(data["fighter_a_odds"])
        raw_b = american_to_implied_raw(data["fighter_b_odds"])
        clean_a, clean_b = remove_vig(raw_a, raw_b)
        implied_a_values.append(clean_a)
        implied_b_values.append(clean_b)
        logger.info("Odds source=%s a=%s b=%s implied_a=%.4f implied_b=%.4f", source_name, data["fighter_a_odds"], data["fighter_b_odds"], clean_a, clean_b)

    avg_a = sum(implied_a_values) / len(implied_a_values)
    avg_b = sum(implied_b_values) / len(implied_b_values)
    return {
        "success": True,
        "sources": results,
        "averaged": {"implied_prob_a": avg_a, "implied_prob_b": avg_b},
        "failures": failures,
    }


def _matchup_window(text: str, fighter_a_name: str, fighter_b_name: str) -> str | None:
    normalized = _normalize(text)
    a_norm = _normalize(fighter_a_name)
    b_norm = _normalize(fighter_b_name)
    a_index = normalized.find(a_norm)
    b_index = normalized.find(b_norm)
    if a_index == -1 or b_index == -1 or abs(a_index - b_index) > 2500:
        return None
    start = max(0, min(a_index, b_index) - 800)
    end = min(len(text), max(a_index, b_index) + max(len(fighter_a_name), len(fighter_b_name)) + 1200)
    return text[start:end]


def _normalize(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()
