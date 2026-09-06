"""
Injury report lookups via API-Sports (https://api-sports.io).
"""
import httpx
from config import API_SPORTS_KEY, API_SPORTS_BASES

ODDS_SPORT_KEY_TO_SHORT = {
    "americanfootball_nfl": "nfl",
    "americanfootball_ncaaf": "ncaaf",
    "basketball_nba": "nba",
    "baseball_mlb": "mlb",
    "icehockey_nhl": "nhl",
}

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
            first = results[0]
            team_obj = first.get("team", first)
            team_id = team_obj.get("id")
    except (httpx.HTTPError, ValueError, KeyError, AttributeError) as e:
        print(f"[injuries_client] team lookup failed for {team_name} ({short_sport}): {e}")

    _team_id_cache[cache_key] = team_id
    return team_id


async def get_injury_notes(sport_key: str, team_name: str) -> str | None:
    if not API_SPORTS_KEY:
        return None

    short_sport = ODDS_SPORT_KEY_TO_SHORT.get(sport_key)
    if not short_sport:
        return None

    base_url = API_SPORTS_BASES.get(short_sport)
    if not base_url:
        return None

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
    for entry in entries[:10]:
        player = entry.get("player", {})
        name = player.get("name") or entry.get("player_name") or "Unknown player"
        status = entry.get("status") or entry.get("type") or entry.get("description") or "status unclear"
        lines.append(f"{name}: {status}")

    if not lines:
        return f"No injuries currently reported for {team_name} (per API-Sports)."

    return f"{team_name} injury report — " + "; ".join(lines)
