from __future__ import annotations

import json as _json
import re
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

from prediction_backend import (
    KALSHI_BASE_URL,
    POLYMARKET_CLOB_URL,
    POLYMARKET_GAMMA_URL,
    PredictionAPIException,
    _get_json,
    _normalize_levels,
    _session_with_retries,
)

KALSHI_GAME_SERIES  = "KXNCAAMBGAME"
KALSHI_TOTAL_SERIES = "KXNCAAMBTOTAL"
DEFAULT_ARB_THRESHOLD = 1.0


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_listish(val: Any) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = _json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _clob_best_ask(session: Any, token_id: Optional[str]) -> Optional[float]:
    if not token_id:
        return None
    book_url = f"{POLYMARKET_CLOB_URL.rstrip('/')}/book"
    try:
        book = _get_json(session, book_url, params={"token_id": token_id})
        asks = _normalize_levels(book.get("asks") if isinstance(book, dict) else None)
        if not asks:
            return None
        return min(lvl.price for lvl in asks)
    except PredictionAPIException:
        return None


def _dates_match(d1: str, d2: str) -> bool:
    """True if two YYYY-MM-DD strings are the same day or adjacent (timezone buffer)."""
    if not d1 or not d2:
        return False
    if d1 == d2:
        return True
    try:
        delta = abs((datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d2, "%Y-%m-%d")).days)
        return delta <= 1
    except ValueError:
        return False


# ── Kalshi fetching ───────────────────────────────────────────────────────────

def _fetch_kalshi_series(session: Any, series_ticker: str, status_callback=None) -> List[Dict]:
    """Fetch all open markets for a Kalshi series, paginated."""
    def _s(msg: str) -> None:
        if status_callback:
            status_callback(msg)

    url = f"{KALSHI_BASE_URL.rstrip('/')}/trade-api/v2/markets"
    all_markets: List[Dict] = []
    cursor: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"status": "open", "series_ticker": series_ticker, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            payload = _get_json(session, url, params=params)
        except PredictionAPIException as exc:
            _s(f"Kalshi API error ({series_ticker}): {exc}")
            break
        markets = payload.get("markets", []) if isinstance(payload, dict) else []
        all_markets.extend(markets)
        cursor = payload.get("cursor")
        if not cursor or len(markets) < 200:
            break
    return all_markets


def _kalshi_cbb_games(session: Any, status_callback=None) -> List[Dict]:
    """
    Return list of Kalshi CBB game structures, one per event (game).
    Each game has: event_ticker, title, game_date, kalshi_link,
    teams: {team_name: {ticker, yes_ask, no_ask}}
    """
    markets = _fetch_kalshi_series(session, KALSHI_GAME_SERIES, status_callback)

    events: Dict[str, List[Dict]] = {}
    for m in markets:
        et = m.get("event_ticker", "")
        events.setdefault(et, []).append(m)

    games = []
    for event_ticker, event_markets in events.items():
        teams: Dict[str, Dict] = {}
        for m in event_markets:
            # yes_sub_title holds the team name for the YES side
            team = m.get("yes_sub_title") or ""
            if not team:
                continue
            teams[team] = {
                "ticker":   m.get("ticker", ""),
                "yes_ask":  float(m.get("yes_ask_dollars") or 0),
                "no_ask":   float(m.get("no_ask_dollars") or 0),
            }

        if len(teams) != 2:
            continue

        # Game date from expected_expiration_time (earliest market)
        exp_times = [m.get("expected_expiration_time", "") for m in event_markets if m.get("expected_expiration_time")]
        game_date = sorted(exp_times)[0][:10] if exp_times else ""

        title = event_markets[0].get("title", "")
        series = event_markets[0].get("series_ticker", KALSHI_GAME_SERIES)
        games.append({
            "event_ticker": event_ticker,
            "title":        title,
            "game_date":    game_date,
            "teams":        teams,
            "team_names":   list(teams.keys()),
            "kalshi_link":  f"https://kalshi.com/markets/{series}/{event_ticker}",
        })

    return games


