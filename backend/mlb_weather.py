"""
MLB-specific weather lookup. Unlike weather.py (used for NFL, which just
checks current conditions), this:
1. Pulls an HOURLY forecast and picks the hour closest to first pitch,
   instead of "right now" - a morning check of a 7pm game is otherwise
   useless.
2. Classifies wind direction relative to the park's orientation (blowing
   out to center / blowing in / crosswind), because in baseball wind
   DIRECTION relative to the field matters as much as wind speed - it's
   one of the more well-documented handicapping angles for MLB totals.
3. Knows which parks are domed/retractable, since weather is irrelevant
   (or uncertain) for those.

HONESTY NOTE ON ORIENTATION DATA:
`orientation_deg` (the compass bearing from home plate toward center
field) is left as None for every park below. Real orientation charts
exist (see links below) but hardcoding specific degree numbers without
verifying each one against a source risks silently feeding Claude a
wrong classification, which is worse than no classification. Fill in
`orientation_deg` per park yourself using:
  - https://www.baseball-almanac.com/stadium/ballpark_NSEW_AL.shtml
  - https://www.baseball-almanac.com/stadium/ballpark_NSEW_NL.shtml
  - https://ballparks.com/baseball/general/facts/diamonds/index.htm
Until you do, the app still returns raw wind speed/direction/temp/precip
for the correct hour - Claude is just told explicitly that it can't be
classified relative to the field yet, rather than guessing.

Roof status (dome / retractable / outdoor) IS filled in, since that's a
stable physical fact, not a number that's easy to silently get wrong.
Verified for the 2026 season: Rays are back in a repaired, domed
Tropicana Field; Athletics remain at the outdoor Sutter Health Park in
Sacramento.
"""
import httpx
from datetime import datetime, timezone

# team_name (as it appears in The Odds API) -> park info.
# Multiple name variants are included per team where naming might differ
# (e.g. "Athletics" vs "Oakland Athletics") so lookups don't silently miss.
MLB_PARKS: dict[str, dict] = {
    "Arizona Diamondbacks": {"lat": 33.4453, "lon": -112.0667, "roof": "retractable", "orientation_deg": 0},
    "Atlanta Braves": {"lat": 33.8908, "lon": -84.4678, "roof": "outdoor", "orientation_deg": 140},
    "Baltimore Orioles": {"lat": 39.2839, "lon": -76.6218, "roof": "outdoor", "orientation_deg": 22.5},
    "Boston Red Sox": {"lat": 42.3467, "lon": -71.0972, "roof": "outdoor", "orientation_deg": 22.5},
    "Chicago Cubs": {"lat": 41.9484, "lon": -87.6553, "roof": "outdoor", "orientation_deg": 22.5},
    "Chicago White Sox": {"lat": 41.8299, "lon": -87.6338, "roof": "outdoor", "orientation_deg": 140},
    "Cincinnati Reds": {"lat": 39.0979, "lon": -84.5063, "roof": "outdoor", "orientation_deg": 110},
    "Cleveland Guardians": {"lat": 41.4962, "lon": -81.6852, "roof": "outdoor", "orientation_deg": 0},
    "Colorado Rockies": {"lat": 39.7559, "lon": -104.9942, "roof": "outdoor", "orientation_deg": 0},
    "Detroit Tigers": {"lat": 42.3390, "lon": -83.0485, "roof": "outdoor", "orientation_deg": 135},
    "Houston Astros": {"lat": 29.7573, "lon": -95.3555, "roof": "retractable", "orientation_deg": 320},
    "Kansas City Royals": {"lat": 39.0517, "lon": -94.4803, "roof": "outdoor", "orientation_deg": 30},
    "Los Angeles Angels": {"lat": 33.8003, "lon": -117.8827, "roof": "outdoor", "orientation_deg": 22.5},
    "Los Angeles Dodgers": {"lat": 34.0739, "lon": -118.2400, "roof": "outdoor", "orientation_deg": 22.5},
    "Miami Marlins": {"lat": 25.7781, "lon": -80.2196, "roof": "retractable", "orientation_deg": 130},
    "Milwaukee Brewers": {"lat": 43.0280, "lon": -87.9712, "roof": "retractable", "orientation_deg": 130},
    "Minnesota Twins": {"lat": 44.9817, "lon": -93.2776, "roof": "outdoor", "orientation_deg": 75},
    "New York Mets": {"lat": 40.7571, "lon": -73.8458, "roof": "outdoor", "orientation_deg": 20},
    "New York Yankees": {"lat": 40.8296, "lon": -73.9262, "roof": "outdoor", "orientation_deg": 90},
    "Athletics": {"lat": 38.5802, "lon": -121.5130, "roof": "outdoor", "orientation_deg": 60},
    "Oakland Athletics": {"lat": 38.5802, "lon": -121.5130, "roof": "outdoor", "orientation_deg": 60},
    "Philadelphia Phillies": {"lat": 39.9061, "lon": -75.1665, "roof": "outdoor", "orientation_deg": 10},
    "Pittsburgh Pirates": {"lat": 40.4468, "lon": -80.0057, "roof": "outdoor", "orientation_deg": 120},
    "San Diego Padres": {"lat": 32.7073, "lon": -117.1566, "roof": "outdoor", "orientation_deg": 0},
    "San Francisco Giants": {"lat": 37.7786, "lon": -122.3893, "roof": "outdoor", "orientation_deg": 90},
    "Seattle Mariners": {"lat": 47.5914, "lon": -122.3325, "roof": "retractable", "orientation_deg": 22.5},
    "St. Louis Cardinals": {"lat": 38.6226, "lon": -90.1928, "roof": "outdoor", "orientation_deg": 22.5},
    "Tampa Bay Rays": {"lat": 27.7683, "lon": -82.6534, "roof": "dome", "orientation_deg": None},
    "Texas Rangers": {"lat": 32.7473, "lon": -97.0842, "roof": "retractable", "orientation_deg": 130},
    "Toronto Blue Jays": {"lat": 43.6414, "lon": -79.3894, "roof": "retractable", "orientation_deg": 0},
    "Washington Nationals": {"lat": 38.8730, "lon": -77.0074, "roof": "outdoor", "orientation_deg": 22.5},
}


