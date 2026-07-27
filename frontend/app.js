const API_BASE = ""; // same origin; change if backend is hosted elsewhere

const sportFilter = document.getElementById("sportFilter");
const confSlider = document.getElementById("confSlider");
const confVal = document.getElementById("confVal");
const loadBtn = document.getElementById("loadBtn");
const statusEl = document.getElementById("status");
const container = document.getElementById("picksContainer");

confSlider.addEventListener("input", () => {
  confVal.textContent = confSlider.value;
});

loadBtn.addEventListener("click", loadPicks);

async function loadPicks() {
  container.innerHTML = "";
  statusEl.textContent = "Fetching odds and running AI analysis... this can take a bit.";

  const sport = sportFilter.value;
  const minConfidence = confSlider.value;

  try {
    const res = await fetch(`${API_BASE}/api/picks?sport=${sport}&min_confidence=${minConfidence}`);
    const data = await res.json();

    if (data.error) {
      statusEl.textContent = data.error;
      return;
    }

    statusEl.textContent =
      `Analyzed ${data.games_analyzed} game(s). ${data.picks_meeting_threshold} pick(s) meet the ${data.min_confidence}%+ threshold. ` +
      (data.analysis_errors > 0 ? `⚠️ ${data.analysis_errors} game(s) failed AI analysis (see server logs). ` : "") +
      (data.odds_fetch_errors && data.odds_fetch_errors.length > 0
        ? `🛑 Odds fetch failed: ${data.odds_fetch_errors.join(" | ")} `
        : "") +
      `Generated ${new Date(data.generated_at_utc + "Z").toLocaleString()}.`;

    if (data.picks.length === 0) {
      container.innerHTML = `<p style="color:#8a93a8">No games currently meet this confidence threshold. Try lowering it or checking back later as lines move.</p>`;
      return;
    }

    data.picks.forEach(pick => container.appendChild(renderCard(pick)));
  } catch (err) {
    statusEl.textContent = "Failed to load picks. Is the backend running? " + err;
  }
}

function renderCard(pick) {
  const card = document.createElement("div");
  card.className = "pick-card";

  const confClass = pick.confidence >= 80 ? "conf-high" : "conf-mid";
  const kickoff = new Date(pick.commence_time).toLocaleString();

  const factors = (pick.key_factors || [])
    .map(f => `<li>${escapeHtml(f)}</li>`)
    .join("");

  card.innerHTML = `
    <div class="sport-tag">${escapeHtml(pick.sport)}</div>
    <h3>${escapeHtml(pick.matchup)}</h3>
    <span class="confidence-badge ${confClass}">${pick.confidence}% confidence</span>
    <div class="pick-line">${pick.pick_type}: <strong>${escapeHtml(pick.team || "")} ${escapeHtml(pick.line || "")}</strong></div>
    <ul class="key-factors">${factors}</ul>
    <div class="reasoning">${escapeHtml(pick.reasoning)}</div>
    <div class="meta">Kickoff/tip: ${kickoff} &middot; Consensus spread: ${pick.consensus_home_spread ?? "N/A"} &middot; Total: ${pick.consensus_total ?? "N/A"}</div>
  `;
  return card;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// Auto-load on first visit with default filters
loadPicks();
