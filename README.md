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
