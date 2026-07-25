"""
Run with: uvicorn main:app --reload --host 0.0.0.0 --port 8000
See README.md for setup instructions.
"""
import asyncio
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import SPORTS, DEFAULT_MIN_CONFIDENCE
from odds_client import fetch_todays_events, summarize_event_odds
from line_tracker import init_db, record_snapshot, get_line_movement
from weather import get_game_weather
from injuries_client import get_injury_notes
from ai_analyzer import analyze_game

app = FastAPI(title="Today's ATS/ML Picks")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


@app.get("/api/sports")
def list_sports():
    return {"sports": list(SPORTS.keys())}


@app.get("/api/picks")
async def get_picks(
    sport: str = Query("all", description="one of: all, " + ", ".join(SPORTS.keys())),
    min_confidence: int = Query(DEFAULT_MIN_CONFIDENCE, ge=0, le=100),
):
    """
    Fetch today's games for the requested sport(s), snapshot the lines,
    run each game through the AI analyzer, and return only picks that
    meet the confidence threshold.
    """
    sport_keys = list(SPORTS.values()) if sport == "all" else [SPORTS.get(sport)]
    sport_keys = [s for s in sport_keys if s]
    if not sport_keys:
        return {"error": f"Unknown sport '{sport}'. Valid: all, {', '.join(SPORTS.keys())}", "picks": []}

    all_summaries = []
    for sport_key in sport_keys:
        try:
            events = await fetch_todays_events(sport_key)
        except Exception as e:  # noqa: BLE001
            print(f"[odds_client] failed to fetch {sport_key}: {e}")
            continue
        for event in events:
            summary = summarize_event_odds(event)
            record_snapshot(summary)
            summary["line_movement"] = get_line_movement(summary["id"])
            summary["weather"] = await get_game_weather(summary["home_team"])

            home_injuries = await get_injury_notes(sport_key, summary["home_team"])
            away_injuries = await get_injury_notes(sport_key, summary["away_team"])
            injury_parts = [n for n in (home_injuries, away_injuries) if n]
            summary["injury_notes"] = " | ".join(injury_parts) if injury_parts else None

            all_summaries.append(summary)

    # Run AI analysis concurrently
    analyses = await asyncio.gather(*(analyze_game(g) for g in all_summaries))
    analysis_errors = sum(1 for a in analyses if a.get("_error"))

    picks = []
    for game, analysis in zip(all_summaries, analyses):
        if analysis.get("has_pick") and analysis.get("confidence", 0) >= min_confidence:
            picks.append({
                "sport": game["sport_title"],
                "matchup": f"{game['away_team']} @ {game['home_team']}",
                "commence_time": game["commence_time"],
                "consensus_home_spread": game["consensus_home_spread"],
                "consensus_total": game["consensus_total"],
                "line_movement": game["line_movement"],
                "weather": game["weather"],
                "pick_type": analysis["pick_type"],
                "team": analysis["team"],
                "line": analysis["line"],
                "confidence": analysis["confidence"],
                "key_factors": analysis.get("key_factors", []),
                "reasoning": analysis["reasoning"],
            })

    picks.sort(key=lambda p: p["confidence"], reverse=True)
    return {
        "generated_at_utc": __import__("datetime").datetime.utcnow().isoformat(),
        "min_confidence": min_confidence,
        "games_analyzed": len(all_summaries),
        "picks_meeting_threshold": len(picks),
        "analysis_errors": analysis_errors,
        "picks": picks,
        "disclaimer": (
            "Confidence scores are Claude's analytical opinion based on the data "
            "provided, not a statistically calibrated win probability. Sports "
            "outcomes are inherently uncertain. Bet responsibly."
        ),
    }


# Serve the frontend
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
