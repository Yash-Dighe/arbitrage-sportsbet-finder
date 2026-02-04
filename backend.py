from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Dict, Generator, Iterable, Optional, Tuple
import time
import requests
from itertools import chain

try:
    from tqdm import tqdm
except ImportError:
    tqdm = lambda *args, **kwargs: args[0]

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://api.the-odds-api.com/v4"


class APIException(RuntimeError):
    pass


class AuthenticationException(APIException):
    pass


class RateLimitException(APIException):
    pass


class ResponseFormatException(APIException):
    pass


def _session_with_retries(timeout: float) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.request_timeout = timeout
    return session


def handle_faulty_response(response: requests.Response) -> None:
    if response.status_code == 401:
        raise AuthenticationException("Failed to authenticate with the API.")
    if response.status_code == 429:
        raise RateLimitException("Encountered API rate limit.")
    raise APIException(f"API request failed with status {response.status_code}: {response.text[:200]}")


def get_sports(session: requests.Session, key: str) -> set[str]:
    url = f"{BASE_URL}/sports/"
    resp = session.get(url, params={"apiKey": key}, timeout=session.request_timeout)
    if not resp.ok:
        handle_faulty_response(resp)

    data = resp.json()
    if not isinstance(data, list):
        raise ResponseFormatException("Unexpected sports response format.")
    return {item["key"] for item in data if isinstance(item, dict) and "key" in item}


def get_data(session: requests.Session, key: str, sport: str, region: str) -> list[dict]:
    url = f"{BASE_URL}/sports/{sport}/odds/"
    params = {
        "apiKey": key,
        "regions": region,
        "oddsFormat": "decimal",
        "dateFormat": "unix",
    }
    resp = session.get(url, params=params, timeout=session.request_timeout)
    if not resp.ok:
        handle_faulty_response(resp)

    data = resp.json()
    if isinstance(data, dict) and "message" in data:
        return []
    if not isinstance(data, list):
        raise ResponseFormatException("Unexpected odds response format.")
    return [x for x in data if isinstance(x, dict)]


def _select_market(bookmaker: dict, market_key: str) -> Optional[dict]:
    markets = bookmaker.get("markets", [])
    if not isinstance(markets, list):
        return None
    for m in markets:
        if isinstance(m, dict) and m.get("key") == market_key:
            return m
    return None


def _best_odds_for_match(match: dict, market_key: str) -> Dict[str, Tuple[str, float]]:
    best: Dict[str, Tuple[str, float]] = {}
    bookmakers = match.get("bookmakers", [])
    if not isinstance(bookmakers, list):
        return best

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue
        bookie_name = bookmaker.get("title", "Unknown Book")
        market = _select_market(bookmaker, market_key)
        if not market:
            continue

        outcomes = market.get("outcomes", [])
        if not isinstance(outcomes, list):
            continue

        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            name = outcome.get("name")
            price = outcome.get("price")
            if not isinstance(name, str) or not isinstance(price, (int, float)):
                continue
            if price <= 1:
                continue

            if name not in best or price > best[name][1]:
                best[name] = (bookie_name, float(price))

    return best


def _stake_plan(bankroll: float, odds: Dict[str, Tuple[str, float]]) -> Dict[str, float]:
    inv_sum = sum(1 / o for _, o in odds.values())
    if inv_sum <= 0:
        return {}
    return {name: bankroll * (1 / o) / inv_sum for name, (_, o) in odds.items()}


def process_matches(
    matches: Iterable[dict],
    market_key: str,
    bankroll: float,
    include_started_matches: bool,
) -> Generator[dict, None, None]:
    matches = tqdm(matches, desc="Checking matches", leave=False, unit=" matches")

    for match in matches:
        start_time = match.get("commence_time")
        if not isinstance(start_time, (int, float)):
            continue
        start_time = int(start_time)

        if (not include_started_matches) and (start_time < time.time()):
            continue

        best_odds = _best_odds_for_match(match, market_key=market_key)
        if len(best_odds) < 2:
            continue

        total_implied = sum(1 / odd for _, odd in best_odds.values())
        hours_to_start = (start_time - time.time()) / 3600.0

        stakes = _stake_plan(bankroll, best_odds)
        profit_frac = (1.0 / total_implied) - 1.0 if total_implied > 0 else 0.0
        profit_abs = bankroll * profit_frac
        profit_pct = 100.0 * profit_frac

        legs = {
            outcome_name: {
                "bookmaker": bookie,
                "odds": odd,
                "stake": stakes.get(outcome_name, 0.0),
            }
            for outcome_name, (bookie, odd) in best_odds.items()
        }

        yield {
            "match_name": f"{match.get('home_team', '?')} v. {match.get('away_team', '?')}",
            "league": match.get("sport_key", "unknown"),
            "market_key": market_key,
            "match_start_time": start_time,
            "hours_to_start": hours_to_start,
            "total_implied_odds": total_implied,
            "bankroll": bankroll,
            "profit_pct": profit_pct,
            "profit_abs": profit_abs,
            "legs": legs,
        }


def get_arbitrage_opportunities(
    key: str,
    region: str,
    market_key: str = "h2h",
    cutoff: float = 0.0,
    bankroll: float = 100.0,
    include_started_matches: bool = False,
    timeout: float = 10.0,
):
    session = _session_with_retries(timeout=timeout)

    sports = get_sports(session, key)
    all_matches = chain.from_iterable(
        get_data(session, key, sport, region=region) for sport in sports
    )

    processed = process_matches(
        all_matches,
        market_key=market_key,
        bankroll=bankroll,
        include_started_matches=include_started_matches,
    )

    return (m for m in processed if 0 < m["total_implied_odds"] < (1.0 - cutoff))
    parser = argparse.ArgumentParser(
        description="Find sports betting arbitrage opportunities using The Odds API."
    ) 
