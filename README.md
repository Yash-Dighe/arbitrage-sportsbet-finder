# Arbitrage Finder
A simple tool to find sports betting arbitrage opportunities.

The tool fetches the odds from [The Odds API](https://the-odds-api.com/) and compares the odds at different bookmakers to each other in order to determine whether there are profitable and risk-free bets available.

## Installation
Clone:

    git clone https://github.com/Yash-Dighe/arbitrage-sportsbet-finder.git

Dependencies:

    pip install -r requirements.txt

The Odds API Key:

    python main.py --key <YOUR_API_KEY>

### API key
Set your API key with `-k` or `--key` arguments.

### Region
Use `-r` or `--region` arguments to set the region you want to search in. Acceptable values: `"eu"`,`"us"`, `"uk"`, and `"au"`. 

### Unformatted
Remove pretty printing with the `-u` or `--unformatted` flags.

### Cutoff
The `-c` or `--cutoff` sets a minimum profit margin while searching for arbitrage opportunities. 

### Help
The `-h` or `--help` flags will help you!

## Basic UI
If you'd rather not type CLI flags, launch the desktop UI:

    python ui.py

The UI lets you switch between `sportsbook` and `prediction` engines, fill in the same settings as the CLI, and view the returned opportunities in a scrollable results panel.

### Prediction Market Engine (Orderbook-based)
Use `prediction_math.py` with Kalshi/Polymarket orderbooks:

    python main.py --engine prediction --prediction-source all

Provider selection:

    python main.py --engine prediction --prediction-source kalshi
    python main.py --engine prediction --prediction-source polymarket

Thresholds:

    python main.py --engine prediction --prediction-min-edge 2.0 --prediction-min-profit-per-contract 0.01

Optional provider URLs via environment variables:

- `KALSHI_BASE_URL` (default: `https://api.elections.kalshi.com`)
- `POLYMARKET_GAMMA_URL` (default: `https://gamma-api.polymarket.com`)
- `POLYMARKET_CLOB_URL` (default: `https://clob.polymarket.com`)
