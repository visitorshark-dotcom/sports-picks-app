"""
Configuration loaded entirely from environment variables.
NEVER hardcode API keys here. Copy .env.example to .env and fill in your own.
"""
import os
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# API-Sports (https://api-sports.io) - used for injury reports.
# One key works across all of their per-sport subdomains.
API_SPORTS_KEY = os.getenv("API_SPORTS_KEY", "")

# Each sport lives on its own API-Sports subdomain. Only sports with a
# confirmed injuries endpoint are wired up. NCAAB is intentionally omitted -
# API-Sports' basketball product (API-NBA) does not appear to cover college
# hoops, so we don't fake support for it.
API_SPORTS_BASES = {
    "nfl": "https://v1.american-football.api-sports.io",
    "ncaaf": "https://v1.american-football.api-sports.io",
    "nba": "https://v2.nba.api-sports.io",
    "mlb": "https://v1.baseball.api-sports.io",
    "nhl": "https://v1.hockey.api-sports.io",
}

# Regions/bookmakers for The Odds API
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "us")
ODDS_FORMAT = "american"

# Sports covered - The Odds API sport keys
SPORTS = {
    "nfl": "americanfootball_nfl",
    "ncaaf": "americanfootball_ncaaf",
    "nba": "basketball_nba",
    "ncaab": "basketball_ncaab",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
}

DB_PATH = os.getenv("DB_PATH", "picks_cache.db")

DEFAULT_MIN_CONFIDENCE = int(os.getenv("DEFAULT_MIN_CONFIDENCE", "70"))

if not ODDS_API_KEY:
    print("[WARNING] ODDS_API_KEY is not set. Set it in your .env file.")
if not ANTHROPIC_API_KEY:
    print("[WARNING] ANTHROPIC_API_KEY is not set. Set it in your .env file.")
