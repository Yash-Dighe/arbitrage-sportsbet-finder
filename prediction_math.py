from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class OrderLevel:
    price: float
    volume: float


class SolverUnavailable(RuntimeError):
    pass


def vwap(levels: Iterable[OrderLevel]) -> float:
    num = 0.0
    den = 0.0
    for lvl in levels:
        if lvl.volume <= 0 or lvl.price <= 0:
            continue
        num += lvl.price * lvl.volume
        den += lvl.volume
    return num / den if den > 0 else 0.0


def _sorted_positive_levels(levels: Iterable[OrderLevel], ascending: bool) -> List[OrderLevel]:
    cleaned = [lvl for lvl in levels if lvl.volume > 0 and lvl.price > 0]
    return sorted(cleaned, key=lambda x: x.price, reverse=not ascending)


def two_outcome_executable_buy_arbitrage(
    yes_asks: Iterable[OrderLevel],
    no_asks: Iterable[OrderLevel],
    min_edge: float = 0.0,
    min_profit_per_contract: float = 0.0,
) -> Optional[Dict[str, Any]]:
    yes = _sorted_positive_levels(yes_asks, ascending=True)
    no = _sorted_positive_levels(no_asks, ascending=True)

    if not yes or not no:
        return None

    i = 0
    j = 0
    yes_remaining = yes[0].volume
    no_remaining = no[0].volume

    total_contracts = 0.0
    total_cost = 0.0
    yes_exec: List[OrderLevel] = []
    no_exec: List[OrderLevel] = []

    while i < len(yes) and j < len(no):
        cost = yes[i].price + no[j].price
        edge = 1.0 - cost
        if edge <= 0:
            break

        take = min(yes_remaining, no_remaining)
        if take <= 0:
            break

        total_contracts += take
        total_cost += take * cost
        yes_exec.append(OrderLevel(price=yes[i].price, volume=take))
        no_exec.append(OrderLevel(price=no[j].price, volume=take))

        yes_remaining -= take
        no_remaining -= take

        if yes_remaining <= 1e-12:
            i += 1
            if i < len(yes):
                yes_remaining = yes[i].volume
        if no_remaining <= 1e-12:
            j += 1
            if j < len(no):
                no_remaining = no[j].volume

    if total_contracts <= 0:
        return None

    avg_cost = total_cost / total_contracts
    profit_per_contract = 1.0 - avg_cost
    if profit_per_contract < max(min_edge, min_profit_per_contract):
        return None

    max_profit = total_contracts * profit_per_contract
    return {
        "vwap_yes": vwap(yes_exec),
        "vwap_no": vwap(no_exec),
        "edge": profit_per_contract,
        "max_contracts": total_contracts,
        "profit_per_contract": profit_per_contract,
        "max_profit": max_profit,
        "avg_cost": avg_cost,
        "best_ask_yes": yes[0].price,
        "best_ask_no": no[0].price,
        "total_cost": total_cost,
        "worst_case_payout": total_cost + max_profit,
    }


