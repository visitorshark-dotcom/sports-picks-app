"""
Injury report lookups via API-Sports (https://api-sports.io).

Notes on reliability:
- API-Sports gives you ONE API key from your dashboard, but each sport
  (American Football, Basketball/NBA, Baseball, Hockey) is a SEPARATE
  product you must individually subscribe to (the free plan is fine, but
  you have to activate it per sport - it's not automatic just because you
  have an account). Each sport also tracks its own 100-requests/day free
  quota independently, so using up NFL's doesn't touch NHL's.
- API-Sports' public docs render via JS, so the exact response field names
  below are based on their documented conventions (team objects have an
  `id`/`name`, injuries return per-player entries) rather than a captured
  live sample. Parsing is defensive on purpose: if a field is missing or
  named differently than expected, we skip that entry instead of crashing
  the whole request. Sanity-check a live response against your dashboard
  docs the first time you run this.
- Free tier is 100 requests/day PER sport subdomain. Team-id lookups are
  cached in memory for the life of the process so we don't burn quota
  re-resolving the same team name every time /api/picks is called.
- NCAAB has no entry in API_SPORTS_BASES (see config.py) because API-Sports'
  basketball product doesn't appear to cover college hoops - we skip it
  rather than guess at an endpoint that may not exist.
"""
import httpx
from config import API_SPORTS_KEY, API_SPORTS_BASES

# Maps our internal sport keys -> The Odds API's sport_key prefix, so we can
# figure out which API-Sports subdomain to use from an odds summary.
ODDS_SPORT_KEY_TO_SHORT = {
    "americanfootball_nfl": "nfl",
    "americanfootball_ncaaf": "ncaaf",
    "basketball_nba": "nba",
    "baseball_mlb": "mlb",
    "icehockey_nhl": "nhl",
}

# In-memory cache: {(short_sport, team_name_lower): team_id}
_team_id_cache: dict[tuple[str, str], int | None] = {}


def _headers():
    return {"x-apisports-key": API_SPORTS_KEY}


async def _resolve_team_id(client: httpx.AsyncClient, base_url: str, short_sport: str, team_name: str) -> int | None:
    cache_key = (short_sport, team_name.lower())
    if cache_key in _team_id_cache:
        return _team_id_cache[cache_key]

    team_id = None
    try:
        resp = await client.get(f"{base_url}/teams", headers=_headers(), params={"search": team_name})
        resp.raise_for_status()
        data = resp.json()
        results = data.get("response", [])
        if results:
            # response shape varies slightly by sport; try common shapes
            first = results[0]
            team_obj = first.get("team", first)  # some sports nest under "team"
            team_id = team_obj.get("id")
    except (httpx.HTTPError, ValueError, KeyError, AttributeError) as e:
        print(f"[injuries_client] team lookup failed for {team_name} ({short_sport}): {e}")

    _team_id_cache[cache_key] = team_id
    return team_id


async def get_injury_notes(sport_key: str, team_name: str) -> str | None:
    """
    Return a short human-readable injury summary for a team, or None if
    unavailable (unsupported sport, no API key configured, lookup failed,
    or no injuries reported).
    """
    if not API_SPORTS_KEY:
        return None

    short_sport = ODDS_SPORT_KEY_TO_SHORT.get(sport_key)
    if not short_sport:
        return None

    base_url = API_SPORTS_BASES.get(short_sport)
    if not base_url:
        return None  # e.g. NCAAB - not supported by this provider

    async with httpx.AsyncClient(timeout=15) as client:
        team_id = await _resolve_team_id(client, base_url, short_sport, team_name)
        if not team_id:
            return None

        try:
            resp = await client.get(f"{base_url}/injuries", headers=_headers(), params={"team": team_id})
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            print(f"[injuries_client] injuries fetch failed for {team_name}: {e}")
            return None

    entries = data.get("response", [])
    if not entries:
        return f"No injuries currently reported for {team_name} (per API-Sports)."

    lines = []
    for entry in entries[:10]:  # cap so we don't blow the prompt up
        player = entry.get("player", {})
        name = player.get("name") or entry.get("player_name") or "Unknown player"
        status = (
            entry.get("status")
            or entry.get("type")
            or entry.get("description")
            or "status unclear"
        )
        lines.append(f"{name}: {status}")

    if not lines:
        return f"No injuries currently reported for {team_name} (per API-Sports)."

    return f"{team_name} injury report — " + "; ".join(lines)
