"""
Thin wrapper around The Odds API (https://the-odds-api.com).
Free tier: 500 requests/month, US region odds, main markets.
"""
import httpx
from datetime import datetime, timezone
from config import ODDS_API_KEY, ODDS_REGIONS, ODDS_FORMAT

BASE_URL = "https://api.the-odds-api.com/v4"


async def fetch_todays_events(sport_key: str) -> list[dict]:
    """
    Fetch odds (h2h, spreads, totals) for a given sport, filtered to games
    that start today AND haven't started yet. Once a game begins, books
    typically pull or freeze markets, so past-commence-time games would
    otherwise show up with thin/missing odds data and get correctly (but
    confusingly) skipped by the AI analysis with no explanation.
    """
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": ODDS_REGIONS,
        "markets": "h2h,spreads,totals",
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": "iso",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        events = resp.json()

    now = datetime.now(timezone.utc)
    today = now.date()
    todays_events = []
    for ev in events:
        try:
            commence = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if commence.date() == today and commence > now:
            todays_events.append(ev)
    return todays_events


async def fetch_sports_list() -> list[dict]:
    """Return the full list of sports The Odds API currently supports (for debugging)."""
    url = f"{BASE_URL}/sports"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params={"apiKey": ODDS_API_KEY})
        resp.raise_for_status()
        return resp.json()


def summarize_event_odds(event: dict) -> dict:
    """
    Collapse the (often multi-bookmaker) raw event payload into a single
    consensus-ish snapshot: best-available spread/ML/total, averaged across
    books, plus each book's individual lines for reference.
    """
    home = event.get("home_team")
    away = event.get("away_team")
    bookmakers = event.get("bookmakers", [])

    spreads, totals, home_ml, away_ml = [], [], [], []
    per_book = []

    for book in bookmakers:
        book_summary = {"key": book.get("key"), "title": book.get("title")}
        for market in book.get("markets", []):
            if market["key"] == "spreads":
                for outcome in market["outcomes"]:
                    if outcome["name"] == home:
                        spreads.append(outcome.get("point"))
                        book_summary["home_spread"] = outcome.get("point")
                        book_summary["home_spread_price"] = outcome.get("price")
            elif market["key"] == "h2h":
                for outcome in market["outcomes"]:
                    if outcome["name"] == home:
                        home_ml.append(outcome.get("price"))
                        book_summary["home_ml"] = outcome.get("price")
                    elif outcome["name"] == away:
                        away_ml.append(outcome.get("price"))
                        book_summary["away_ml"] = outcome.get("price")
            elif market["key"] == "totals":
                for outcome in market["outcomes"]:
                    if outcome["name"] == "Over":
                        totals.append(outcome.get("point"))
                        book_summary["total"] = outcome.get("point")
        per_book.append(book_summary)

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "id": event.get("id"),
        "sport_key": event.get("sport_key"),
        "sport_title": event.get("sport_title"),
        "commence_time": event.get("commence_time"),
        "home_team": home,
        "away_team": away,
        "consensus_home_spread": avg(spreads),
        "consensus_total": avg(totals),
        "consensus_home_ml": avg(home_ml),
        "consensus_away_ml": avg(away_ml),
        "num_books": len(bookmakers),
        "per_book": per_book,
    }
