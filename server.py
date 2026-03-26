from __future__ import annotations

import argparse
import json
import os
import queue
import threading
from typing import Iterator, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from main import stream_with_args

load_dotenv()

app = FastAPI(title="Arbitrage Finder")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:4173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _make_args(
    engine: str,
    key: Optional[str],
    region: str,
    state: Optional[str],
    market: str,
    cutoff: float,
    bankroll: float,
    include_started: bool,
    timeout: float,
    bookmakers: Optional[str],
    us_sportsbooks: bool,
    prediction_source: str,
    prediction_strategy: str,
    prediction_limit: int,
    prediction_cross_similarity: float,
    prediction_min_edge: float,
    prediction_min_profit_per_contract: float,
    prediction_levels_per_contract: int,
    prediction_assume_exhaustive: bool,
    prediction_strict_bundle_completeness: bool,
    prediction_ip_integer: bool,
    prediction_fee_bps: float,
    prediction_slippage_bps: float,
    prediction_debug: bool,
    prediction_debug_sample: int,
    cbb_min_edge: float,
    cbb_threshold: float,
    cbb_include_totals: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        engine=engine,
        key=key or os.environ.get("API_KEY"),
        region=region,
        state=state or None,
        market=market,
        cutoff=cutoff,
        bankroll=bankroll,
        include_started=include_started,
        timeout=timeout,
        bookmakers=bookmakers or None,
        us_sportsbooks=us_sportsbooks,
        prediction_source=prediction_source,
        prediction_strategy=prediction_strategy,
        prediction_limit=prediction_limit,
        prediction_cross_similarity=prediction_cross_similarity,
        prediction_min_edge=prediction_min_edge,
        prediction_min_profit_per_contract=prediction_min_profit_per_contract,
        prediction_levels_per_contract=prediction_levels_per_contract,
        prediction_assume_exhaustive=prediction_assume_exhaustive,
        prediction_strict_bundle_completeness=prediction_strict_bundle_completeness,
        prediction_ip_integer=prediction_ip_integer,
        prediction_fee_bps=prediction_fee_bps,
        prediction_slippage_bps=prediction_slippage_bps,
        prediction_debug=prediction_debug,
        prediction_debug_sample=prediction_debug_sample,
        prediction_kalshi_prefilter=True,
        prediction_kalshi_min_liquidity=1.0,
        cbb_min_edge=cbb_min_edge,
        cbb_threshold=cbb_threshold,
        cbb_include_totals=cbb_include_totals,
        unformatted=False,
        pretty=False,
    )


@app.get("/api/stream")
def search_stream(
    engine: str = Query("sportsbook"),
    key: Optional[str] = Query(None),
    region: str = Query("us"),
    state: Optional[str] = Query(None),
    market: str = Query("h2h"),
    cutoff: float = Query(0.0),
    bankroll: float = Query(100.0),
    include_started: bool = Query(False),
    timeout: float = Query(30.0),
    bookmakers: Optional[str] = Query(None),
    us_sportsbooks: bool = Query(False),
    prediction_source: str = Query("all"),
    prediction_strategy: str = Query("combinatorial"),
    prediction_limit: int = Query(500),
    prediction_cross_similarity: float = Query(0.5),
    prediction_min_edge: float = Query(0.0),
    prediction_min_profit_per_contract: float = Query(0.01),
    prediction_levels_per_contract: int = Query(5),
    prediction_assume_exhaustive: bool = Query(True),
    prediction_strict_bundle_completeness: bool = Query(True),
    prediction_ip_integer: bool = Query(False),
    prediction_fee_bps: float = Query(5.0),
    prediction_slippage_bps: float = Query(10.0),
    prediction_debug: bool = Query(False),
    prediction_debug_sample: int = Query(0),
    cbb_min_edge: float = Query(0.0),
    cbb_threshold: float = Query(1.0),
    cbb_include_totals: bool = Query(True),
) -> StreamingResponse:
    args = _make_args(
        engine=engine, key=key, region=region, state=state, market=market,
        cutoff=cutoff, bankroll=bankroll, include_started=include_started,
        timeout=timeout, bookmakers=bookmakers, us_sportsbooks=us_sportsbooks,
        prediction_source=prediction_source, prediction_strategy=prediction_strategy,
        prediction_limit=prediction_limit, prediction_cross_similarity=prediction_cross_similarity,
        prediction_min_edge=prediction_min_edge,
        prediction_min_profit_per_contract=prediction_min_profit_per_contract,
        prediction_levels_per_contract=prediction_levels_per_contract,
        prediction_assume_exhaustive=prediction_assume_exhaustive,
        prediction_strict_bundle_completeness=prediction_strict_bundle_completeness,
        prediction_ip_integer=prediction_ip_integer,
        prediction_fee_bps=prediction_fee_bps, prediction_slippage_bps=prediction_slippage_bps,
        prediction_debug=prediction_debug, prediction_debug_sample=prediction_debug_sample,
        cbb_min_edge=cbb_min_edge, cbb_threshold=cbb_threshold,
        cbb_include_totals=cbb_include_totals,
    )

    def generate() -> Iterator[str]:
        q: queue.Queue = queue.Queue()
        sentinel = object()

        def run() -> None:
            try:
                def on_status(msg: str) -> None:
                    q.put({"type": "status", "message": msg})

                for eng, arb in stream_with_args(args, status_callback=on_status):
                    q.put({"type": "arb", "engine": eng, "data": arb})
            except Exception as exc:
                q.put({"type": "error", "message": str(exc)})
            finally:
                q.put(sentinel)

        threading.Thread(target=run, daemon=True).start()

        while True:
            item = q.get()
            if item is sentinel:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Serve built frontend in production
_dist = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
