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
from mlb_weather import get_mlb_game_weather
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
    show_all: bool = Query(False, description="if true, include every analyzed game (even declined ones) with Claude's reasoning, not just picks meeting the threshold"),
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
    odds_fetch_errors = []
    for sport_key in sport_keys:
        try:
            events = await fetch_todays_events(sport_key)
        except Exception as e:  # noqa: BLE001
            error_msg = f"{sport_key}: {e}"
            print(f"[odds_client] failed to fetch {sport_key}: {e}")
            odds_fetch_errors.append(error_msg)
            continue
        for event in events:
            summary = summarize_event_odds(event)
            record_snapshot(summary)
            summary["line_movement"] = get_line_movement(summary["id"])
            if sport_key == "baseball_mlb":
                summary["weather"] = await get_mlb_game_weather(summary["home_team"], summary["commence_time"])
            else:
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
    all_analyses_debug = []
    all_full = []  # every game with full detail, regardless of has_pick/threshold
    for game, analysis in zip(all_summaries, analyses):
        all_analyses_debug.append({
            "matchup": f"{game['away_team']} @ {game['home_team']}",
            "has_pick": analysis.get("has_pick", False),
            "confidence": analysis.get("confidence", 0),
            "pick_type": analysis.get("pick_type"),
            "team": analysis.get("team"),
            "reasoning": analysis.get("reasoning"),
        })

        full_entry = {
            "sport": game["sport_title"],
            "matchup": f"{game['away_team']} @ {game['home_team']}",
            "commence_time": game["commence_time"],
            "consensus_home_spread": game["consensus_home_spread"],
            "consensus_total": game["consensus_total"],
            "line_movement": game["line_movement"],
            "weather": game["weather"],
            "has_pick": analysis.get("has_pick", False),
            "pick_type": analysis.get("pick_type"),
            "team": analysis.get("team"),
            "line": analysis.get("line"),
            "confidence": analysis.get("confidence", 0),
            "key_factors": analysis.get("key_factors", []),
            "reasoning": analysis.get("reasoning"),
        }
        all_full.append(full_entry)

        if analysis.get("has_pick") and analysis.get("confidence", 0) >= min_confidence:
            picks.append(full_entry)

    picks.sort(key=lambda p: p["confidence"], reverse=True)

    # "Best available" - top games by confidence even if none cleared your
    # threshold, so there's always something to look at day-to-day. Each
    # entry is honestly flagged with meets_threshold / has_real_edge so this
    # can never be mistaken for a genuine high-confidence pick when it isn't.
    best_available_pool = [g for g in all_full if g["has_pick"]]
    best_available_pool.sort(key=lambda g: g["confidence"], reverse=True)
    best_available = []
    for g in best_available_pool[:2]:
        entry = dict(g)
        entry["meets_your_threshold"] = g["confidence"] >= min_confidence
        best_available.append(entry)

    response = {
        "generated_at_utc": __import__("datetime").datetime.utcnow().isoformat(),
        "min_confidence": min_confidence,
        "games_analyzed": len(all_summaries),
        "picks_meeting_threshold": len(picks),
        "analysis_errors": analysis_errors,
        "odds_fetch_errors": odds_fetch_errors,
        "picks": picks,
        "best_available": best_available,
        "disclaimer": (
            "Confidence scores are Claude's analytical opinion based on the data "
            "provided, not a statistically calibrated win probability. Sports "
            "outcomes are inherently uncertain. Bet responsibly. 'best_available' "
            "entries are the day's top games EVEN IF they didn't meet your "
            "threshold - check meets_your_threshold before treating one as a "
            "genuine high-confidence pick."
        ),
    }
    if show_all:
        response["all_games"] = all_analyses_debug
    return response


# Serve the frontend
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
