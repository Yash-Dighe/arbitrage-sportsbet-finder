from __future__ import annotations

import argparse
import json
import os
from dotenv import load_dotenv
from rich import print

from backend import get_arbitrage_opportunities


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="Arbitrage Finder")

    parser.add_argument(
        "-k", "--key",
        default=os.environ.get("API_KEY"),
    )
    parser.add_argument(
        "-r", "--region",
        choices=["eu", "us", "au", "uk"],
        default="eu",
    )
    parser.add_argument(
        "-m", "--market",
        default="h2h",
    )
    parser.add_argument(
        "-c", "--cutoff",
        type=float,
        default=0.0,
    )
    parser.add_argument(
        "-b", "--bankroll",
        type=float,
        default=100.0,
    )
    parser.add_argument(
        "--include-started",
        action="store_true",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--unformatted",
        action="store_true",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    if not args.key:
        raise SystemExit("Missing API key.")

    cutoff = args.cutoff / 100.0

    arbs = list(get_arbitrage_opportunities(
        key=args.key,
        region=args.region,
        market_key=args.market,
        cutoff=cutoff,
        bankroll=args.bankroll,
        include_started_matches=args.include_started,
        timeout=args.timeout,
    ))

    if args.unformatted or args.pretty:
        if args.pretty:
            print(json.dumps(arbs, indent=2, sort_keys=True))
        else:
            print(arbs)
        return

    print(
        f"{len(arbs)} arbitrage opportunities found "
        f"{':money-mouth_face:' if arbs else ':man_shrugging:'}"
    )

    for arb in arbs:
        print(f"\n[italic]{arb['match_name']}[/italic]  [dim]({arb['league']})[/dim]")
        print(
            f"  Market: [bold]{arb['market_key']}[/bold] | "
            f"Starts in: [bold]{arb['hours_to_start']:.2f}h[/bold]"
        )
        print(
            f"  Total implied odds: [bold]{arb['total_implied_odds']:.6f}[/bold] | "
            f"Profit: [bold green]{arb['profit_pct']:.3f}%[/bold green] "
            f"([bold green]{arb['profit_abs']:.2f}[/bold green] on bankroll {arb['bankroll']:.2f})"
        )

        print("  Best odds + suggested stakes:")
        for outcome_name, row in arb["legs"].items():
            print(
                f"    • [bold red]{outcome_name}[/bold red] @ "
                f"[green]{row['odds']}[/green] "
                f"from {row['bookmaker']}  -> stake [bold]{row['stake']:.2f}[/bold]"
            )


if __name__ == "__main__":
    main()
