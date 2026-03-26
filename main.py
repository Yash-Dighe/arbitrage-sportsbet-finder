from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv
from rich import print

from backend import get_arbitrage_opportunities
from cbb_backend import get_cbb_opportunities
from prediction_backend import PredictionAPIException, get_prediction_opportunities


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="Arbitrage Finder")

    parser.add_argument(
        "--engine",
        choices=["sportsbook", "prediction", "cbb"],
        default="sportsbook",
        help="Use traditional sportsbook arbitrage, prediction-market orderbooks, or CBB cross-exchange.",
    )
    parser.add_argument("-k", "--key", default=os.environ.get("API_KEY"))
    parser.add_argument("-r", "--region", choices=["us", "eu", "uk", "au"], default="us")
    parser.add_argument("--state", help="Optional state-specific rules, e.g. il for Illinois sportsbook fees/minimums.")
    parser.add_argument("-m", "--market", default="h2h")
    parser.add_argument("-c", "--cutoff", type=float, default=0.0, help="Minimum sportsbook arbitrage percentage edge.")
    parser.add_argument("-b", "--bankroll", type=float, default=100.0)
    parser.add_argument("--include-started", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--bookmakers", type=str, help="Comma-separated list of bookmaker names to include.")
    parser.add_argument("--us-sportsbooks", action="store_true", help="Restrict to preset major US sportsbooks.")

    parser.add_argument("--prediction-source", choices=["all", "kalshi", "polymarket", "cross"], default="all")
    parser.add_argument("--prediction-strategy", choices=["pairwise", "combinatorial"], default="combinatorial")
    parser.add_argument("--prediction-limit", type=int, default=500)
    parser.add_argument("--prediction-cross-similarity", type=float, default=0.5,
                        help="Minimum Jaccard similarity to match a Kalshi/Polymarket pair (0-1).")
    parser.add_argument("--prediction-min-edge", type=float, default=0.0)
    parser.add_argument("--prediction-min-profit-per-contract", type=float, default=0.01)
    parser.add_argument("--prediction-levels-per-contract", type=int, default=5)
    parser.add_argument(
        "--prediction-assume-exhaustive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Assume grouped winner markets are exhaustive.",
    )
    parser.add_argument(
        "--prediction-strict-bundle-completeness",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require explicit Other/None-style outcome for combinatorial bundles.",
    )
    parser.add_argument(
        "--prediction-ip-integer",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use integer position sizes in Gurobi model.",
    )
    parser.add_argument("--prediction-fee-bps", type=float, default=5.0, help="Fee haircut (bps) in guarantees.")
    parser.add_argument("--prediction-slippage-bps", type=float, default=10.0, help="Slippage haircut (bps) in guarantees.")
    parser.add_argument("--prediction-debug", action="store_true")
    parser.add_argument("--prediction-debug-sample", type=int, default=0)

    parser.add_argument("--cbb-min-edge", type=float, default=0.0,
                        help="Minimum edge for CBB arb (default 0 = show all).")
    parser.add_argument("--cbb-threshold", type=float, default=1.0,
                        help="Show CBB pairs where sum < threshold (default 1.0 = strict arb only).")
    parser.add_argument("--cbb-include-totals", action=argparse.BooleanOptionalAction, default=True,
                        help="Include over/under totals arb (default true).")

    parser.add_argument(
        "--prediction-kalshi-prefilter",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--prediction-kalshi-min-liquidity", type=float, default=1.0)

    parser.add_argument("--unformatted", action="store_true")
    parser.add_argument("--pretty", action="store_true")

    return parser.parse_args()


def _resolve_bookmakers(args: argparse.Namespace) -> list[str] | None:
    if args.bookmakers:
        return [b.strip() for b in args.bookmakers.split(",") if b.strip()]
    if args.us_sportsbooks:
        return [
            "DraftKings",
            "FanDuel",
            "BetMGM",
            "BetRivers",
            "BetUS",
            "BetOnline.ag",
            "MyBookie.ag",
            "Bovada",
            "Bally Bet",
            "BetAnything",
            "Fliff",
            "Hard Rock Bet",
            "Hard Rock Bet (AZ)",
            "betPARX",
            "theScore Bet",
        ]
    return None


def _format_sportsbook(arbs: list[dict]) -> None:
    print(f"{len(arbs)} arbitrage opportunities found")
    for arb in arbs:
        print(f"\n[italic]{arb['match_name']}[/italic]  [dim]({arb['league']})[/dim]")
        print(f"  Market: [bold]{arb['market_key']}[/bold] | Starts in: [bold]{arb['hours_to_start']:.2f}h[/bold]")
        print(
            f"  Total implied odds: [bold]{arb['total_implied_odds']:.6f}[/bold] | "
            f"Profit: [bold green]{arb['profit_pct']:.3f}%[/bold green] "
            f"([bold green]{arb['profit_abs']:.2f}[/bold green] on bankroll {arb['bankroll']:.2f})"
        )
        if arb.get("state"):
            print(
                f"  After {str(arb['state']).upper()} adjustments: "
                f"[bold green]{arb.get('adjusted_profit_pct', 0.0):.3f}%[/bold green] "
                f"([bold green]{arb.get('adjusted_profit_abs', 0.0):.2f}[/bold green]) | "
                f"Fees: [bold]{arb.get('total_fees', 0.0):.2f}[/bold]"
            )
        legs = arb.get("legs", {})
        if isinstance(legs, dict) and legs:
            print("  Legs:")
            for outcome, leg in legs.items():
                book = leg.get("bookmaker", "?")
                odds = leg.get("odds", 0.0)
                stake = leg.get("stake", 0.0)
                link = leg.get("link")
                print(f"    [cyan]{outcome}[/cyan] @ [bold]{odds}[/bold] on [bold]{book}[/bold] (stake: ${stake:.2f})")
                if link:
                    print(f"      {link}")


def _format_prediction(arbs: list[dict]) -> None:
    print(f"{len(arbs)} prediction-market opportunities found")

    for arb in arbs:
        strategy = arb.get("strategy", "unknown")
        print(f"\n[italic]{arb.get('match_name', '?')}[/italic]  [dim]({arb.get('source', '?')})[/dim]")
        print(f"  Strategy: [bold]{strategy}[/bold] | Market ID: [bold]{arb.get('market_id', '?')}[/bold]")

        if strategy in ("combinatorial_ip", "combinatorial_ip_cross"):
            guaranteed = arb.get("max_profit", 0.0)
            total_cost = arb.get("total_cost", 0.0)
            floor_payout = arb.get("worst_case_payout", 0.0)
            floor_profit = arb.get("state_floor_profit", guaranteed)
            print(
                f"  Guaranteed profit: [bold green]{guaranteed:.4f}[/bold green] | "
                f"Profit/contract: [bold green]{arb.get('profit_per_contract', 0.0):.6f}[/bold green] | "
                f"Total contracts: [bold]{arb.get('max_contracts', 0.0):.2f}[/bold]"
            )
            print(
                f"  Total cost now: [bold]{total_cost:.4f}[/bold] | "
                f"Worst-case payout: [bold]{floor_payout:.4f}[/bold] | "
                f"Floor profit check: [bold green]{floor_profit:.4f}[/bold green] | "
                f"ROI: [bold green]{arb.get('roi', 0.0) * 100.0:.3f}%[/bold green]"
            )

            assumptions = arb.get("assumptions", [])
            if isinstance(assumptions, list) and assumptions:
                print("  Assumptions:")
                for item in assumptions:
                    print(f"    - {item}")

            legs = arb.get("legs", [])
            if isinstance(legs, list) and legs:
                print("  Legs:")
                for leg in legs:
                    side = leg.get("side", "YES")
                    print(
                        f"    - [{side}] {leg.get('label', leg.get('contract_id', '?'))}: "
                        f"qty={leg.get('qty', 0.0):.2f}, avg_price={leg.get('avg_price', 0.0):.4f}"
                    )
            continue

        if strategy == "cross_exchange":
            direction = arb.get("direction", "?")
            similarity = arb.get("similarity", 0.0)
            edge_pct = arb.get("edge", 0.0) * 100.0
            contracts = arb.get("max_contracts", 0.0)
            is_sell = arb.get("side") == "sell"
            if is_sell:
                p_yes = arb.get("vwap_yes_bid", arb.get("best_bid_yes", 0.0))
                p_no  = arb.get("vwap_no_bid",  arb.get("best_bid_no",  0.0))
            else:
                p_yes = arb.get("vwap_yes", arb.get("best_ask_yes", 0.0))
                p_no  = arb.get("vwap_no",  arb.get("best_ask_no",  0.0))
            side_label = "[sell]" if is_sell else "[buy]"
            warning = "  [bold yellow]WARNING: large edge likely means a bad match — verify manually[/bold yellow]" if edge_pct > 5.0 else ""
            print(
                f"  {side_label} Direction: [bold]{direction}[/bold] | "
                f"Match similarity: [dim]{similarity:.2f}[/dim]"
            )
            print(
                f"  Edge: [bold green]{edge_pct:.3f}%[/bold green] | "
                f"Contracts: [bold]{contracts:.2f}[/bold] | "
                f"Profit/contract: [bold green]{arb.get('profit_per_contract', 0.0):.4f}[/bold green]"
            )
            if is_sell:
                print(f"  Sell on Kalshi   (YES): {contracts:.2f} contracts @ ${p_yes:.4f} bid")
                print(f"  Sell on Polymarket (NO):  {contracts:.2f} contracts @ ${p_no:.4f} bid")
            elif "YES@Kalshi" in direction:
                print(f"  Bet on Kalshi   (YES): ${p_yes * contracts:.2f}  @ ${p_yes:.4f}/contract")
                print(f"  Bet on Polymarket (NO):  ${p_no  * contracts:.2f}  @ ${p_no:.4f}/contract")
            elif "YES@Polymarket" in direction:
                print(f"  Bet on Polymarket (YES): ${p_yes * contracts:.2f}  @ ${p_yes:.4f}/contract")
                print(f"  Bet on Kalshi   (NO):  ${p_no  * contracts:.2f}  @ ${p_no:.4f}/contract")
            kalshi_link = arb.get("kalshi_link")
            poly_link = arb.get("poly_link")
            if kalshi_link:
                print(f"  Kalshi: {kalshi_link}")
            if poly_link:
                print(f"  Polymarket: {poly_link}")
            if warning:
                print(warning)
        else:
            side_label = "[sell]" if arb.get("side") == "sell" else "[buy]"
            print(
                f"  {side_label} Edge: [bold green]{arb.get('edge', 0.0) * 100.0:.3f}%[/bold green] | "
                f"Max contracts: [bold]{arb.get('max_contracts', 0.0):.2f}[/bold] | "
                f"Profit/contract: [bold green]{arb.get('profit_per_contract', 0.0):.6f}[/bold green]"
            )



def _format_cbb(arbs: list[dict]) -> None:
    if not arbs:
        print("No CBB arbitrage opportunities found.")
        return
    print(f"{len(arbs)} CBB cross-exchange opportunity/ies found")
    for arb in arbs:
        strategy = arb.get("strategy", "")
        label = "Moneyline" if strategy == "cbb_moneyline" else f"O/U {arb.get('direction','')}"
        edge_pct = arb.get("edge", 0.0) * 100.0
        print(f"\n  [bold]{arb.get('match_name', '?')}[/bold]  {label}")
        print(f"  Sum: [bold]{arb.get('sum', 0.0):.4f}[/bold] | Edge: [bold green]{edge_pct:.3f}%[/bold green]")
        print(f"  Kalshi [{arb.get('kalshi_leg','?')}]: ${arb.get('kalshi_price', 0.0):.4f}")
        print(f"  Polymarket [{arb.get('poly_leg','?')}]: ${arb.get('poly_price', 0.0):.4f}")
        if arb.get("kalshi_link"):
            print(f"  Kalshi: {arb['kalshi_link']}")
        if arb.get("poly_link"):
            print(f"  Polymarket: {arb['poly_link']}")


def stream_with_args(args: argparse.Namespace, status_callback=None):
    """Like run_with_args but yields (engine, arb) tuples one at a time."""
    if args.engine == "cbb":
        for arb in get_cbb_opportunities(
            timeout=args.timeout,
            min_edge=args.cbb_min_edge,
            arb_threshold=args.cbb_threshold,
            include_totals=args.cbb_include_totals,
            status_callback=status_callback,
        ):
            yield args.engine, arb
        return

    if args.engine == "sportsbook":
        if not args.key:
            raise SystemExit("Missing API key. Use --key or set API_KEY.")
        bookmakers = _resolve_bookmakers(args)
        region = "us,us2" if args.us_sportsbooks else args.region
        for arb in get_arbitrage_opportunities(
            key=args.key,
            region=region,
            market_key=args.market,
            cutoff=args.cutoff / 100.0,
            bankroll=args.bankroll,
            include_started_matches=args.include_started,
            timeout=args.timeout,
            bookmakers=bookmakers,
            state=args.state,
        ):
            yield args.engine, arb
    else:
        try:
            for arb in get_prediction_opportunities(
                source=args.prediction_source,
                strategy=args.prediction_strategy,
                status_callback=status_callback,
                min_edge=args.prediction_min_edge / 100.0,
                min_profit_per_contract=args.prediction_min_profit_per_contract,
                timeout=args.timeout,
                limit=args.prediction_limit,
                debug=args.prediction_debug,
                debug_sample=args.prediction_debug_sample,
                kalshi_prefilter=args.prediction_kalshi_prefilter,
                kalshi_min_liquidity=args.prediction_kalshi_min_liquidity,
                assume_exhaustive=args.prediction_assume_exhaustive,
                strict_bundle_completeness=args.prediction_strict_bundle_completeness,
                levels_per_contract=args.prediction_levels_per_contract,
                integer_positions=args.prediction_ip_integer,
                fee_bps=args.prediction_fee_bps,
                slippage_bps=args.prediction_slippage_bps,
                cross_similarity_threshold=args.prediction_cross_similarity,
            ):
                yield args.engine, arb
        except PredictionAPIException as exc:
            raise SystemExit(f"Prediction provider request failed: {exc}")


def run_with_args(args: argparse.Namespace) -> tuple[str, list[dict]]:
    if args.engine == "cbb":
        arbs = list(get_cbb_opportunities(
            timeout=args.timeout,
            min_edge=args.cbb_min_edge,
            arb_threshold=args.cbb_threshold,
            include_totals=args.cbb_include_totals,
        ))
        return args.engine, arbs

    if args.engine == "sportsbook":
        if not args.key:
            raise SystemExit("Missing API key. Use --key or set API_KEY.")

        cutoff = args.cutoff / 100.0
        bookmakers = _resolve_bookmakers(args)
        region = "us,us2" if args.us_sportsbooks else args.region
        arbs = list(
            get_arbitrage_opportunities(
                key=args.key,
                region=region,
                market_key=args.market,
                cutoff=cutoff,
                bankroll=args.bankroll,
                include_started_matches=args.include_started,
                timeout=args.timeout,
                bookmakers=bookmakers,
                state=args.state,
            )
        )
    else:
        try:
            arbs = list(
                get_prediction_opportunities(
                    source=args.prediction_source,
                    strategy=args.prediction_strategy,
                    min_edge=args.prediction_min_edge / 100.0,
                    min_profit_per_contract=args.prediction_min_profit_per_contract,
                    timeout=args.timeout,
                    limit=args.prediction_limit,
                    debug=args.prediction_debug,
                    debug_sample=args.prediction_debug_sample,
                    kalshi_prefilter=args.prediction_kalshi_prefilter,
                    kalshi_min_liquidity=args.prediction_kalshi_min_liquidity,
                    assume_exhaustive=args.prediction_assume_exhaustive,
                    strict_bundle_completeness=args.prediction_strict_bundle_completeness,
                    levels_per_contract=args.prediction_levels_per_contract,
                    integer_positions=args.prediction_ip_integer,
                    fee_bps=args.prediction_fee_bps,
                    slippage_bps=args.prediction_slippage_bps,
                    cross_similarity_threshold=args.prediction_cross_similarity,
                )
            )
        except PredictionAPIException as exc:
            raise SystemExit(f"Prediction provider request failed: {exc}")

    return args.engine, arbs


def main() -> None:
    load_dotenv()
    args = parse_args()
    engine, arbs = run_with_args(args)

    if args.unformatted or args.pretty:
        if args.pretty:
            print(json.dumps(arbs, indent=2, sort_keys=True))
        else:
            print(arbs)
        return

    if engine == "sportsbook":
        _format_sportsbook(arbs)
    elif engine == "cbb":
        _format_cbb(arbs)
    else:
        _format_prediction(arbs)


if __name__ == "__main__":
    main()