def _kalshi_cbb_totals(session: Any, status_callback=None) -> Dict[str, Dict]:
    """
    Return dict keyed by total-event-ticker, each value:
    {title, lines: {float_line: {ticker, over_yes_ask, under_no_ask}}}
    YES = Over line, NO = Under line.
    """
    markets = _fetch_kalshi_series(session, KALSHI_TOTAL_SERIES, status_callback)

    events: Dict[str, List[Dict]] = {}
    for m in markets:
        et = m.get("event_ticker", "")
        events.setdefault(et, []).append(m)

    totals_by_event: Dict[str, Dict] = {}
    for event_ticker, event_markets in events.items():
        lines: Dict[float, Dict] = {}
        for m in event_markets:
            yes_sub = m.get("yes_sub_title") or ""
            match = re.search(r"Over ([\d.]+)", yes_sub)
            if not match:
                continue
            line = float(match.group(1))
            lines[line] = {
                "ticker":        m.get("ticker", ""),
                "over_yes_ask":  float(m.get("yes_ask_dollars") or 0),  # YES = Over
                "under_no_ask":  float(m.get("no_ask_dollars") or 0),   # NO  = Under
            }
        if lines:
            totals_by_event[event_ticker] = {
                "title": event_markets[0].get("title", ""),
                "lines": lines,
            }

    return totals_by_event


# ── Polymarket fetching ───────────────────────────────────────────────────────

# Maps lowercase Kalshi team name fragments → candidate Polymarket slug abbreviations
# (most-likely first)
_TEAM_POLY_ABBREVS: Dict[str, List[str]] = {
    "alabama":          ["ala"],
    "arizona state":    ["asu", "az-st"],
    "arizona":          ["ariz", "az"],
    "arkansas":         ["ark"],
    "auburn":           ["aub"],
    "baylor":           ["bay", "baylor"],
    "boise state":      ["bsu", "boise-st"],
    "butler":           ["but", "butler"],
    "cincinnati":       ["cin"],
    "clemson":          ["clem"],
    "colorado state":   ["csu", "colo-st"],
    "colorado":         ["colo", "col"],
    "connecticut":      ["uconn", "conn"],
    "uconn":            ["uconn", "conn"],
    "creighton":        ["crei"],
    "dayton":           ["day"],
    "duke":             ["duke"],
    "florida state":    ["fsu"],
    "florida":          ["fla"],
    "gonzaga":          ["gonz"],
    "houston":          ["hou"],
    "illinois":         ["ill"],
    "indiana":          ["ind"],
    "iowa state":       ["isu"],
    "iowa":             ["iowa"],
    "kansas state":     ["ksu", "k-st"],
    "kansas":           ["kan", "ku"],
    "kentucky":         ["ky", "uk"],
    "louisville":       ["lou"],
    "lsu":              ["lsu"],
    "marquette":        ["marq"],
    "maryland":         ["md"],
    "memphis":          ["mem"],
    "miami":            ["mia"],
    "michigan state":   ["msu"],
    "michigan":         ["mich"],
    "mississippi state": ["msst"],
    "mississippi":      ["miss"],
    "missouri":         ["miz"],
    "nebraska":         ["neb"],
    "north carolina":   ["unc", "nc"],
    "notre dame":       ["nd"],
    "ohio state":       ["osu"],
    "oklahoma state":   ["okst", "okla-st"],
    "oklahoma":         ["okla", "ok"],
    "oregon":           ["ore"],
    "purdue":           ["pur"],
    "rutgers":          ["rut"],
    "seton hall":       ["sh", "seton"],
    "st. john's":       ["sju"],
    "stanford":         ["stan"],
    "syracuse":         ["syr"],
    "tennessee":        ["tenn"],
    "texas a&m":        ["tamu", "tam"],
    "texas tech":       ["ttu"],
    "texas":            ["tx"],
    "ucla":             ["ucla"],
    "usc":              ["usc"],
    "utah":             ["utah"],
    "vanderbilt":       ["vand"],
    "villanova":        ["vil", "nova"],
    "virginia tech":    ["vt"],
    "virginia":         ["uva", "va"],
    "wake forest":      ["wfu", "wf"],
    "washington":       ["wash"],
    "west virginia":    ["wvu"],
    "wisconsin":        ["wisc"],
    "xavier":           ["xav"],
}


def _name_to_poly_abbrevs(team_name: str) -> List[str]:
    """Return candidate Polymarket slug abbreviations for a team name (longest match first)."""
    lower = team_name.lower().strip()
    # Exact lookup first
    if lower in _TEAM_POLY_ABBREVS:
        return list(_TEAM_POLY_ABBREVS[lower])
    # Substring lookup (e.g. "Michigan St." → "michigan state")
    for key, abbrevs in sorted(_TEAM_POLY_ABBREVS.items(), key=lambda kv: -len(kv[0])):
        if key in lower or lower.startswith(key[:4]):
            return list(abbrevs)
    # Fallback: first 4 chars lowercased
    return [re.sub(r"[^a-z0-9]", "", lower)[:4]]