def two_outcome_sell_arbitrage(
    yes_bids: Iterable[OrderLevel],
    no_bids: Iterable[OrderLevel],
    min_edge: float = 0.0,
    min_profit_per_contract: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Sell-side: if best_bid_yes + best_bid_no > 1, selling both YES and NO is profitable.
    Walk the bid books (descending — best bids first) to find max executable volume.
    """
    yes = _sorted_positive_levels(yes_bids, ascending=False)
    no = _sorted_positive_levels(no_bids, ascending=False)
    if not yes or not no:
        return None

    i = j = 0
    yes_remaining = yes[0].volume
    no_remaining = no[0].volume
    total_contracts = 0.0
    total_received = 0.0
    yes_exec: List[OrderLevel] = []
    no_exec: List[OrderLevel] = []

    while i < len(yes) and j < len(no):
        received = yes[i].price + no[j].price
        edge = received - 1.0
        if edge <= 0:
            break
        take = min(yes_remaining, no_remaining)
        if take <= 0:
            break
        total_contracts += take
        total_received += take * received
        yes_exec.append(OrderLevel(price=yes[i].price, volume=take))
        no_exec.append(OrderLevel(price=no[j].price, volume=take))
        yes_remaining -= take
        no_remaining -= take
        if yes_remaining <= 1e-12:
            i += 1
            if i < len(yes):
                yes_remaining = yes[i].volume
        if no_remaining <= 1e-12:
            j += 1
            if j < len(no):
                no_remaining = no[j].volume

    if total_contracts <= 0:
        return None

    avg_received = total_received / total_contracts
    profit_per_contract = avg_received - 1.0
    if profit_per_contract < max(min_edge, min_profit_per_contract):
        return None

    return {
        "side": "sell",
        "vwap_yes_bid": vwap(yes_exec),
        "vwap_no_bid": vwap(no_exec),
        "edge": profit_per_contract,
        "max_contracts": total_contracts,
        "profit_per_contract": profit_per_contract,
        "max_profit": total_contracts * profit_per_contract,
        "avg_received": avg_received,
        "best_bid_yes": yes[0].price,
        "best_bid_no": no[0].price,
        "total_received": total_received,
        # at settlement you owe $1 per contract (one side always pays out)
        "worst_case_obligation": total_contracts,
    }


def solve_winner_bundle_arbitrage(
    bundle_id: str,
    contracts: List[Dict[str, Any]],
    min_profit_per_contract: float = 0.0,
    integer_positions: bool = False,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Combinatorial arbitrage for a mutually exclusive/exhaustive winner bundle.

    States: exactly one contract wins.
    Decision: buy quantities across ask levels for each contract.
    Objective: maximize guaranteed payout - post-costs across all winner states.
    """
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SolverUnavailable(
            "Gurobi (gurobipy) is required for combinatorial strategy but is unavailable."
        ) from exc

    if len(contracts) < 2:
        return None

    fee_rate = max(0.0, fee_bps) / 10000.0
    slippage_rate = max(0.0, slippage_bps) / 10000.0
    cost_multiplier = 1.0 + fee_rate + slippage_rate

    model = gp.Model(f"bundle_arb_{bundle_id}")
    model.Params.OutputFlag = 0

    vtype = GRB.INTEGER if integer_positions else GRB.CONTINUOUS

    # YES position variables: pay $1 when contract ci resolves YES (wins)
    yes_qty_vars: Dict[tuple[int, int], Any] = {}
    for ci, contract in enumerate(contracts):
        for li, lvl in enumerate(contract.get("levels", [])):
            if not isinstance(lvl, OrderLevel):
                continue
            ub = max(float(lvl.volume), 0.0)
            if ub <= 0:
                continue
            yes_qty_vars[(ci, li)] = model.addVar(lb=0.0, ub=ub, vtype=vtype, name=f"y_{ci}_{li}")

    # NO position variables: pay $1 when contract ci resolves NO (any other contract wins)
    # Enables "buy all NO" when sum(YES prices) > 1 — the $17M strategy from the paper
    no_qty_vars: Dict[tuple[int, int], Any] = {}
    for ci, contract in enumerate(contracts):
        for li, lvl in enumerate(contract.get("no_levels", [])):
            if not isinstance(lvl, OrderLevel):
                continue
            ub = max(float(lvl.volume), 0.0)
            if ub <= 0:
                continue
            no_qty_vars[(ci, li)] = model.addVar(lb=0.0, ub=ub, vtype=vtype, name=f"n_{ci}_{li}")

    # backward-compat alias used in result extraction below
    qty_vars = yes_qty_vars

    if not yes_qty_vars and not no_qty_vars:
        return None

    t = model.addVar(lb=-GRB.INFINITY, vtype=GRB.CONTINUOUS, name="t")

    def _raw_cost_expr() -> Any:
        expr = 0
        for (ci, li), var in yes_qty_vars.items():
            lvl = contracts[ci]["levels"][li]
            expr += float(lvl.price) * var
        for (ci, li), var in no_qty_vars.items():
            lvl = contracts[ci]["no_levels"][li]
            expr += float(lvl.price) * var
        return expr

    raw_cost_expr = _raw_cost_expr()
    total_cost_expr = cost_multiplier * raw_cost_expr

    for winner_ci in range(len(contracts)):
        payout = 0
        # YES tokens pay out only for the winning contract
        for (ci, li), var in yes_qty_vars.items():
            if ci == winner_ci:
                payout += var
        # NO tokens pay out for every contract EXCEPT the winner
        for (ci, li), var in no_qty_vars.items():
            if ci != winner_ci:
                payout += var
        model.addConstr(payout - total_cost_expr >= t, name=f"state_{winner_ci}")

    model.setObjective(t, GRB.MAXIMIZE)
    model.optimize()

    if model.Status != GRB.OPTIMAL:
        return None

    guaranteed_profit = float(t.X)
    if guaranteed_profit <= 1e-10:
        return None

    # qty_by_contract tracks the payout each contract delivers when it wins
    qty_by_contract: Dict[int, float] = {ci: 0.0 for ci in range(len(contracts))}
    allocations: List[Dict[str, Any]] = []
    total_qty = 0.0
    raw_cost_val = 0.0

    for ci, contract in enumerate(contracts):
        yes_qty = 0.0
        yes_cost = 0.0
        for li, lvl in enumerate(contract.get("levels", [])):
            var = yes_qty_vars.get((ci, li))
            if var is None:
                continue
            x = float(var.X)
            if x <= 1e-9:
                continue
            yes_qty += x
            yes_cost += x * float(lvl.price)

        no_qty = 0.0
        no_cost = 0.0
        for li, lvl in enumerate(contract.get("no_levels", [])):
            var = no_qty_vars.get((ci, li))
            if var is None:
                continue
            x = float(var.X)
            if x <= 1e-9:
                continue
            no_qty += x
            no_cost += x * float(lvl.price)

        if yes_qty <= 1e-9 and no_qty <= 1e-9:
            continue

        # payout when this contract wins = YES tokens held for it
        qty_by_contract[ci] = yes_qty
        total_qty += yes_qty + no_qty
        raw_cost_val += yes_cost + no_cost

        if yes_qty > 1e-9:
            allocations.append({
                "contract_id": contract.get("contract_id"),
                "label": contract.get("label"),
                "side": "YES",
                "qty": yes_qty,
                "avg_price": yes_cost / yes_qty,
                "notional_cost": yes_cost,
            })
        if no_qty > 1e-9:
            allocations.append({
                "contract_id": contract.get("contract_id"),
                "label": contract.get("label"),
                "side": "NO",
                "qty": no_qty,
                "avg_price": no_cost / no_qty,
                "notional_cost": no_cost,
            })

    if total_qty <= 1e-9:
        return None

    total_cost_val = raw_cost_val * cost_multiplier

    # For each possible winning state, compute actual payout from the full portfolio.
    # YES tokens pay only when their contract wins; NO tokens pay when their contract loses.
    no_qty_by_contract: Dict[int, float] = {}
    for (ci, _li), var in no_qty_vars.items():
        x = float(var.X)
        if x > 1e-9:
            no_qty_by_contract[ci] = no_qty_by_contract.get(ci, 0.0) + x

    total_no = sum(no_qty_by_contract.values())
    state_payouts = []
    for winner_ci in range(len(contracts)):
        yes_payout = qty_by_contract.get(winner_ci, 0.0)
        # NO tokens for the winner resolve worthless; all other NO tokens pay
        no_payout = total_no - no_qty_by_contract.get(winner_ci, 0.0)
        state_payouts.append(yes_payout + no_payout)

    worst_case_payout = min(state_payouts) if state_payouts else 0.0
    state_floor_profit = worst_case_payout - total_cost_val

    guaranteed_profit = min(guaranteed_profit, state_floor_profit)
    if guaranteed_profit <= 1e-10:
        return None

    # profit_per_contract: guaranteed profit per unit purchased (valid for YES-only bundles;
    # for mixed YES+NO portfolios the denominator counts heterogeneous units — use roi instead)
    profit_per_contract = guaranteed_profit / total_qty if total_qty > 1e-9 else 0.0
    # roi: profit per dollar invested — meaningful regardless of YES/NO mix
    roi = guaranteed_profit / total_cost_val if total_cost_val > 1e-9 else 0.0

    if profit_per_contract < min_profit_per_contract:
        return None

    return {
        "bundle_id": bundle_id,
        "edge": profit_per_contract,
        "profit_per_contract": profit_per_contract,
        "roi": roi,
        "max_profit": guaranteed_profit,
        "max_contracts": total_qty,
        "avg_cost": (total_cost_val / total_qty) if total_qty > 1e-9 else 0.0,
        "raw_cost": raw_cost_val,
        "total_cost": total_cost_val,
        "worst_case_payout": worst_case_payout,
        "state_floor_profit": state_floor_profit,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "cost_multiplier": cost_multiplier,
        "legs": allocations,
    }
