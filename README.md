# Arbitrage Finder

CLI tool to scan The Odds API for **mathematical arbitrage** opportunities by comparing the best available **decimal odds** across bookmakers for a given market (default: `h2h`).

It outputs:
- best odds per outcome + which bookmaker has them
- suggested **stake sizing** for a given bankroll
- expected profit in **% and absolute $**

> Note: This detects *mathematical* arbitrage only. Real-world execution depends on stake limits, void rules, timing/line movement, etc.

---

## Requirements
- Python 3.10+ recommended
- An API key from The Odds API (you can get a free one)

---

## Installation

Clone the repo:
```bash
git clone https://github.com/Yash-Dighe/arbitrage-sportsbet-finder.git
cd arbitrage-sportsbet-finder