def _parse_total_line_from_slug(slug: str) -> Optional[float]:
    """Extract total line from a slug like 'cbb-tx-pur-2026-03-26-total-148pt5' → 148.5"""
    m = re.search(r"-total-(\d+)pt(\d*)", slug)
    if not m:
        return None
    integer_part = m.group(1)
    decimal_part = m.group(2)
    return float(f"{integer_part}.{decimal_part}") if decimal_part else float(integer_part)


def _parse_game_date_from_slug(slug: str) -> str:
    """Extract YYYY-MM-DD from 'cbb-tx-pur-2026-03-26' or similar."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", slug)
    return m.group(1) if m else ""


def _poly_event_to_game(event: Dict) -> Optional[Dict]:
    """Convert a Polymarket event dict (with markets) to our internal structure."""
    slug = event.get("slug", "")
    if not slug.startswith("cbb-"):
        return None

    markets = event.get("markets", [])
    if not markets:
        return None

    moneyline: Optional[Dict] = None
    totals: List[Dict] = []

    for m in markets:
        m_slug  = m.get("slug", "")
        outcomes = _parse_listish(m.get("outcomes"))
        prices   = [float(p) for p in _parse_listish(m.get("outcomePrices"))]
        clob_ids = _parse_listish(m.get("clobTokenIds"))

        if not outcomes or not prices or len(outcomes) != len(prices):
            continue

        if "total" in m_slug and "spread" not in m_slug:
            line = _parse_total_line_from_slug(m_slug)
            if line is None:
                continue
            over_idx  = next((i for i, o in enumerate(outcomes) if str(o).lower() == "over"),  None)
            under_idx = next((i for i, o in enumerate(outcomes) if str(o).lower() == "under"), None)
            if over_idx is None or under_idx is None:
                continue
            totals.append({
                "slug":           m_slug,
                "line":           line,
                "over_mid":       prices[over_idx],
                "under_mid":      prices[under_idx],
                "over_token_id":  clob_ids[over_idx]  if len(clob_ids) > over_idx  else None,
                "under_token_id": clob_ids[under_idx] if len(clob_ids) > under_idx else None,
            })

        elif "total" not in m_slug and "spread" not in m_slug:
            # Moneyline: outcomes are team names
            teams: Dict[str, Dict] = {}
            for i, outcome in enumerate(outcomes):
                teams[str(outcome)] = {
                    "mid":      prices[i],
                    "token_id": clob_ids[i] if len(clob_ids) > i else None,
                }
            moneyline = {"slug": m_slug, "teams": teams}

    if not moneyline:
        return None

    game_date = _parse_game_date_from_slug(slug)
    return {
        "slug":      slug,
        "title":     event.get("title", ""),
        "game_date": game_date,
        "teams":     moneyline["teams"],   # {full_team_name: {mid, token_id}}
        "totals":    totals,
        "poly_link": f"https://polymarket.com/sports/cbb/{slug}",
    }


def _fetch_poly_event_by_slug(session: Any, slug: str) -> Optional[Dict]:
    """Fetch a single Polymarket event by slug; return None on 404 or error."""
    url = f"{POLYMARKET_GAMMA_URL.rstrip('/')}/events/slug/{slug}"
    try:
        event = _get_json(session, url)
        if isinstance(event, dict) and event.get("slug"):
            return event
    except PredictionAPIException:
        pass
    return None


def _fetch_poly_cbb_games(session: Any, kalshi_games: List[Dict], status_callback=None) -> List[Dict]:
    """
    For each Kalshi game, try to find the matching Polymarket event by constructing
    candidate slugs from team names + date.  Both home/away orderings are tried.
    """
    def _s(msg: str) -> None:
        if status_callback:
            status_callback(msg)

    games: List[Dict] = []
    seen_slugs: set = set()

    for kg in kalshi_games:
        team_names = kg.get("team_names", [])
        game_date  = kg.get("game_date", "")
        if len(team_names) < 2 or not game_date:
            continue

        abbrevs_0 = _name_to_poly_abbrevs(team_names[0])
        abbrevs_1 = _name_to_poly_abbrevs(team_names[1])

        # Also try adjacent dates in case of timezone shift
        try:
            from datetime import date as _date, timedelta as _td
            base = _date.fromisoformat(game_date)
            date_candidates = [
                game_date,
                (base - _td(days=1)).isoformat(),
                (base + _td(days=1)).isoformat(),
            ]
        except ValueError:
            date_candidates = [game_date]

        found = None
        for d in date_candidates:
            if found:
                break
            for a0 in abbrevs_0:
                if found:
                    break
                for a1 in abbrevs_1:
                    # Try both orderings (Kalshi doesn't distinguish home/away)
                    for slug in (f"cbb-{a0}-{a1}-{d}", f"cbb-{a1}-{a0}-{d}"):
                        if slug in seen_slugs:
                            continue
                        event = _fetch_poly_event_by_slug(session, slug)
                        if event:
                            game = _poly_event_to_game(event)
                            if game and game["slug"] not in seen_slugs:
                                seen_slugs.add(game["slug"])
                                games.append(game)
                                found = game
                                break
                    if found:
                        break

        if not found:
            _s(f"No Polymarket match: {' vs '.join(team_names)} ({game_date})")

    return games


# ── Matching ──────────────────────────────────────────────────────────────────

def _match_team(k_team: str, poly_teams: List[str]) -> Optional[str]:
    """
    Find which Polymarket team name best matches a Kalshi team name.
    Kalshi uses short names ("Texas"), Polymarket uses full names ("Texas Longhorns").
    """
    k_lower = k_team.lower()
    for p_team in poly_teams:
        p_lower = p_team.lower()
        if k_lower in p_lower or p_lower.startswith(k_lower):
            return p_team
    return None


def _match_games(kalshi_games: List[Dict], poly_games: List[Dict]) -> List[Dict]:
    """Return list of {kalshi, poly, team_map} for matched game pairs."""
    matches = []
    for kg in kalshi_games:
        for pg in poly_games:
            if not _dates_match(kg["game_date"], pg["game_date"]):
                continue

            poly_team_names = list(pg["teams"].keys())
            team_map: Dict[str, str] = {}
            for k_team in kg["team_names"]:
                p_team = _match_team(k_team, poly_team_names)
                if p_team:
                    team_map[k_team] = p_team

            if len(team_map) == len(kg["team_names"]):
                matches.append({"kalshi": kg, "poly": pg, "team_map": team_map})
                break

    return matches


# ── Main generator ────────────────────────────────────────────────────────────

def get_cbb_opportunities(
    timeout: float = 15.0,
    min_edge: float = 0.0,
    arb_threshold: float = DEFAULT_ARB_THRESHOLD,
    include_totals: bool = True,
    status_callback=None,
) -> Generator[Dict[str, Any], None, None]:
    """
    Yield cross-exchange arb opportunities for CBB games (Kalshi vs Polymarket).

    Checks:
      - Moneyline (game winner): Kalshi YES teamA + Polymarket teamB < threshold
      - Totals (O/U): matched lines only — Kalshi YES(Over) + Poly Under < threshold
                                           Kalshi NO(Under) + Poly Over < threshold

    Yields one dict per opportunity (or no-arb snapshot when threshold is raised).
    """
    def _status(msg: str) -> None:
        if status_callback:
            status_callback(msg)

    session = _session_with_retries(timeout=timeout)

    _status("Fetching Kalshi CBB game markets…")
    kalshi_games = _kalshi_cbb_games(session, status_callback=_status)
    _status(f"Kalshi: {len(kalshi_games)} games")

    if include_totals:
        _status("Fetching Kalshi CBB totals markets…")
        kalshi_totals = _kalshi_cbb_totals(session, status_callback=_status)
        _status(f"Kalshi: {len(kalshi_totals)} games with totals lines")
    else:
        kalshi_totals = {}

    _status("Fetching Polymarket CBB events…")
    poly_games = _fetch_poly_cbb_games(session, kalshi_games, status_callback=_status)
    _status(f"Polymarket: {len(poly_games)} games")

    _status("Matching games across exchanges…")
    matches = _match_games(kalshi_games, poly_games)
    _status(f"Matched {len(matches)} game pair(s) — checking prices…")

    for match in matches:
        kg       = match["kalshi"]
        pg       = match["poly"]
        team_map = match["team_map"]   # kalshi_name → polymarket_name
        team_names = list(team_map.keys())

        # ── Moneyline ────────────────────────────────────────────────────────
        # Fetch accurate Polymarket ask prices from CLOB
        poly_asks: Dict[str, float] = {}
        for k_team, p_team in team_map.items():
            token_id = pg["teams"][p_team].get("token_id")
            ask = _clob_best_ask(session, token_id)
            poly_asks[k_team] = ask if ask is not None else pg["teams"][p_team].get("mid", 0.0)

        # Check both directions: teamA wins / teamB wins
        for k_team_a in team_names:
            k_team_b  = next(t for t in team_names if t != k_team_a)
            kalshi_ask = kg["teams"][k_team_a]["yes_ask"]
            poly_ask   = poly_asks.get(k_team_b, 0.0)

            if kalshi_ask <= 0 or poly_ask <= 0:
                continue

            total_cost = kalshi_ask + poly_ask
            edge       = 1.0 - total_cost

            if total_cost < arb_threshold and edge >= min_edge:
                yield {
                    "strategy":           "cbb_moneyline",
                    "source":             "kalshi+polymarket",
                    "match_name":         f"{kg['title']} — {k_team_a} wins",
                    "game_date":          kg["game_date"],
                    "direction":          f"{k_team_a} wins",
                    "kalshi_leg":         f"YES ({k_team_a} wins)",
                    "poly_leg":           f"{k_team_b} wins",
                    "kalshi_price":       kalshi_ask,
                    "poly_price":         poly_ask,
                    "sum":                total_cost,
                    "edge":               edge,
                    "profit_per_contract": edge,
                    "kalshi_ticker":      kg["teams"][k_team_a]["ticker"],
                    "poly_slug":          pg["slug"],
                    "kalshi_link":        kg["kalshi_link"],
                    "poly_link":          pg["poly_link"],
                    "no_arb":             edge < 0,
                }

        # ── Totals ────────────────────────────────────────────────────────────
        if not include_totals:
            continue

        # Kalshi total event ticker: replace game series prefix
        total_event_ticker = kg["event_ticker"].replace(KALSHI_GAME_SERIES, KALSHI_TOTAL_SERIES)
        k_total_data = kalshi_totals.get(total_event_ticker)
        if not k_total_data:
            continue

        for p_total in pg["totals"]:
            p_line = p_total["line"]

            # Only match exact same line
            k_line = k_total_data["lines"].get(p_line)
            if not k_line:
                continue

            # Fetch accurate Polymarket CLOB asks for this total
            over_ask  = _clob_best_ask(session, p_total.get("over_token_id"))
            under_ask = _clob_best_ask(session, p_total.get("under_token_id"))
            if over_ask  is None: over_ask  = p_total["over_mid"]
            if under_ask is None: under_ask = p_total["under_mid"]

            # Direction 1: Kalshi YES (Over) + Polymarket Under
            sum_over  = k_line["over_yes_ask"] + under_ask
            edge_over = 1.0 - sum_over
            if sum_over < arb_threshold and edge_over >= min_edge:
                yield {
                    "strategy":           "cbb_total",
                    "source":             "kalshi+polymarket",
                    "match_name":         f"O/U {p_line} — {kg['title']}",
                    "game_date":          kg["game_date"],
                    "direction":          f"OVER {p_line}",
                    "kalshi_leg":         f"YES (Over {p_line})",
                    "poly_leg":           f"Under {p_line}",
                    "kalshi_price":       k_line["over_yes_ask"],
                    "poly_price":         under_ask,
                    "sum":                sum_over,
                    "edge":               edge_over,
                    "profit_per_contract": edge_over,
                    "kalshi_ticker":      k_line["ticker"],
                    "poly_slug":          p_total["slug"],
                    "kalshi_link":        kg["kalshi_link"],
                    "poly_link":          f"https://polymarket.com/sports/cbb/{p_total['slug']}",
                    "no_arb":             edge_over < 0,
                }

            # Direction 2: Kalshi NO (Under) + Polymarket Over
            sum_under  = k_line["under_no_ask"] + over_ask
            edge_under = 1.0 - sum_under
            if sum_under < arb_threshold and edge_under >= min_edge:
                yield {
                    "strategy":           "cbb_total",
                    "source":             "kalshi+polymarket",
                    "match_name":         f"O/U {p_line} — {kg['title']}",
                    "game_date":          kg["game_date"],
                    "direction":          f"UNDER {p_line}",
                    "kalshi_leg":         f"NO (Under {p_line})",
                    "poly_leg":           f"Over {p_line}",
                    "kalshi_price":       k_line["under_no_ask"],
                    "poly_price":         over_ask,
                    "sum":                sum_under,
                    "edge":               edge_under,
                    "profit_per_contract": edge_under,
                    "kalshi_ticker":      k_line["ticker"],
                    "poly_slug":          p_total["slug"],
                    "kalshi_link":        kg["kalshi_link"],
                    "poly_link":          f"https://polymarket.com/sports/cbb/{p_total['slug']}",
                    "no_arb":             edge_under < 0,
                }
