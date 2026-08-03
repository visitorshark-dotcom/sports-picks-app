"""
Sends structured game data to Claude and asks for a disciplined,
evidence-grounded ATS/ML pick with a confidence score.

IMPORTANT HONESTY NOTE (read this, don't just delete it):
"Confidence score" here means "how strongly the model's stated reasoning
supports this pick given the data it was given" — NOT a calibrated
real-world win probability. Betting markets are highly efficient; treat
every output as one analytical opinion among many inputs, not a lock.
"""
import json
import re
from anthropic import AsyncAnthropic
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are a disciplined sports betting analyst. You will be given \
odds data, line movement, and any available injury/weather context for one game. \
Every game has a side the market favors (even if only slightly) - your job is to \
name that side (ATS or ML, whichever you find more compelling) and give an honest \
confidence score for how strong that edge actually is. You are NOT deciding whether \
to "have a pick" - you always name a side. The confidence score is what carries all \
the honesty about how strong or weak that edge is.

Rules you must follow:
- ALWAYS populate pick_type, team, and line - never null. If the edge is weak, that's \
what the confidence score is for (e.g. 52-58), not a reason to omit the pick entirely.
- Be conservative on the confidence NUMBER, not on whether to name a side. Most games \
should score well below 60 - it is correct and expected for many games to land in the \
50s. Reward-hunting for high confidence is not the goal; accuracy of your stated \
uncertainty is.
- Ground every claim in the data actually provided. Do not invent injuries, \
records, or trends you were not given. If you don't have enough information to \
judge something (e.g. injuries), say so explicitly rather than guessing, and let \
that uncertainty pull your confidence down rather than making you omit a side.
- CRITICAL - price/juice matters, not just "who wins": a heavily-favored moneyline \
(e.g. -200 or worse) already has a high win probability baked into its price. \
Picking that side isn't valuable just because they're likely to win - it's only \
valuable if you believe the TRUE win probability is meaningfully higher than the \
market's implied probability (given to you in the prompt). If you don't have a \
specific reason to think the market is underpricing a heavy favorite, prefer the \
ATS side instead (spreads are priced close to -110 regardless of which team is \
favored, so the risk/reward is far better for the same underlying opinion), or \
lower your confidence to reflect that it's poor value even if you think they'll win. \
Do not recommend a heavily-juiced moneyline (-180 or worse) at high confidence \
unless your reasoning explicitly explains why the true edge exceeds the price.
- Consider: line movement vs. the initial number (sharp money signal), \
discrepancies between books, situational factors (rest, travel, weather for \
outdoor games), and any injury notes given.
- For MLB games with a "retractable" roof status, the actual open/closed \
state is unknown to you - treat any weather data for those as a minor, \
uncertain factor rather than a confident signal. If wind is "unclassified" \
(park orientation not configured), you can still note wind speed generally \
but should not claim a directional carry/suppression effect you can't verify.
- Output ONLY valid JSON, no markdown fences, no commentary outside the JSON.
- Keep "reasoning" to 2-4 sentences and "key_factors" to short phrases (under \
15 words each) - concise, not padded. This keeps responses reliably complete.

JSON schema:
{
  "pick_type": "ATS" | "ML",         // always populated
  "team": string,                     // always populated - the side you lean toward
  "line": string,                     // e.g. "-3.5" or "+150", always populated
  "confidence": integer,             // 0-100, your honest analytical confidence
  "key_factors": [string],           // 2-5 short bullet points, factual
  "reasoning": string                // 2-4 sentences, plain language
}"""


def _implied_probability(american_odds) -> float | None:
    """Convert American odds to the market's implied win probability (%)."""
    if american_odds is None:
        return None
    try:
        odds = float(american_odds)
    except (TypeError, ValueError):
        return None
    if odds > 0:
        return round(100 / (odds + 100) * 100, 1)
    else:
        return round(-odds / (-odds + 100) * 100, 1)


def _build_user_prompt(game: dict) -> str:
    parts = [
        f"Sport: {game.get('sport_title')}",
        f"Matchup: {game.get('away_team')} @ {game.get('home_team')}",
        f"Kickoff/tip (UTC): {game.get('commence_time')}",
        f"Consensus home spread: {game.get('consensus_home_spread')}",
        f"Consensus total: {game.get('consensus_total')}",
        f"Consensus home ML: {game.get('consensus_home_ml')}, away ML: {game.get('consensus_away_ml')}",
    ]
    home_implied = _implied_probability(game.get("consensus_home_ml"))
    away_implied = _implied_probability(game.get("consensus_away_ml"))
    if home_implied is not None or away_implied is not None:
        parts.append(
            f"Market's IMPLIED win probability from those prices: home {home_implied}%, "
            f"away {away_implied}% (this already bakes in the vig - compare your own "
            f"confidence against this number before recommending a heavily-favored side)."
        )
    parts.append(f"Number of books quoted: {game.get('num_books')}")
    movement = game.get("line_movement")
    if movement:
        parts.append(
            f"Line movement — spread: {movement.get('opening_spread')} -> "
            f"{movement.get('current_spread')} (move: {movement.get('spread_move')}); "
            f"total: {movement.get('opening_total')} -> {movement.get('current_total')} "
            f"(move: {movement.get('total_move')}); based on {movement.get('snapshots_count')} snapshots."
        )
    else:
        parts.append("Line movement — not enough historical snapshots yet (need repeated polling).")

    weather = game.get("weather")
    if weather:
        if "wind_classification" in weather:
            # MLB-style detailed forecast
            parts.append(
                f"Weather forecast for approx. first-pitch hour ({weather.get('forecast_time_utc')} UTC): "
                f"{weather.get('temp_f')}F, wind {weather.get('wind_mph')} mph from {weather.get('wind_from_deg')}° "
                f"({weather.get('wind_classification')}), precipitation probability {weather.get('precip_probability_pct')}%. "
                f"Roof status: {weather.get('roof_status')}"
                + (" (open/closed unknown - weight this weather data accordingly)" if weather.get("roof_status") == "retractable" else "") + "."
            )
        else:
            parts.append(
                f"Weather at kickoff (approx, current conditions): {weather.get('temp_f')}F, "
                f"wind {weather.get('wind_mph')} mph, precipitation {weather.get('precipitation_mm')} mm."
            )
    else:
        parts.append("Weather — not available (indoor/domed venue, unmapped stadium, or fetch failed).")

    injuries = game.get("injury_notes")
    if injuries:
        parts.append(f"Injury notes (user-supplied): {injuries}")
    else:
        parts.append("Injury notes — none supplied.")

    return "\n".join(parts)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


async def analyze_game(game: dict) -> dict:
    """Call Claude once for a single game and return the parsed pick dict."""
    try:
        response = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(game)}],
        )
        raw_text = "".join(block.text for block in response.content if block.type == "text")
        parsed = _extract_json(raw_text)
    except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001 - surface as a non-pick, don't crash the batch
        print(f"[ai_analyzer] FAILED for {game.get('away_team')} @ {game.get('home_team')}: {repr(e)}")
        parsed = {
            "pick_type": None,
            "team": None,
            "line": None,
            "confidence": 0,
            "key_factors": [],
            "reasoning": f"Analysis failed: {e}",
            "_error": True,
        }
    return parsed
