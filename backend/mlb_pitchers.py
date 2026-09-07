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

MATCHING NOTE: games are matched on BOTH team names plus closest game
time, across a 3-day window (yesterday/today/tomorrow relative to the
game's UTC date) - not just "team name" against a single date string.
That fixes two real bugs: (1) doubleheaders, where a team plays twice in
a day and a team-name-only lookup could grab the wrong game's pitcher,
and (2) UTC date-boundary mismatches, where a late-night US game can
fall on a different calendar date in UTC than MLB's own official
schedule date, silently pulling an entirely different matchup.
"""
import httpx
from datetime import datetime, timedelta, timezone

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
STATS_URL_TMPL = "https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
HEADERS = {"User-Agent": "sports-picks-app/1.0"}

# In-memory cache, keyed by date string, of the RAW list of games for that
# date (not reduced to a team->pitcher dict) so we can match on the full
# matchup + time rather than team name alone.
_schedule_cache: dict[str, list[dict]] = {}
_pitcher_stats_cache: dict[int, dict] = {}


async def _fetch_schedule_for_date(date_str: str) -> list[dict]:
    """Returns a list of games for that date, each with home/away team
    names, the game's own scheduled time, and probable pitchers."""
    if date_str in _schedule_cache:
        return _schedule_cache[date_str]

    games_out = []
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
                home_info = game.get("teams", {}).get("home", {})
                away_info = game.get("teams", {}).get("away", {})
                games_out.append({
                    "game_date_iso": game.get("gameDate"),  # actual scheduled start, ISO UTC
                    "home_team": home_info.get("team", {}).get("name"),
                    "away_team": away_info.get("team", {}).get("name"),
                    "home_pitcher": home_info.get("probablePitcher"),
                    "away_pitcher": away_info.get("probablePitcher"),
                })
    except (httpx.HTTPError, ValueError) as e:
        print(f"[mlb_pitchers] schedule fetch failed for {date_str}: {e}")

    _schedule_cache[date_str] = games_out
    return games_out


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
    Returns {'home': {...} | None, 'away': {...} | None}. Matches on BOTH
    team names plus closest game time, searching a 3-day window, so
    doubleheaders and UTC date-boundary issues can't silently return the
    wrong game's pitchers. Always returns a dict, never raises - this is a
    best-effort enrichment, not a hard dependency.
    """
    try:
        commence_dt = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return {"home": None, "away": None}

    center_date = commence_dt.date()
    season = center_date.year

    candidate_games = []
    for offset in (-1, 0, 1):
        date_str = (center_date + timedelta(days=offset)).isoformat()
        candidate_games.extend(await _fetch_schedule_for_date(date_str))

    # Match on BOTH team names, then pick whichever candidate's actual
    # scheduled time is closest to our commence_time (handles doubleheaders).
    matches = [
        g for g in candidate_games
        if g["home_team"] == home_team and g["away_team"] == away_team
    ]

    if not matches:
        return {"home": None, "away": None}

    def time_diff(g):
        try:
            g_dt = datetime.fromisoformat(g["game_date_iso"].replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            return float("inf")
        return abs((g_dt - commence_dt).total_seconds())

    best_match = min(matches, key=time_diff)

    # Sanity check: if even the closest match is more than 6 hours off,
    # something's wrong (e.g. a postponed/rescheduled game) - don't return
    # pitchers we're not confident actually belong to this game.
    if time_diff(best_match) > 6 * 3600:
        return {"home": None, "away": None}

    result = {"home": None, "away": None}
    for side in ("home", "away"):
        pitcher = best_match.get(f"{side}_pitcher")
        if not pitcher or not pitcher.get("id"):
            continue
        stats = await _fetch_pitcher_season_stats(pitcher["id"], season)
        result[side] = {"name": pitcher.get("fullName"), "stats": stats}

    return result
