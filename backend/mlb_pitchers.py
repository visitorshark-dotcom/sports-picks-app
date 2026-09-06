"""
Probable starting pitcher lookup via MLB's own public Stats API
(statsapi.mlb.com). This is free, requires no API key, and is the same
data source widely used by open-source baseball tools (baseballr,
MLB-StatsAPI, pymlb-statsapi, etc). It's undocumented/unofficial in the
sense that MLB doesn't publish formal terms for it, but it's stable and
has been relied on by the sabermetrics community for years.

Starting pitcher quality is arguably the single biggest driver of both
moneyline value and run totals in baseball - without it, an analysis is
missing its most important input. This fills that gap.
"""
import httpx
from datetime import datetime, timezone

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
STATS_URL_TMPL = "https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
HEADERS = {"User-Agent": "sports-picks-app/1.0"}

# In-memory cache for the day's schedule + pitcher stats, keyed by date
# string, so we don't re-fetch for every single game on every request.
_schedule_cache: dict[str, dict] = {}
_pitcher_stats_cache: dict[int, dict] = {}


async def _fetch_schedule_for_date(date_str: str) -> dict:
    """team_name -> {'name': pitcher full name, 'id': pitcher id} for that date's games."""
    if date_str in _schedule_cache:
        return _schedule_cache[date_str]

    result = {}
    try:
        async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
            resp = await client.get(
                SCHEDULE_URL,
                params={"sportId": 1, "date": date_str, "hydrate": "probablePitcher"},
            )
            resp.raise_for_status()
            data = resp.json()
        for date_block in data.get("dates", []):
            for game in date_block.get("games", []):
                for side in ("home", "away"):
                    team_info = game.get("teams", {}).get(side, {})
                    team_name = team_info.get("team", {}).get("name")
                    pitcher = team_info.get("probablePitcher")
                    if team_name and pitcher:
                        result[team_name] = {
                            "name": pitcher.get("fullName"),
                            "id": pitcher.get("id"),
                        }
    except (httpx.HTTPError, ValueError) as e:
        print(f"[mlb_pitchers] schedule fetch failed for {date_str}: {e}")

    _schedule_cache[date_str] = result
    return result


async def _fetch_pitcher_season_stats(pitcher_id: int, season: int) -> dict | None:
    if pitcher_id in _pitcher_stats_cache:
        return _pitcher_stats_cache[pitcher_id]

    stats = None
    try:
        async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
            resp = await client.get(
                STATS_URL_TMPL.format(pitcher_id=pitcher_id),
                params={"stats": "season", "group": "pitching", "season": season},
            )
            resp.raise_for_status()
            data = resp.json()
        splits = data.get("stats", [{}])[0].get("splits", [])
        if splits:
            s = splits[0].get("stat", {})
            stats = {
                "era": s.get("era"),
                "whip": s.get("whip"),
                "wins": s.get("wins"),
                "losses": s.get("losses"),
                "innings_pitched": s.get("inningsPitched"),
                "strikeouts": s.get("strikeOuts"),
                "walks": s.get("baseOnBalls"),
            }
    except (httpx.HTTPError, ValueError, IndexError, KeyError) as e:
        print(f"[mlb_pitchers] stats fetch failed for pitcher {pitcher_id}: {e}")

    _pitcher_stats_cache[pitcher_id] = stats
    return stats


async def get_probable_pitcher(home_team: str, away_team: str, commence_time_iso: str) -> dict:
    """
    Returns {'home': {...} | None, 'away': {...} | None}, each either None
    (no probable pitcher announced yet, or lookup failed) or a dict with
    name + season stats. Always returns a dict, never raises - this is a
    best-effort enrichment, not a hard dependency.
    """
    try:
        game_date = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except ValueError:
        return {"home": None, "away": None}

    date_str = game_date.isoformat()
    season = game_date.year
    schedule = await _fetch_schedule_for_date(date_str)

    result = {"home": None, "away": None}
    for side, team_name in (("home", home_team), ("away", away_team)):
        pitcher = schedule.get(team_name)
        if not pitcher or not pitcher.get("id"):
            continue
        stats = await _fetch_pitcher_season_stats(pitcher["id"], season)
        result[side] = {"name": pitcher["name"], "stats": stats}

    return result
