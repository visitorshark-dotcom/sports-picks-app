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
Your job is to decide whether there is a genuinely strong ATS (against the spread) \
or moneyline case for one side, and to assign an honest confidence score.

Rules you must follow:
- Be conservative. Most games do NOT have a strong enough edge to warrant a high \
confidence score. It is correct and expected for many games to score below 60.
- Ground every claim in the data actually provided. Do not invent injuries, \
records, or trends you were not given. If you don't have enough information to \
judge something (e.g. injuries), say so explicitly rather than guessing.
- Reward-hunting for "70+ confidence" picks is not the goal — accuracy of your \
own stated uncertainty is the goal.
- Consider: line movement vs. the initial number (sharp money signal), \
discrepancies between books, situational factors (rest, travel, weather for \
outdoor games), and any injury notes given.
- Output ONLY valid JSON, no markdown fences, no commentary outside the JSON.

JSON schema:
{
  "has_pick": boolean,               // false if no side has a real edge
  "pick_type": "ATS" | "ML" | null,
  "team": string | null,             // the team you'd back
  "line": string | null,             // e.g. "-3.5" or "+150"
  "confidence": integer,             // 0-100, your honest analytical confidence
  "key_factors": [string],           // 2-5 short bullet points, factual
  "reasoning": string                // 2-4 sentences, plain language
}"""


def _build_user_prompt(game: dict) -> str:
    parts = [
        f"Sport: {game.get('sport_title')}",
        f"Matchup: {game.get('away_team')} @ {game.get('home_team')}",
        f"Kickoff/tip (UTC): {game.get('commence_time')}",
        f"Consensus home spread: {game.get('consensus_home_spread')}",
        f"Consensus total: {game.get('consensus_total')}",
        f"Consensus home ML: {game.get('consensus_home_ml')}, away ML: {game.get('consensus_away_ml')}",
        f"Number of books quoted: {game.get('num_books')}",
    ]
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
        parts.append(
            f"Weather at kickoff (approx, current conditions): {weather.get('temp_f')}F, "
            f"wind {weather.get('wind_mph')} mph, precipitation {weather.get('precipitation_mm')} mm."
        )
    else:
        parts.append("Weather — not available (indoor venue, unmapped stadium, or fetch failed).")

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
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(game)}],
        )
        raw_text = "".join(block.text for block in response.content if block.type == "text")
        parsed = _extract_json(raw_text)
    except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001 - surface as a non-pick, don't crash the batch
        parsed = {
            "has_pick": False,
            "pick_type": None,
            "team": None,
            "line": None,
            "confidence": 0,
            "key_factors": [],
            "reasoning": f"Analysis failed: {e}",
        }
    return parsed
