from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import traceback
import webbrowser
from tkinter import BooleanVar, DoubleVar, IntVar, StringVar, Tk, ttk
from tkinter.scrolledtext import ScrolledText

from dotenv import load_dotenv

from main import run_with_args, stream_with_args


URL_PATTERN = re.compile(r"https?://[^\s]+")


class ArbitrageFinderUI:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Arbitrage Sportsbet Finder")
        self.root.geometry("980x760")
        self.root.minsize(840, 640)

        self.engine = StringVar(value="sportsbook")
        self.key = StringVar(value=os.environ.get("API_KEY", ""))
        self.region = StringVar(value="us")
        self.state = StringVar(value="")
        self.market = StringVar(value="h2h")
        self.cutoff = DoubleVar(value=0.0)
        self.bankroll = DoubleVar(value=100.0)
        self.include_started = BooleanVar(value=False)
        self.timeout = DoubleVar(value=10.0)
        self.bookmakers = StringVar(value="")
        self.us_sportsbooks = BooleanVar(value=False)

        self.btc15m_min_edge = DoubleVar(value=0.0)
        self.btc15m_threshold = DoubleVar(value=1.0)

        self.prediction_source = StringVar(value="all")
        self.prediction_strategy = StringVar(value="combinatorial")
        self.prediction_limit = IntVar(value=500)
        self.prediction_cross_similarity = DoubleVar(value=0.5)
        self.prediction_min_edge = DoubleVar(value=0.0)
        self.prediction_min_profit_per_contract = DoubleVar(value=0.01)
        self.prediction_levels_per_contract = IntVar(value=5)
        self.prediction_assume_exhaustive = BooleanVar(value=True)
        self.prediction_strict_bundle_completeness = BooleanVar(value=True)
        self.prediction_ip_integer = BooleanVar(value=False)
        self.prediction_fee_bps = DoubleVar(value=5.0)
        self.prediction_slippage_bps = DoubleVar(value=10.0)
        self.prediction_debug = BooleanVar(value=False)
        self.prediction_debug_sample = IntVar(value=0)

        self.show_raw_json = BooleanVar(value=False)
        self.status = StringVar(value="Ready.")
        self._link_targets: dict[str, str] = {}

        self._build()
        self._sync_engine_sections()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)

        top = ttk.LabelFrame(frame, text="Run Settings", padding=10)
        top.grid(row=0, column=0, sticky="ew")
        for col in range(4):
            top.columnconfigure(col, weight=1)

        ttk.Label(top, text="Engine").grid(row=0, column=0, sticky="w")
        engine_box = ttk.Combobox(top, textvariable=self.engine, values=["sportsbook", "prediction", "btc15m"], state="readonly")
        engine_box.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        engine_box.bind("<<ComboboxSelected>>", lambda _event: self._sync_engine_sections())

        ttk.Label(top, text="Timeout (sec)").grid(row=0, column=1, sticky="w")
        ttk.Entry(top, textvariable=self.timeout).grid(row=1, column=1, sticky="ew", padx=(0, 8))

        ttk.Checkbutton(top, text="Show raw JSON", variable=self.show_raw_json).grid(row=1, column=2, sticky="w")

        self.run_button = ttk.Button(top, text="Run Search", command=self._run_search)
        self.run_button.grid(row=1, column=3, sticky="e")

        self.sportsbook_frame = ttk.LabelFrame(frame, text="Sportsbook Options", padding=10)
        self.sportsbook_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self._build_sportsbook_section()

        self.prediction_frame = ttk.LabelFrame(frame, text="Prediction Market Options", padding=10)
        self.prediction_frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self._build_prediction_section()

        self.btc15m_frame = ttk.LabelFrame(frame, text="BTC 15m Cross-Exchange Options", padding=10)
        self.btc15m_frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self._build_btc15m_section()

        results_frame = ttk.LabelFrame(frame, text="Results", padding=10)
        results_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

        self.results = ScrolledText(results_frame, wrap="word", font=("Consolas", 10))
        self.results.grid(row=0, column=0, sticky="nsew")

        status_bar = ttk.Label(frame, textvariable=self.status, anchor="w")
        status_bar.grid(row=4, column=0, sticky="ew", pady=(8, 0))

    def _build_sportsbook_section(self) -> None:
        frame = self.sportsbook_frame
        for col in range(4):
            frame.columnconfigure(col, weight=1)

        ttk.Label(frame, text="API Key").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.key).grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Region").grid(row=0, column=2, sticky="w")
        ttk.Combobox(frame, textvariable=self.region, values=["us", "eu", "uk", "au"], state="readonly").grid(
            row=1, column=2, sticky="ew", padx=(0, 8)
        )

        ttk.Label(frame, text="State (optional)").grid(row=0, column=3, sticky="w")
        ttk.Entry(frame, textvariable=self.state).grid(row=1, column=3, sticky="ew")

        ttk.Label(frame, text="Cutoff %").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.cutoff).grid(row=3, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Bankroll").grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.bankroll).grid(row=3, column=1, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Market").grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.market).grid(row=3, column=2, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Bookmakers (comma-separated)").grid(row=2, column=3, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.bookmakers).grid(row=3, column=3, sticky="ew")

        ttk.Checkbutton(frame, text="Include started matches", variable=self.include_started).grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(frame, text="Use preset major US sportsbooks", variable=self.us_sportsbooks).grid(
            row=4, column=1, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _build_prediction_section(self) -> None:
        frame = self.prediction_frame
        for col in range(4):
            frame.columnconfigure(col, weight=1)

        ttk.Label(frame, text="Source").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=self.prediction_source, values=["all", "kalshi", "polymarket", "cross"], state="readonly").grid(
            row=1, column=0, sticky="ew", padx=(0, 8)
        )

        ttk.Label(frame, text="Strategy").grid(row=0, column=1, sticky="w")
        ttk.Combobox(frame, textvariable=self.prediction_strategy, values=["pairwise", "combinatorial"], state="readonly").grid(
            row=1, column=1, sticky="ew", padx=(0, 8)
        )

        ttk.Label(frame, text="Market Limit").grid(row=0, column=2, sticky="w")
        ttk.Entry(frame, textvariable=self.prediction_limit).grid(row=1, column=2, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Min Edge %").grid(row=0, column=3, sticky="w")
        ttk.Entry(frame, textvariable=self.prediction_min_edge).grid(row=1, column=3, sticky="ew")

        ttk.Label(frame, text="Min Profit / Contract").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.prediction_min_profit_per_contract).grid(row=3, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Levels / Contract").grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.prediction_levels_per_contract).grid(row=3, column=1, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Fee (bps)").grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.prediction_fee_bps).grid(row=3, column=2, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Slippage (bps)").grid(row=2, column=3, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.prediction_slippage_bps).grid(row=3, column=3, sticky="ew")

        ttk.Label(frame, text="Debug Sample").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.prediction_debug_sample).grid(row=5, column=0, sticky="ew", padx=(0, 8))

        ttk.Checkbutton(frame, text="Assume exhaustive bundle", variable=self.prediction_assume_exhaustive).grid(
            row=5, column=1, sticky="w"
        )
        ttk.Checkbutton(frame, text="Require strict bundle completeness", variable=self.prediction_strict_bundle_completeness).grid(
            row=5, column=2, sticky="w"
        )
        ttk.Checkbutton(frame, text="Use integer positions", variable=self.prediction_ip_integer).grid(
            row=5, column=3, sticky="w"
        )

        ttk.Label(frame, text="Cross-exchange similarity (0-1)").grid(row=6, column=1, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.prediction_cross_similarity).grid(row=7, column=1, sticky="ew", padx=(0, 8))

        ttk.Checkbutton(frame, text="Enable debug stats", variable=self.prediction_debug).grid(
            row=7, column=0, sticky="w", pady=(8, 0)
        )

    def _build_btc15m_section(self) -> None:
        frame = self.btc15m_frame
        for col in range(4):
            frame.columnconfigure(col, weight=1)

        ttk.Label(frame, text="Min Edge (e.g. 0.01 = 1¢/contract)").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.btc15m_min_edge).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="Show pairs where sum <").grid(row=0, column=1, sticky="w")
        ttk.Entry(frame, textvariable=self.btc15m_threshold).grid(row=1, column=1, sticky="ew", padx=(0, 8))

        ttk.Label(frame, text="(1.0 = strict arb only; raise to see near-arb pricing)", foreground="gray").grid(
            row=1, column=2, columnspan=2, sticky="w"
        )

    def _sync_engine_sections(self) -> None:
        state = self.engine.get()
        self.sportsbook_frame.state(["!disabled"] if state == "sportsbook" else ["disabled"])
        self.prediction_frame.state(["!disabled"] if state == "prediction" else ["disabled"])
        self.btc15m_frame.state(["!disabled"] if state == "btc15m" else ["disabled"])

    def _append_results(self, text: str) -> None:
        self.results.delete("1.0", "end")
        self.results.insert("end", text)
        self._bind_links()
        self.results.see("1.0")

    def _bind_links(self) -> None:
        self._link_targets.clear()
        for tag in self.results.tag_names():
            if tag.startswith("link-"):
                self.results.tag_delete(tag)

        content = self.results.get("1.0", "end-1c")
        for index, match in enumerate(URL_PATTERN.finditer(content)):
            start = f"1.0+{match.start()}c"
            end = f"1.0+{match.end()}c"
            tag = f"link-{index}"
            self._link_targets[tag] = match.group(0)
            self.results.tag_add(tag, start, end)
            self.results.tag_config(tag, foreground="#0563C1", underline=True)
            self.results.tag_bind(tag, "<Button-1>", lambda _event, name=tag: self._open_link(name))
            self.results.tag_bind(tag, "<Enter>", lambda _event: self.results.config(cursor="hand2"))
            self.results.tag_bind(tag, "<Leave>", lambda _event: self.results.config(cursor=""))

    def _open_link(self, tag_name: str) -> None:
        url = self._link_targets.get(tag_name)
        if url:
            webbrowser.open(url)

    def _format_sportsbook_results(self, arbs: list[dict]) -> str:
        if not arbs:
            return "No sportsbook arbitrage opportunities found."

        lines = [f"Sportsbook opportunities: {len(arbs)}", ""]
        for index, arb in enumerate(arbs, start=1):
            lines.append(f"{index}. {arb.get('match_name', '?')}")
            lines.append(f"   League: {arb.get('league', 'unknown')}")
            lines.append(
                "   Profit: "
                f"{arb.get('profit_pct', 0.0):.3f}% "
                f"(${arb.get('profit_abs', 0.0):.2f} on ${arb.get('bankroll', 0.0):.2f})"
            )
            if arb.get("state"):
                lines.append(
                    "   After state adjustments: "
                    f"{arb.get('adjusted_profit_pct', 0.0):.3f}% "
                    f"(${arb.get('adjusted_profit_abs', 0.0):.2f}) | fees ${arb.get('total_fees', 0.0):.2f}"
                )
                if not arb.get("meets_minimums", True):
                    lines.append(
                        "   Illinois minimums: "
                        f"current bankroll too small; estimated required bankroll ${arb.get('required_bankroll', 0.0):.2f}"
                    )
            lines.append(
                "   Market: "
                f"{arb.get('market_key', '?')} | Starts in {arb.get('hours_to_start', 0.0):.2f}h | "
                f"Implied total {arb.get('total_implied_odds', 0.0):.6f}"
            )

            legs = arb.get("legs", {})
            if isinstance(legs, dict) and legs:
                lines.append("   Bet plan:")
                for outcome_name, row in legs.items():
                    if not isinstance(row, dict):
                        continue
                    line = (
                        f"     - {outcome_name}: {row.get('bookmaker', '?')} at {row.get('odds', 0.0)} "
                        f"| stake ${row.get('stake', 0.0):.2f}"
                    )
                    fee = float(row.get("fee", 0.0))
                    min_bet = float(row.get("min_bet", 0.0))
                    if fee > 0:
                        line += f" | fee ${fee:.2f}"
                    if min_bet > 0:
                        line += f" | min ${min_bet:.2f}"
                    link = row.get("link")
                    if isinstance(link, str) and link.strip():
                        line += f"\n       Open: {link.strip()}"
                    lines.append(line)
            lines.append("")

        return "\n".join(lines).rstrip()

    def _format_prediction_results(self, arbs: list[dict]) -> str:
        if not arbs:
            return "No prediction-market opportunities found."

        # Group cross-exchange results by market_id so the same pair
        # isn't listed multiple times as separate numbered entries.
        cross_groups: dict[str, list[dict]] = {}
        other_arbs: list[dict] = []
        for arb in arbs:
            if arb.get("strategy") == "cross_exchange":
                mid = arb.get("market_id", "")
                cross_groups.setdefault(mid, []).append(arb)
            else:
                other_arbs.append(arb)

        grouped: list[list[dict]] = [[a] for a in other_arbs] + list(cross_groups.values())
        lines = [f"Prediction-market opportunities: {len(grouped)}", ""]

        for index, group in enumerate(grouped, start=1):
            first = group[0]
            strategy = first.get("strategy", "unknown")

            if strategy == "cross_exchange":
                # Header: show the pair name and links once
                kalshi_q = first.get("kalshi_ticker", "")
                # Use the first arb's match_name but strip the direction suffix
                pair_name = first.get("match_name", "?")
                lines.append(f"{index}. {pair_name}")
                lines.append(
                    f"   Source: {first.get('source', '?')} | "
                    f"Similarity: {first.get('similarity', 0.0):.2f} | "
                    f"Kalshi: {kalshi_q}"
                )
                kalshi_link = first.get("kalshi_link")
                poly_link = first.get("poly_link")
                if kalshi_link:
                    lines.append(f"   Kalshi: {kalshi_link}")
                if poly_link:
                    lines.append(f"   Polymarket: {poly_link}")
                # List each direction as a sub-item
                for arb in group:
                    direction = arb.get("direction", "?")
                    edge_pct = arb.get("edge", 0.0) * 100.0
                    contracts = arb.get("max_contracts", 0.0)
                    is_sell = arb.get("side") == "sell"
                    if is_sell:
                        p_yes = arb.get("vwap_yes_bid", arb.get("best_bid_yes", 0.0))
                        p_no  = arb.get("vwap_no_bid",  arb.get("best_bid_no",  0.0))
                    else:
                        p_yes = arb.get("vwap_yes", arb.get("best_ask_yes", 0.0))
                        p_no  = arb.get("vwap_no",  arb.get("best_ask_no",  0.0))
                    warning = "   *** large edge — verify manually ***" if edge_pct > 5.0 else ""
                    lines.append(
                        f"   -> {direction}: "
                        f"{edge_pct:.2f}% edge | "
                        f"{arb.get('profit_per_contract', 0.0):.4f} profit/contract | "
                        f"{contracts:.2f} contracts"
                        f"{warning}"
                    )
                    if is_sell:
                        lines.append(f"      Sell Kalshi   (YES): {contracts:.2f} contracts @ ${p_yes:.4f} bid")
                        lines.append(f"      Sell Polymarket (NO):  {contracts:.2f} contracts @ ${p_no:.4f} bid")
                    elif "YES@Kalshi" in direction:
                        lines.append(f"      Kalshi   (YES): ${p_yes * contracts:.2f}  @ ${p_yes:.4f}/contract")
                        lines.append(f"      Polymarket (NO):  ${p_no  * contracts:.2f}  @ ${p_no:.4f}/contract")
                    elif "YES@Polymarket" in direction:
                        lines.append(f"      Polymarket (YES): ${p_yes * contracts:.2f}  @ ${p_yes:.4f}/contract")
                        lines.append(f"      Kalshi   (NO):  ${p_no  * contracts:.2f}  @ ${p_no:.4f}/contract")
            else:
                arb = first
                lines.append(f"{index}. {arb.get('match_name', '?')}")
                lines.append(
                    f"   Source: {arb.get('source', '?')} | Strategy: {strategy} | Market ID: {arb.get('market_id', '?')}"
                )
                link = arb.get("link")
                if isinstance(link, str) and link.strip():
                    lines.append(f"   Open: {link.strip()}")

                if strategy in ("combinatorial_ip", "combinatorial_ip_cross"):
                    lines.append(
                        "   Guarantee: "
                        f"${arb.get('max_profit', 0.0):.4f} profit | "
                        f"{arb.get('profit_per_contract', 0.0):.6f} per contract | "
                        f"{arb.get('max_contracts', 0.0):.2f} contracts"
                    )
                    lines.append(
                        "   Cost/Payout: "
                        f"${arb.get('total_cost', 0.0):.4f} cost now | "
                        f"${arb.get('worst_case_payout', 0.0):.4f} worst-case payout | "
                        f"${arb.get('state_floor_profit', 0.0):.4f} floor profit | "
                        f"ROI {arb.get('roi', 0.0) * 100.0:.3f}%"
                    )
                    assumptions = arb.get("assumptions", [])
                    if isinstance(assumptions, list) and assumptions:
                        lines.append("   Assumptions:")
                        for item in assumptions:
                            lines.append(f"     - {item}")
                    legs = arb.get("legs", [])
                    if isinstance(legs, list) and legs:
                        lines.append("   Positions:")
                        for leg in legs:
                            if not isinstance(leg, dict):
                                continue
                            side = leg.get("side", "YES")
                            lines.append(
                                f"     - [{side}] {leg.get('label', leg.get('contract_id', '?'))}: "
                                f"qty {leg.get('qty', 0.0):.2f} at avg {leg.get('avg_price', 0.0):.4f}"
                            )
                else:
                    side_label = "[sell]" if arb.get("side") == "sell" else "[buy]"
                    lines.append(
                        f"   Opportunity {side_label}: "
                        f"{arb.get('edge', 0.0) * 100.0:.3f}% edge | "
                        f"{arb.get('profit_per_contract', 0.0):.6f} profit/contract | "
                        f"{arb.get('max_contracts', 0.0):.2f} max contracts"
                    )

            lines.append("")

        return "\n".join(lines).rstrip()

    def _format_btc15m_results(self, arbs: list[dict]) -> str:
        if not arbs:
            return "No BTC 15m arbitrage opportunities found.\n\nBoth platform prices checked — no mispricing detected for the current 15-minute slot."

        lines = [f"BTC 15m cross-exchange opportunities: {len(arbs)}", ""]
        for index, arb in enumerate(arbs, start=1):
            direction = arb.get("direction", "?")
            edge_pct = arb.get("edge", 0.0) * 100.0
            total_sum = arb.get("sum", 0.0)
            kalshi_leg = arb.get("kalshi_leg", "?")
            poly_leg = arb.get("poly_leg", "?")
            kalshi_price = arb.get("kalshi_price", 0.0)
            poly_price = arb.get("poly_price", 0.0)

            lines.append(f"{index}. Direction: BTC goes {direction}")
            lines.append(f"   Edge: {edge_pct:.3f}% | Total cost: ${total_sum:.4f} → guaranteed $1.00 payout")
            lines.append(f"   Kalshi  [{kalshi_leg}]: ${kalshi_price:.4f}/contract")
            lines.append(f"   Kalshi  YES ask: ${arb.get('kalshi_yes_ask', 0.0):.4f}  |  NO ask: ${arb.get('kalshi_no_ask', 0.0):.4f}")
            lines.append(f"   Polymarket [{poly_leg}]: ${poly_price:.4f}/contract")
            lines.append(f"   Polymarket YES ask: ${arb.get('poly_yes_ask', 0.0):.4f}  |  NO ask: ${arb.get('poly_no_ask', 0.0):.4f}")
            kalshi_link = arb.get("kalshi_link")
            poly_link = arb.get("poly_link")
            if kalshi_link:
                lines.append(f"   Kalshi: {kalshi_link}")
            if poly_link:
                lines.append(f"   Polymarket: {poly_link}")
            lines.append("")

        return "\n".join(lines).rstrip()

    def _format_results(self, engine: str, arbs: list[dict]) -> str:
        if self.show_raw_json.get():
            return json.dumps(arbs, indent=2, sort_keys=True)
        if engine == "sportsbook":
            return self._format_sportsbook_results(arbs)
        if engine == "btc15m":
            return self._format_btc15m_results(arbs)
        return self._format_prediction_results(arbs)

    def _build_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            engine=self.engine.get(),
            key=self.key.get().strip() or None,
            region=self.region.get(),
            state=self.state.get().strip() or None,
            market=self.market.get().strip() or "h2h",
            cutoff=float(self.cutoff.get()),
            bankroll=float(self.bankroll.get()),
            include_started=bool(self.include_started.get()),
            timeout=float(self.timeout.get()),
            bookmakers=self.bookmakers.get().strip() or None,
            us_sportsbooks=bool(self.us_sportsbooks.get()),
            prediction_source=self.prediction_source.get(),
            prediction_strategy=self.prediction_strategy.get(),
            prediction_limit=int(self.prediction_limit.get()),
            prediction_min_edge=float(self.prediction_min_edge.get()),
            prediction_min_profit_per_contract=float(self.prediction_min_profit_per_contract.get()),
            prediction_levels_per_contract=int(self.prediction_levels_per_contract.get()),
            prediction_assume_exhaustive=bool(self.prediction_assume_exhaustive.get()),
            prediction_strict_bundle_completeness=bool(self.prediction_strict_bundle_completeness.get()),
            prediction_ip_integer=bool(self.prediction_ip_integer.get()),
            prediction_fee_bps=float(self.prediction_fee_bps.get()),
            prediction_slippage_bps=float(self.prediction_slippage_bps.get()),
            prediction_debug=bool(self.prediction_debug.get()),
            prediction_debug_sample=int(self.prediction_debug_sample.get()),
            prediction_kalshi_prefilter=True,
            prediction_kalshi_min_liquidity=1.0,
            prediction_cross_similarity=float(self.prediction_cross_similarity.get()),
            btc15m_min_edge=float(self.btc15m_min_edge.get()),
            btc15m_threshold=float(self.btc15m_threshold.get()),
            unformatted=False,
            pretty=False,
        )

    def _run_search(self) -> None:
        self.run_button.state(["disabled"])
        self._fetch_start = time.time()
        self._fetch_phase = "Starting..."
        self._fetching = True
        self._append_results("Fetching opportunities...")
        self._tick_status()

        def worker() -> None:
            try:
                args = self._build_args()
                arbs: list[dict] = []
                engine = args.engine

                def on_status(msg: str) -> None:
                    self._fetch_phase = msg

                for engine, arb in stream_with_args(args, status_callback=on_status):
                    arbs.append(arb)
                    self._fetch_phase = f"{len(arbs)} found"
                    body = self._format_results(engine, arbs)
                    summary = f"Engine: {engine}\nMatches found so far: {len(arbs)}\n\n"
                    self.root.after(0, lambda t=summary + body: self._update_results(t))
                summary = f"Engine: {engine}\nMatches found: {len(arbs)}\n\n"
                body = self._format_results(engine, arbs)
                self._fetching = False
                self.root.after(0, lambda: self._finish_run(summary + body))
            except Exception as exc:  # pragma: no cover - GUI path
                self._fetching = False
                details = "".join(traceback.format_exception(exc))
                self.root.after(0, lambda: self._fail_run(details))

        threading.Thread(target=worker, daemon=True).start()

    def _tick_status(self) -> None:
        if not self._fetching:
            return
        elapsed = time.time() - self._fetch_start
        self.status.set(f"Fetching... {elapsed:.0f}s elapsed — {self._fetch_phase}")
        self.root.after(500, self._tick_status)

    def _update_results(self, output: str) -> None:
        self._append_results(output)
        self.status.set("Fetching... (results updating live)")

    def _finish_run(self, output: str) -> None:
        self._append_results(output)
        self.status.set("Search complete.")
        self.run_button.state(["!disabled"])

    def _fail_run(self, details: str) -> None:
        self._append_results(details)
        self.status.set("Search failed.")
        self.run_button.state(["!disabled"])


def main() -> None:
    load_dotenv()
    root = Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = ArbitrageFinderUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
