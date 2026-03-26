from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from prediction_math import (
    OrderLevel,
    SolverUnavailable,
    solve_winner_bundle_arbitrage,
    two_outcome_executable_buy_arbitrage,
    two_outcome_sell_arbitrage,
)


KALSHI_BASE_URL = os.environ.get("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
POLYMARKET_GAMMA_URL = os.environ.get("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")
POLYMARKET_CLOB_URL = os.environ.get("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")

_STOP_WORDS = frozenset({
    "will", "the", "a", "an", "be", "is", "are", "was", "were",
    "in", "on", "at", "to", "of", "for", "by", "with", "from",
    "or", "and", "not", "this", "that", "it", "its", "does", "do",
    "did", "has", "have", "had", "who", "what", "when", "where",
    "which", "how", "if", "as", "than", "then", "so", "but",
    "can", "could", "would", "should", "may", "might", "us",
})

# Generic tokens shared by many political/electoral markets that carry no
# discriminating power when checking if two questions are the same event.
# A matched pair must share at least one token NOT in this set.
_CROSS_GENERIC_TOKENS = frozenset({
    "win", "wins", "winning", "winner", "won",
    "election", "elections", "electoral",
    "presidential", "president", "presidency",
    "vote", "votes", "voting", "voter", "voters",
    "democratic", "republican", "gop",
    "nomination", "nominee", "nominate",
    "primary", "general", "runoff",
    "next", "upcoming",
    "usa", "united", "states", "american", "america",
    "race", "seat", "party",
    "first", "second", "third",
    "become",
    # Years alone are too generic: every 2028 election market shares the year
    "2024", "2025", "2026", "2027", "2028", "2029", "2030",
    # Rules/resolution boilerplate shared by all markets
    "market", "markets", "resolve", "resolves", "resolved", "resolution",
    "yes", "no", "otherwise",
    "announced", "announcement", "officially", "official",
    "contract", "contracts", "criteria", "condition",
    "above", "below", "before", "after", "date",
    "based", "including", "included", "regardless",
    "actor", "film", "movie", "series",
    "cast", "casting", "role",
})


def _question_tokens(question: str) -> frozenset:
    words = re.sub(r"[^a-z0-9\s]", "", question.lower()).split()
    return frozenset(w for w in words if w not in _STOP_WORDS and len(w) > 1)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _has_entity_overlap(a: frozenset, b: frozenset) -> bool:
    """Return True if the two token sets share at least one specific entity token.

    Filters out pairs that only share generic electoral vocabulary (e.g. "win",
    "election", "presidential") without any specific anchor like a candidate
    name, year, or other concrete entity.
    """
    shared = a & b
    return bool(shared - _CROSS_GENERIC_TOKENS)


class PredictionAPIException(RuntimeError):
    pass


@dataclass
class ProviderStats:
    markets_seen: int = 0
    missing_tokens: int = 0
    prefilter_skipped: int = 0
    orderbook_errors: int = 0
    empty_books: int = 0
    opportunities: int = 0


def _session_with_retries(timeout: float) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
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


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_listish(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _normalize_levels(raw_levels: Any) -> List[OrderLevel]:
    levels: List[OrderLevel] = []

    if isinstance(raw_levels, dict):
        for k, v in raw_levels.items():
            price = _to_float(k)
            volume = _to_float(v)
            if price is None or volume is None:
                continue
            if price > 1.0:
                price = price / 100.0
            if price <= 0 or volume <= 0:
                continue
            levels.append(OrderLevel(price=price, volume=volume))
        return levels

    if not isinstance(raw_levels, list):
        return levels

    for row in raw_levels:
        price: Optional[float] = None
        volume: Optional[float] = None

        if isinstance(row, dict):
            price = _to_float(row.get("price"))
            if price is None:
                price = _to_float(row.get("p"))
            volume = _to_float(row.get("size"))
            if volume is None:
                volume = _to_float(row.get("quantity"))
            if volume is None:
                volume = _to_float(row.get("q"))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            price = _to_float(row[0])
            volume = _to_float(row[1])

        if price is None or volume is None:
            continue
        if price > 1.0:
            price = price / 100.0
        if price <= 0 or volume <= 0:
            continue
        levels.append(OrderLevel(price=price, volume=volume))

    return levels


def _invert_bids_to_asks(bids: List[OrderLevel]) -> List[OrderLevel]:
    asks: List[OrderLevel] = []
    for lvl in bids:
        ask_price = 1.0 - lvl.price
        if 0 < ask_price < 1:
            asks.append(OrderLevel(price=ask_price, volume=lvl.volume))
    return asks


def _sample_rows(raw_levels: Any) -> List[Any]:
    if isinstance(raw_levels, list):
        return raw_levels[:2]
    return []


def _print_kalshi_sample(ticker: str, payload: Any) -> None:
    if not isinstance(payload, dict):
        print(f"[kalshi-sample:{ticker}] payload_type={type(payload).__name__}", file=sys.stderr)
        return

    container = payload.get("orderbook") if isinstance(payload.get("orderbook"), dict) else payload
    summary: Dict[str, Any] = {
        "ticker": ticker,
        "top_keys": list(payload.keys())[:20],
        "container_keys": list(container.keys())[:20] if isinstance(container, dict) else [],
    }
    if isinstance(container, dict):
        summary["yes_sample"] = _sample_rows(container.get("yes"))
        summary["no_sample"] = _sample_rows(container.get("no"))
        summary["yes_dollars_type"] = type(container.get("yes_dollars")).__name__
        summary["no_dollars_type"] = type(container.get("no_dollars")).__name__
        summary["yes_asks_sample"] = _sample_rows(container.get("yes_asks") or container.get("asks_yes"))
        summary["no_asks_sample"] = _sample_rows(container.get("no_asks") or container.get("asks_no"))

    print(f"[kalshi-sample] {json.dumps(summary, default=str)[:1000]}", file=sys.stderr)


def _print_poly_sample(market_id: str, yes_book: Any, no_book: Any) -> None:
    def summarize(book: Any) -> Dict[str, Any]:
        if not isinstance(book, dict):
            return {"type": type(book).__name__}
        return {
            "keys": list(book.keys())[:20],
            "asks_sample": _sample_rows(book.get("asks")),
            "bids_sample": _sample_rows(book.get("bids")),
        }

    summary = {
        "market_id": market_id,
        "yes_book": summarize(yes_book),
        "no_book": summarize(no_book),
    }
    print(f"[polymarket-sample] {json.dumps(summary, default=str)[:1000]}", file=sys.stderr)


def _get_json(session: requests.Session, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    resp = session.get(url, params=params or {}, timeout=session.request_timeout)
    if not resp.ok:
        raise PredictionAPIException(f"Request failed ({resp.status_code}) for {url}: {resp.text[:180]}")
    return resp.json()


def _kalshi_orderbook_to_asks(orderbook_payload: Dict[str, Any]) -> Tuple[List[OrderLevel], List[OrderLevel]]:
    container = orderbook_payload.get("orderbook") if isinstance(orderbook_payload, dict) else None
    if not isinstance(container, dict):
        container = orderbook_payload
    if not isinstance(container, dict):
        return [], []

    yes_bids = _normalize_levels(container.get("yes"))
    no_bids = _normalize_levels(container.get("no"))
    if not yes_bids:
        yes_bids = _normalize_levels(container.get("yes_dollars"))
    if not no_bids:
        no_bids = _normalize_levels(container.get("no_dollars"))

    yes_asks = _normalize_levels(container.get("yes_asks") or container.get("asks_yes"))
    no_asks = _normalize_levels(container.get("no_asks") or container.get("asks_no"))

    if not yes_asks and no_bids:
        yes_asks = _invert_bids_to_asks(no_bids)
    if not no_asks and yes_bids:
        no_asks = _invert_bids_to_asks(yes_bids)

    return yes_asks, no_asks


def _first_market_numeric(market: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        val = _to_float(market.get(key))
        if val is not None:
            return val
    return None


def _kalshi_market_snapshot_asks(market: Dict[str, Any]) -> Tuple[List[OrderLevel], List[OrderLevel]]:
    yes_ask = _first_market_numeric(market, [
        "yes_ask", "yesAsk", "best_yes_ask", "bestYesAsk", "ask_yes", "yes_ask_dollars",
    ])
    no_ask = _first_market_numeric(market, [
        "no_ask", "noAsk", "best_no_ask", "bestNoAsk", "ask_no", "no_ask_dollars",
    ])
    yes_bid = _first_market_numeric(market, [
        "yes_bid", "yesBid", "best_yes_bid", "bestYesBid", "bid_yes", "yes_bid_dollars",
    ])
    no_bid = _first_market_numeric(market, [
        "no_bid", "noBid", "best_no_bid", "bestNoBid", "bid_no", "no_bid_dollars",
    ])

    if yes_ask is None and no_bid is not None:
        yes_ask = 1.0 - no_bid
    if no_ask is None and yes_bid is not None:
        no_ask = 1.0 - yes_bid

    if yes_ask is not None and yes_ask > 1.0:
        yes_ask = yes_ask / 100.0
    if no_ask is not None and no_ask > 1.0:
        no_ask = no_ask / 100.0

    if yes_ask is None or no_ask is None:
        return [], []
    if yes_ask <= 0 or yes_ask >= 1 or no_ask <= 0 or no_ask >= 1:
        return [], []

    fallback_sz = _first_market_numeric(market, ["volume", "open_interest", "openInterest", "liquidity"]) or 1.0
    return [OrderLevel(price=yes_ask, volume=fallback_sz)], [OrderLevel(price=no_ask, volume=fallback_sz)]


def _kalshi_prefilter_pass(market: Dict[str, Any], min_liquidity: float) -> bool:
    for key in [
        "yes_ask", "yesAsk", "best_yes_ask", "bestYesAsk", "ask_yes",
        "no_ask", "noAsk", "best_no_ask", "bestNoAsk", "ask_no",
        "yes_bid", "yesBid", "best_yes_bid", "bestYesBid", "bid_yes",
        "no_bid", "noBid", "best_no_bid", "bestNoBid", "bid_no",
    ]:
        val = _to_float(market.get(key))
        if val is None:
            continue
        if val > 1.0:
            val = val / 100.0
        if 0 < val < 1:
            return True

    for key in ["volume", "open_interest", "openInterest", "liquidity", "dollar_volume", "recent_volume"]:
        val = _to_float(market.get(key))
        if val is not None and val >= min_liquidity:
            return True

    return False


def _polymarket_token_ids(market: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    yes_token: Optional[str] = None
    no_token: Optional[str] = None

    tokens = market.get("tokens")
    if isinstance(tokens, list):
        for token in tokens:
            if not isinstance(token, dict):
                continue
            outcome = str(token.get("outcome", "")).strip().lower()
            token_id = token.get("token_id") or token.get("id")
            if token_id is None:
                continue
            token_id = str(token_id)
            if outcome == "yes" and yes_token is None:
                yes_token = token_id
            if outcome == "no" and no_token is None:
                no_token = token_id

    if yes_token and no_token:
        return yes_token, no_token

    clob_ids = [str(x) for x in _parse_listish(market.get("clobTokenIds")) if x is not None]
    outcomes = [str(x).strip().lower() for x in _parse_listish(market.get("outcomes"))]

    if clob_ids and outcomes and len(clob_ids) == len(outcomes):
        for idx, outcome in enumerate(outcomes):
            if outcome == "yes" and yes_token is None:
                yes_token = clob_ids[idx]
            if outcome == "no" and no_token is None:
                no_token = clob_ids[idx]

    if (not yes_token or not no_token) and len(clob_ids) >= 2:
        yes_token = yes_token or clob_ids[0]
        no_token = no_token or clob_ids[1]

    return yes_token, no_token


def _polymarket_market_url(market: Dict[str, Any]) -> Optional[str]:
    slug = market.get("slug") or market.get("market_slug") or market.get("eventSlug") or market.get("event_slug")
    if isinstance(slug, str) and slug.strip():
        return f"https://polymarket.com/market/{slug.strip()}"
    return None


def _extract_winner_event(question: str) -> Optional[str]:
    q = question.strip().rstrip("?")
    match = re.match(r"^Will\s+.+?\s+win\s+the\s+(.+)$", q, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().lower()


def _is_exhaustive_bundle(
    questions: List[str],
    assume_exhaustive: bool,
    strict_bundle_completeness: bool,
) -> bool:
    combined = " | ".join(q.lower() for q in questions)
    has_explicit_other = ("other" in combined) or ("none" in combined) or ("no one" in combined)

    if strict_bundle_completeness:
        return has_explicit_other
    if assume_exhaustive:
        return True
    return has_explicit_other


def _kalshi_opportunities(
    session: requests.Session,
    min_edge: float,
    min_profit_per_contract: float,
    limit: int,
    stats: ProviderStats,
    debug_sample: int = 0,
    prefilter: bool = True,
    min_liquidity: float = 1.0,
) -> Generator[Dict[str, Any], None, None]:
    markets_url = f"{KALSHI_BASE_URL.rstrip('/')}/trade-api/v2/markets"
    payload = _get_json(session, markets_url, params={"status": "open", "limit": max(1, limit)})
    markets = payload.get("markets") if isinstance(payload, dict) else payload
    if not isinstance(markets, list):
        return

    sample_left = max(0, debug_sample)
    for market in markets:
        if not isinstance(market, dict):
            continue
        stats.markets_seen += 1

        ticker = str(market.get("ticker", "")).strip()
        if not ticker:
            stats.missing_tokens += 1
            continue

        if prefilter and not _kalshi_prefilter_pass(market, min_liquidity=min_liquidity):
            stats.prefilter_skipped += 1
            continue

        orderbook_url = f"{KALSHI_BASE_URL.rstrip('/')}/trade-api/v2/markets/{ticker}/orderbook"
        try:
            ob = _get_json(session, orderbook_url)
        except PredictionAPIException:
            stats.orderbook_errors += 1
            continue

        if sample_left > 0:
            _print_kalshi_sample(ticker=ticker, payload=ob)
            sample_left -= 1

        yes_asks, no_asks = _kalshi_orderbook_to_asks(ob)
        if not yes_asks or not no_asks:
            yes_asks, no_asks = _kalshi_market_snapshot_asks(market)
        if not yes_asks or not no_asks:
            stats.empty_books += 1
            continue

        result = two_outcome_executable_buy_arbitrage(
            yes_asks=yes_asks,
            no_asks=no_asks,
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
        )

        # Also check sell-side: bid_yes + bid_no > 1
        container = ob.get("orderbook") if isinstance(ob, dict) else ob
        if not isinstance(container, dict):
            container = ob
        yes_bids = _normalize_levels(container.get("yes") if isinstance(container, dict) else None)
        no_bids = _normalize_levels(container.get("no") if isinstance(container, dict) else None)
        sell_result = two_outcome_sell_arbitrage(
            yes_bids=yes_bids,
            no_bids=no_bids,
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
        ) if yes_bids and no_bids else None

        if not result and not sell_result:
            continue

        stats.opportunities += 1
        base = {
            "strategy": "pairwise_binary",
            "match_name": market.get("title") or market.get("subtitle") or ticker,
            "source": "kalshi",
            "market_id": ticker,
        }
        if result:
            yield {**base, **result, "profit_pct": result["profit_per_contract"] * 100.0}
        if sell_result:
            yield {**base, **sell_result, "profit_pct": sell_result["profit_per_contract"] * 100.0}


def _polymarket_pairwise_opportunities(
    session: requests.Session,
    min_edge: float,
    min_profit_per_contract: float,
    limit: int,
    stats: ProviderStats,
    debug_sample: int = 0,
) -> Generator[Dict[str, Any], None, None]:
    markets_url = f"{POLYMARKET_GAMMA_URL.rstrip('/')}/markets"
    markets = _get_json(session, markets_url, params={"active": "true", "closed": "false", "limit": max(1, limit)})
    if not isinstance(markets, list):
        return

    sample_left = max(0, debug_sample)
    for market in markets:
        if not isinstance(market, dict):
            continue
        stats.markets_seen += 1

        if market.get("enableOrderBook") is False:
            stats.prefilter_skipped += 1
            continue

        yes_token, no_token = _polymarket_token_ids(market)
        if not yes_token or not no_token:
            stats.missing_tokens += 1
            continue

        book_url = f"{POLYMARKET_CLOB_URL.rstrip('/')}/book"
        try:
            yes_book = _get_json(session, book_url, params={"token_id": yes_token})
            no_book = _get_json(session, book_url, params={"token_id": no_token})
        except PredictionAPIException:
            stats.orderbook_errors += 1
            continue

        market_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or yes_token)
        if sample_left > 0:
            _print_poly_sample(market_id, yes_book, no_book)
            sample_left -= 1

        yes_asks = _normalize_levels(yes_book.get("asks") if isinstance(yes_book, dict) else None)
        no_asks = _normalize_levels(no_book.get("asks") if isinstance(no_book, dict) else None)
        yes_bids = _normalize_levels(yes_book.get("bids") if isinstance(yes_book, dict) else None)
        no_bids = _normalize_levels(no_book.get("bids") if isinstance(no_book, dict) else None)

        if not yes_asks and not yes_bids:
            stats.empty_books += 1
            continue

        result = two_outcome_executable_buy_arbitrage(
            yes_asks=yes_asks,
            no_asks=no_asks,
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
        ) if yes_asks and no_asks else None

        sell_result = two_outcome_sell_arbitrage(
            yes_bids=yes_bids,
            no_bids=no_bids,
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
        ) if yes_bids and no_bids else None

        if not result and not sell_result:
            continue

        stats.opportunities += 1
        base = {
            "strategy": "pairwise_binary",
            "match_name": market.get("question") or market.get("title") or market_id,
            "source": "polymarket",
            "market_id": market_id,
            "link": _polymarket_market_url(market),
        }
        if result:
            yield {**base, **result, "profit_pct": result["profit_per_contract"] * 100.0}
        if sell_result:
            yield {**base, **sell_result, "profit_pct": sell_result["profit_per_contract"] * 100.0}


def _polymarket_combinatorial_opportunities(
    session: requests.Session,
    min_profit_per_contract: float,
    limit: int,
    stats: ProviderStats,
    debug_sample: int,
    assume_exhaustive: bool,
    strict_bundle_completeness: bool,
    levels_per_contract: int,
    integer_positions: bool,
    fee_bps: float,
    slippage_bps: float,
) -> Generator[Dict[str, Any], None, None]:
    markets_url = f"{POLYMARKET_GAMMA_URL.rstrip('/')}/markets"
    markets = _get_json(session, markets_url, params={"active": "true", "closed": "false", "limit": max(1, limit)})
    if not isinstance(markets, list):
        return

    sample_left = max(0, debug_sample)
    bundles: Dict[str, List[Dict[str, Any]]] = {}

    for market in markets:
        if not isinstance(market, dict):
            continue
        stats.markets_seen += 1

        question = str(market.get("question") or market.get("title") or "").strip()
        bundle_key = _extract_winner_event(question)
        if not bundle_key:
            stats.prefilter_skipped += 1
            continue

        yes_token, no_token = _polymarket_token_ids(market)
        if not yes_token or not no_token:
            stats.missing_tokens += 1
            continue

        book_url = f"{POLYMARKET_CLOB_URL.rstrip('/')}/book"
        try:
            yes_book = _get_json(session, book_url, params={"token_id": yes_token})
            no_book = _get_json(session, book_url, params={"token_id": no_token})
        except PredictionAPIException:
            stats.orderbook_errors += 1
            continue

        market_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or yes_token)
        if sample_left > 0:
            _print_poly_sample(market_id, yes_book, no_book)
            sample_left -= 1

        yes_asks = _normalize_levels(yes_book.get("asks") if isinstance(yes_book, dict) else None)
        if not yes_asks:
            stats.empty_books += 1
            continue

        yes_asks = sorted(yes_asks, key=lambda x: x.price)[: max(1, levels_per_contract)]

        # Also collect NO ask levels so the LP can consider "buy all NO" when sum(YES) > 1
        no_asks_combo = _normalize_levels(no_book.get("asks") if isinstance(no_book, dict) else None)
        no_asks_combo = sorted(no_asks_combo, key=lambda x: x.price)[: max(1, levels_per_contract)]

        contract_entry = {
            "contract_id": market_id,
            "label": question,
            "levels": yes_asks,
            "no_levels": no_asks_combo,
            "link": _polymarket_market_url(market),
        }

        bundles.setdefault(bundle_key, []).append(contract_entry)

        # Also group by Polymarket event slug for cross-condition detection.
        # Markets sharing an eventSlug are related even when the regex doesn't fire.
        event_slug = market.get("eventSlug") or market.get("event_slug")
        if isinstance(event_slug, str) and event_slug.strip():
            slug_key = f"__slug__{event_slug.strip().lower()}"
            if slug_key != bundle_key:
                bundles.setdefault(slug_key, []).append(contract_entry)

    for bundle_key, contracts in bundles.items():
        if len(contracts) < 2:
            continue

        # Event-slug bundles may not be exhaustive winner markets; only run them
        # if assume_exhaustive is set (user accepts the risk), otherwise skip the
        # exhaustiveness check so the LP itself determines profitability.
        is_slug_bundle = bundle_key.startswith("__slug__")
        if not is_slug_bundle:
            labels = [str(c["label"]) for c in contracts]
            if not _is_exhaustive_bundle(
                labels,
                assume_exhaustive=assume_exhaustive,
                strict_bundle_completeness=strict_bundle_completeness,
            ):
                stats.prefilter_skipped += 1
                continue

        try:
            result = solve_winner_bundle_arbitrage(
                bundle_id=bundle_key,
                contracts=contracts,
                min_profit_per_contract=min_profit_per_contract,
                integer_positions=integer_positions,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
        except SolverUnavailable as exc:
            raise PredictionAPIException(str(exc))

        if not result:
            continue

        stats.opportunities += 1

        if is_slug_bundle:
            display_name = bundle_key.removeprefix("__slug__")
            strategy_label = "combinatorial_ip_cross"
            assumptions = [
                "Markets grouped by shared Polymarket event slug (may not be exhaustive).",
                "LP finds worst-case profitable trade across all possible winner states.",
                f"Post-cost guarantee models fees={fee_bps:.2f} bps and slippage={slippage_bps:.2f} bps.",
                "Verify logical dependency before executing — not all markets in the slug are necessarily mutually exclusive.",
            ]
        else:
            display_name = bundle_key
            strategy_label = "combinatorial_ip"
            assumptions = [
                "Exactly one contract in this bundle resolves YES (mutually exclusive + exhaustive).",
                f"Post-cost guarantee models fees={fee_bps:.2f} bps and slippage={slippage_bps:.2f} bps.",
                "Orders fill at modeled ask prices and available size.",
                "No void/cancelled market settlement deviations.",
            ]

        yield {
            "strategy": strategy_label,
            "match_name": display_name,
            "source": "polymarket",
            "market_id": bundle_key,
            "link": next((str(c["link"]) for c in contracts if c.get("link")), None),
            **result,
            "profit_pct": result["profit_per_contract"] * 100.0,
            "assumptions": assumptions,
        }


def _cross_exchange_opportunities(
    session: requests.Session,
    min_edge: float,
    min_profit_per_contract: float,
    limit: int,
    stats: ProviderStats,
    similarity_threshold: float = 0.5,
    levels_per_contract: int = 5,
    debug_sample: int = 0,
    status_callback=None,
) -> Generator[Dict[str, Any], None, None]:
    """
    Cross-exchange arbitrage: find matching markets on Kalshi and Polymarket,
    then check if buying YES on one platform + NO on the other costs less than $1.
    """
    def _status(msg: str) -> None:
        if status_callback:
            status_callback(msg)

    # --- Kalshi: fetch events with nested markets (paginated, max 200/page) ---
    _status("Fetching Kalshi markets...")
    kalshi_events_url = f"{KALSHI_BASE_URL.rstrip('/')}/trade-api/v2/events"
    kalshi_page_size = 200  # API hard limit
    kalshi_items: List[Dict[str, Any]] = []
    kalshi_cursor: Optional[str] = None
    kalshi_total_seen = 0
    while kalshi_total_seen < limit:
        params: Dict[str, Any] = {
            "status": "open",
            "limit": min(kalshi_page_size, limit - kalshi_total_seen),
            "with_nested_markets": "true",
        }
        if kalshi_cursor:
            params["cursor"] = kalshi_cursor
        try:
            kalshi_payload = _get_json(session, kalshi_events_url, params=params)
        except PredictionAPIException:
            break
        kalshi_events = kalshi_payload.get("events") if isinstance(kalshi_payload, dict) else []
        if not isinstance(kalshi_events, list) or not kalshi_events:
            break
        for event in kalshi_events:
            if not isinstance(event, dict):
                continue
            event_title = str(event.get("title") or "").strip()
            series_ticker = str(event.get("series_ticker") or "").strip()
            for m in event.get("markets", []):
                if not isinstance(m, dict):
                    continue
                # Skip MVE (multi-variate event) composite markets
                if m.get("mve_collection_ticker"):
                    continue
                ticker = str(m.get("ticker", "")).strip()
                if not ticker:
                    continue
                # Prefer market-level title, fall back to event title
                title = str(m.get("title") or m.get("subtitle") or event_title or ticker).strip()
                yes_asks_snap, no_asks_snap = _kalshi_market_snapshot_asks(m)
                if not yes_asks_snap or not no_asks_snap:
                    continue
                event_ticker = str(m.get("event_ticker") or ticker).strip()
                rules = " ".join(filter(None, [
                    str(m.get("rules_primary") or ""),
                    str(m.get("rules_secondary") or ""),
                ]))
                kalshi_items.append({
                    "ticker": ticker,
                    "event_ticker": event_ticker,
                    "series_ticker": series_ticker,
                    "title": title,
                    "tokens": _question_tokens(title),           # title-only for Jaccard
                    "rule_tokens": _question_tokens(f"{title} {rules}"),  # enriched for entity check
                    "yes_asks": yes_asks_snap[:levels_per_contract],
                    "no_asks": no_asks_snap[:levels_per_contract],
                })
        kalshi_total_seen += len(kalshi_events)
        kalshi_cursor = kalshi_payload.get("cursor") if isinstance(kalshi_payload, dict) else None
        if not kalshi_cursor or len(kalshi_events) < kalshi_page_size:
            break  # no more pages

    if not kalshi_items:
        return

    _status(f"Fetching Polymarket markets... ({len(kalshi_items)} Kalshi markets loaded)")
    # --- Polymarket: fetch market metadata (paginated until limit reached) ---
    poly_url = f"{POLYMARKET_GAMMA_URL.rstrip('/')}/markets"
    page_size = min(500, max(1, limit))
    poly_items: List[Dict[str, Any]] = []
    offset = 0
    while len(poly_items) < limit:
        try:
            poly_raw = _get_json(session, poly_url,
                                 params={"active": "true", "closed": "false",
                                         "limit": page_size, "offset": offset})
        except PredictionAPIException:
            break
        if not isinstance(poly_raw, list) or not poly_raw:
            break
        for m in poly_raw:
            if not isinstance(m, dict):
                continue
            if m.get("enableOrderBook") is False:
                continue
            yes_token, no_token = _polymarket_token_ids(m)
            if not yes_token or not no_token:
                continue
            question = str(m.get("question") or m.get("title") or "").strip()
            if not question:
                continue
            market_id = str(m.get("id") or m.get("conditionId") or m.get("slug") or yes_token)
            description = str(m.get("description") or "")
            poly_items.append({
                "market_id": market_id,
                "question": question,
                "tokens": _question_tokens(question),                          # title-only for Jaccard
                "rule_tokens": _question_tokens(f"{question} {description}"),  # enriched for entity check
                "yes_token": yes_token,
                "no_token": no_token,
                "link": _polymarket_market_url(m),
            })
        if len(poly_raw) < page_size:
            break  # last page
        offset += page_size

    if not poly_items:
        return

    book_url = f"{POLYMARKET_CLOB_URL.rstrip('/')}/book"
    seen_pairs: set = set()
    sample_left = max(0, debug_sample)

    _status(f"Matching {len(kalshi_items)} Kalshi vs {len(poly_items)} Polymarket markets...")
    for k in kalshi_items:
        # Find the single best-matching Polymarket market by Jaccard similarity
        best_sim = 0.0
        best_p: Optional[Dict[str, Any]] = None
        for p in poly_items:
            sim = _jaccard(k["tokens"], p["tokens"])
            if sim > best_sim:
                best_sim = sim
                best_p = p

        if best_sim < similarity_threshold or best_p is None:
            continue
        # Require at least one shared entity token from the rules-enriched token
        # sets (title + resolution criteria). Using rule_tokens gives better
        # false-positive rejection than title tokens alone — two markets that
        # share only generic electoral vocabulary in their titles but have
        # no specific entity (name, org, threshold) in common are filtered out.
        if not _has_entity_overlap(k["rule_tokens"], best_p["rule_tokens"]):
            continue

        pair_key = (k["ticker"], best_p["market_id"])
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        # Fetch Polymarket CLOB only for this matched pair
        try:
            yes_book = _get_json(session, book_url, params={"token_id": best_p["yes_token"]})
            no_book = _get_json(session, book_url, params={"token_id": best_p["no_token"]})
        except PredictionAPIException:
            stats.orderbook_errors += 1
            continue

        if sample_left > 0:
            _print_poly_sample(best_p["market_id"], yes_book, no_book)
            sample_left -= 1

        poly_yes_asks = _normalize_levels(yes_book.get("asks") if isinstance(yes_book, dict) else None)
        poly_no_asks = _normalize_levels(no_book.get("asks") if isinstance(no_book, dict) else None)
        poly_yes_bids = _normalize_levels(yes_book.get("bids") if isinstance(yes_book, dict) else None)
        poly_no_bids = _normalize_levels(no_book.get("bids") if isinstance(no_book, dict) else None)

        stats.markets_seen += 1

        series = k.get("series_ticker") or k["event_ticker"]
        kalshi_link = f"https://kalshi.com/markets/{series}/{k['event_ticker']}"
        base = {
            "strategy": "cross_exchange",
            "source": "kalshi+polymarket",
            "market_id": f"{k['ticker']}+{best_p['market_id']}",
            "link": best_p["link"],
            "kalshi_link": kalshi_link,
            "poly_link": best_p["link"],
            "similarity": round(best_sim, 3),
            "kalshi_ticker": k["ticker"],
            "poly_market_id": best_p["market_id"],
        }

        # Direction A: buy YES on Kalshi + buy NO on Polymarket
        r = two_outcome_executable_buy_arbitrage(
            yes_asks=k["yes_asks"],
            no_asks=poly_no_asks,
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
        ) if poly_no_asks else None
        if r:
            stats.opportunities += 1
            yield {
                **base,
                **r,
                "match_name": f"{k['title']}  vs {best_p['question']}",
                "direction": "YES@Kalshi + NO@Polymarket",
                "profit_pct": r["profit_per_contract"] * 100.0,
            }

        # Direction B: buy YES on Polymarket + buy NO on Kalshi
        r = two_outcome_executable_buy_arbitrage(
            yes_asks=poly_yes_asks,
            no_asks=k["no_asks"],
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
        ) if poly_yes_asks else None
        if r:
            stats.opportunities += 1
            yield {
                **base,
                **r,
                "match_name": f"{best_p['question']}  vs {k['title']}",
                "direction": "YES@Polymarket + NO@Kalshi",
                "profit_pct": r["profit_per_contract"] * 100.0,
            }

        # Direction C: sell YES on Kalshi + sell NO on Polymarket (bid_K_yes + bid_P_no > 1)
        r = two_outcome_sell_arbitrage(
            yes_bids=k["yes_asks"],  # Kalshi snapshot gives ask; bid ≈ 1-no_ask
            no_bids=poly_no_bids,
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
        ) if poly_no_bids else None
        if r:
            stats.opportunities += 1
            yield {
                **base,
                **r,
                "match_name": f"{k['title']}  vs {best_p['question']}",
                "direction": "SELL YES@Kalshi + SELL NO@Polymarket",
                "profit_pct": r["profit_per_contract"] * 100.0,
            }


def _print_stats(provider: str, stats: ProviderStats) -> None:
    print(
        f"[{provider}] seen={stats.markets_seen} prefilter_skipped={stats.prefilter_skipped} "
        f"missing_tokens={stats.missing_tokens} orderbook_errors={stats.orderbook_errors} "
        f"empty_books={stats.empty_books} opportunities={stats.opportunities}",
        file=sys.stderr,
    )


def get_prediction_opportunities(
    source: str = "all",
    min_edge: float = 0.02,
    min_profit_per_contract: float = 0.01,
    timeout: float = 10.0,
    limit: int = 500,
    debug: bool = False,
    debug_sample: int = 0,
    kalshi_prefilter: bool = True,
    kalshi_min_liquidity: float = 1.0,
    strategy: str = "combinatorial",
    assume_exhaustive: bool = True,
    strict_bundle_completeness: bool = True,
    levels_per_contract: int = 5,
    integer_positions: bool = False,
    fee_bps: float = 5.0,
    slippage_bps: float = 10.0,
    cross_similarity_threshold: float = 0.4,
    status_callback=None,
) -> Generator[Dict[str, Any], None, None]:
    session = _session_with_retries(timeout=timeout)
    picked = source.strip().lower()
    mode = strategy.strip().lower()

    if picked not in {"all", "kalshi", "polymarket", "cross"}:
        raise ValueError("source must be one of: all, kalshi, polymarket, cross")
    if mode not in {"pairwise", "combinatorial"}:
        raise ValueError("strategy must be one of: pairwise, combinatorial")

    kalshi_stats = ProviderStats()
    poly_stats = ProviderStats()

    if picked in {"all", "kalshi"}:
        yield from _kalshi_opportunities(
            session=session,
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
            limit=limit,
            stats=kalshi_stats,
            debug_sample=debug_sample,
            prefilter=kalshi_prefilter,
            min_liquidity=kalshi_min_liquidity,
        )

    if picked in {"all", "polymarket"}:
        if mode == "pairwise":
            yield from _polymarket_pairwise_opportunities(
                session=session,
                min_edge=min_edge,
                min_profit_per_contract=min_profit_per_contract,
                limit=limit,
                stats=poly_stats,
                debug_sample=debug_sample,
            )
        else:
            yield from _polymarket_combinatorial_opportunities(
                session=session,
                min_profit_per_contract=min_profit_per_contract,
                limit=limit,
                stats=poly_stats,
                debug_sample=debug_sample,
                assume_exhaustive=assume_exhaustive,
                strict_bundle_completeness=strict_bundle_completeness,
                levels_per_contract=levels_per_contract,
                integer_positions=integer_positions,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )

    cross_stats = ProviderStats()
    if picked in {"all", "cross"}:
        yield from _cross_exchange_opportunities(
            session=session,
            min_edge=min_edge,
            min_profit_per_contract=min_profit_per_contract,
            limit=limit,
            stats=cross_stats,
            status_callback=status_callback,
            similarity_threshold=cross_similarity_threshold,
            levels_per_contract=levels_per_contract,
            debug_sample=debug_sample,
        )

    if debug:
        if picked in {"all", "kalshi"}:
            _print_stats("kalshi", kalshi_stats)
        if picked in {"all", "polymarket"}:
            _print_stats("polymarket", poly_stats)
        if picked in {"all", "cross"}:
            _print_stats("cross", cross_stats)