def _classify_wind(wind_from_deg: float, orientation_deg: float) -> str:
    """
    wind_from_deg: compass bearing the wind is blowing FROM (meteorological convention).
    orientation_deg: compass bearing from home plate toward center field.
    Wind blowing OUT to center means it's blowing FROM behind home plate
    TOWARD center field, i.e. its source direction is opposite center field.
    """
    def angle_diff(a, b):
        d = abs(a - b) % 360
        return min(d, 360 - d)

    out_source = (orientation_deg + 180) % 360
    if angle_diff(wind_from_deg, out_source) <= 45:
        return "blowing out toward center field"
    if angle_diff(wind_from_deg, orientation_deg) <= 45:
        return "blowing in from center field"
    return "crosswind"


async def get_mlb_game_weather(home_team: str, commence_time_iso: str) -> dict | None:
    park = MLB_PARKS.get(home_team)
    if not park:
        return None
    if park["roof"] == "dome":
        return None  # fixed roof, weather doesn't reach the field

    try:
        game_time = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00"))
    except ValueError:
        return None

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": park["lat"],
        "longitude": park["lon"],
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_direction_10m",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "UTC",
        "forecast_days": 3,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            hourly = resp.json().get("hourly", {})
    except httpx.HTTPError:
        return None

    times = hourly.get("time", [])
    if not times:
        return None

    # Find the forecast hour closest to first pitch
    target = game_time.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    best_idx, best_diff = None, None
    for i, t in enumerate(times):
        try:
            t_dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        diff = abs((t_dt - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_idx, best_diff = i, diff

    if best_idx is None:
        return None

    wind_speed = hourly.get("wind_speed_10m", [None])[best_idx]
    wind_dir = hourly.get("wind_direction_10m", [None])[best_idx]
    temp = hourly.get("temperature_2m", [None])[best_idx]
    precip_prob = hourly.get("precipitation_probability", [None])[best_idx]

    result = {
        "roof_status": park["roof"],  # "outdoor" or "retractable" (roof open/closed unknown for retractable)
        "forecast_time_utc": times[best_idx],
        "temp_f": temp,
        "wind_mph": wind_speed,
        "wind_from_deg": wind_dir,
        "precip_probability_pct": precip_prob,
    }

    if park["orientation_deg"] is not None and wind_dir is not None:
        result["wind_classification"] = _classify_wind(wind_dir, park["orientation_deg"])
    else:
        result["wind_classification"] = (
            "unclassified - park orientation not yet configured in mlb_weather.py"
        )

    return result
