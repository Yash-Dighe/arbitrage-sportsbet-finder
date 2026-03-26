from __future__ import annotations

from typing import Optional


ILLINOIS_RULES: dict[str, dict[str, float | str]] = {
    "fanduel": {"fee": 0.50, "label": "FanDuel"},
    "draftkings": {"fee": 0.50, "label": "DraftKings"},
    "caesars": {"fee": 0.25, "label": "Caesars"},
    "fanatics": {"fee": 0.25, "label": "Fanatics Sportsbook"},
    "bet365": {"fee": 0.25, "fee_below_stake": 10.0, "label": "bet365"},
    "betmgm": {"min_bet": 2.50, "label": "BetMGM"},
    "betrivers": {"min_bet": 1.00, "label": "BetRivers"},
    "espnbet": {"min_bet": 1.00, "label": "ESPN BET"},
    "hardrockbet": {"min_bet": 2.00, "label": "Hard Rock Bet"},
    "circasports": {"min_bet": 10.00, "label": "Circa Sports"},
}


BOOK_ALIASES: dict[str, str] = {
    "fan duel": "fanduel",
    "fanatics sportsbook": "fanatics",
    "bet rivers": "betrivers",
    "espn bet": "espnbet",
    "hard rock bet": "hardrockbet",
    "circa sports": "circasports",
}


def _normalize_bookmaker(name: str) -> str:
    normalized = "".join(ch.lower() for ch in name if ch.isalnum() or ch.isspace()).strip()
    compact = normalized.replace(" ", "")
    if compact in ILLINOIS_RULES:
        return compact
    return BOOK_ALIASES.get(normalized, compact)


def get_book_rule(state: Optional[str], bookmaker: str) -> dict[str, float | str]:
    if not state or state.strip().lower() != "il":
        return {}
    return ILLINOIS_RULES.get(_normalize_bookmaker(bookmaker), {})
