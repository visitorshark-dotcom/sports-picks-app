"""
Best-effort weather lookup for outdoor games, using Open-Meteo (free, no API key).
This only works if you supply a lat/lon for the home team's stadium in
STADIUM_COORDS below. Fill this in for the teams/sports you care about most —
it's not practical to hardcode every college venue, so NCAA coverage here is
left sparse on purpose. Indoor/dome venues should map to None (skip weather).

If a team isn't in the map, weather is simply omitted from that game's
analysis rather than guessed at.
"""
import httpx

# Fill in as needed. Format: "Team Name": (lat, lon) or None for domed/indoor.
STADIUM_COORDS: dict[str, tuple[float, float] | None] = {
    "Green Bay Packers": (44.5013, -88.0622),
    "Buffalo Bills": (42.7738, -78.7870),
    "Chicago Bears": (41.8623, -87.6167),
    "Kansas City Chiefs": (39.0489, -94.4839),
    # Add more NFL/NCAAF teams here. Domed teams (e.g. "Las Vegas Raiders",
    # "New Orleans Saints", "Atlanta Falcons", "Detroit Lions", "Minnesota Vikings",
    # "Dallas Cowboys", "Houston Texans", "Indianapolis Colts", "Arizona Cardinals")
    # should map to None so weather is skipped for them.
}


async def get_game_weather(home_team: str) -> dict | None:
    coords = STADIUM_COORDS.get(home_team)
    if not coords:
        return None
    lat, lon = coords
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json().get("current", {})
        return {
            "temp_f": data.get("temperature_2m"),
            "precipitation_mm": data.get("precipitation"),
            "wind_mph": data.get("wind_speed_10m"),
        }
    except httpx.HTTPError:
        return None
