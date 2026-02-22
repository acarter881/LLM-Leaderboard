#!/usr/bin/env python3
"""Generate a self-contained HTML dashboard from leaderboard time series data.

Focused on Kalshi settlement decisions: shows only the models contending
for #1 and the metrics that matter for predicting who will hold the top
spot at settlement (score gaps, overtake probability, H2H win rates,
vote accumulation / CI convergence).

Usage:
    python dashboard.py                        # default paths
    python dashboard.py -o my_dashboard.html   # custom output
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from snapshot_store import load_timeseries, DEFAULT_TIMESERIES_DIR

DEFAULT_OUTPUT = "dashboard.html"


# ---------------------------------------------------------------------------
# Data extraction — contender-focused
# ---------------------------------------------------------------------------

def extract_chart_data(timeseries: list[dict]) -> dict:
    """Transform JSONL timeseries into chart-ready data for contenders only.

    "Contenders" = models that appear in overtake_top5 with prob > 1%,
    plus any model ranked #2 or #3 in the latest snapshot (in case
    overtake data hasn't been computed yet).

    Returns a dict with:
        timestamps   - list of ISO timestamps
        leader       - name of current #1
        contenders   - ordered list of contender names
        score_gap    - {name: [leader_score - contender_score, ...]}
        overtake     - {name: [prob, ...]}
        h2h          - {name: [win_rate, ...]}
        votes        - {name: [total_votes, ...]}  (includes leader)
        ci           - {name: [ci_value, ...]}      (includes leader)
        leader_prob  - [prob_staying_1, ...]
    """
    empty = {
        "timestamps": [], "leader": None, "contenders": [],
        "score_gap": {}, "overtake": {}, "h2h": {},
        "votes": {}, "ci": {}, "leader_prob": [],
    }
    if not timeseries:
        return empty

    latest = timeseries[-1]
    latest_models = latest.get("models", [])
    if not latest_models:
        return empty

    leader_name = latest_models[0].get("name", "")

    # --- Identify contenders ---
    # From overtake data: any model with prob > 1% in any record.
    contender_set: set[str] = set()
    for record in timeseries:
        for o in record.get("overtake_top5", []):
            name = o.get("name")
            prob = o.get("prob", 0)
            if name and prob and prob > 0.01:
                contender_set.add(name)

    # Fallback: if no overtake data, use #2 and #3 from latest snapshot.
    if not contender_set:
        for m in latest_models[1:3]:
            name = m.get("name")
            if name:
                contender_set.add(name)

    # Also include H2H models.
    for record in timeseries:
        for h in record.get("h2h_top5", []):
            name = h.get("name")
            if name:
                contender_set.add(name)

    contender_set.discard(leader_name)
    # Order by latest rank.
    latest_rank = {}
    for m in latest_models:
        name = m.get("name")
        if name:
            latest_rank[name] = m.get("rank", 999)
    contenders = sorted(contender_set, key=lambda n: latest_rank.get(n, 999))

    # All models we need data for (leader + contenders).
    all_names = [leader_name] + contenders

    # --- Build per-timestamp data ---
    timestamps: list[str] = []
    score_gap: dict[str, list[float | None]] = {n: [] for n in contenders}
    overtake: dict[str, list[float | None]] = {n: [] for n in contenders}
    h2h: dict[str, list[float | None]] = {n: [] for n in contenders}
    votes: dict[str, list[int | None]] = {n: [] for n in all_names}
    ci: dict[str, list[float | None]] = {n: [] for n in all_names}
    leader_prob: list[float | None] = []

    for record in timeseries:
        timestamps.append(record.get("ts", ""))

        # Model lookup for this snapshot.
        model_lookup: dict[str, dict] = {}
        for m in record.get("models", []):
            name = m.get("name")
            if name:
                model_lookup[name] = m

        leader = model_lookup.get(leader_name)
        leader_score = leader.get("score") if leader else None

        # Votes & CI for leader + all contenders.
        for name in all_names:
            m = model_lookup.get(name)
            votes[name].append(m.get("votes") if m else None)
            ci[name].append(m.get("ci") if m else None)

        # Score gap, overtake, H2H for contenders only.
        overtake_lookup = {}
        for o in record.get("overtake_top5", []):
            if o.get("name"):
                overtake_lookup[o["name"]] = o.get("prob")
        h2h_lookup = {}
        for h in record.get("h2h_top5", []):
            if h.get("name"):
                h2h_lookup[h["name"]] = h.get("wr")

        for name in contenders:
            m = model_lookup.get(name)
            contender_score = m.get("score") if m else None
            if leader_score is not None and contender_score is not None:
                score_gap[name].append(leader_score - contender_score)
            else:
                score_gap[name].append(None)
            overtake[name].append(overtake_lookup.get(name))
            h2h[name].append(h2h_lookup.get(name))

        leader_prob.append(record.get("leader_prob_staying_1"))

    return {
        "timestamps": timestamps,
        "leader": leader_name,
        "contenders": contenders,
        "score_gap": score_gap,
        "overtake": overtake,
        "h2h": h2h,
        "votes": votes,
        "ci": ci,
        "leader_prob": leader_prob,
    }


# ---------------------------------------------------------------------------
# HTML template — 4 focused charts
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arena Leaderboard Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 20px;
  }
  h1 {
    text-align: center;
    margin-bottom: 4px;
    color: #e94560;
    font-size: 1.6em;
  }
  .subtitle {
    text-align: center;
    color: #888;
    font-size: 0.85em;
    margin-bottom: 24px;
  }
  .chart-container {
    background: #16213e;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }
  .chart-container h2 {
    color: #e94560;
    font-size: 1.1em;
    margin-bottom: 8px;
  }
  .chart-container .chart-note {
    color: #888;
    font-size: 0.8em;
    margin-bottom: 4px;
  }
  .chart { width: 100%%; height: 380px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .note {
    text-align: center;
    color: #666;
    font-size: 0.8em;
    margin-top: 16px;
  }
</style>
</head>
<body>
<h1>Kalshi Settlement Dashboard</h1>
<div class="subtitle">%(leader_label)s &mdash; Generated: %(generated_at)s &mdash; %(num_records)d snapshots</div>

<div class="chart-container">
  <h2>Score Gap to #1</h2>
  <p class="chart-note">Points behind the leader. Falling = catching up. Zero = tied.</p>
  <div id="chart-gap" class="chart"></div>
</div>

<div class="grid">
  <div class="chart-container">
    <h2>Overtake Probability</h2>
    <p class="chart-note">Chance each contender surpasses #1 given current uncertainty.</p>
    <div id="chart-overtake" class="chart"></div>
  </div>
  <div class="chart-container">
    <h2>H2H Win Rate vs #1</h2>
    <p class="chart-note">Predicted head-to-head win %% (Bradley-Terry / Elo).</p>
    <div id="chart-h2h" class="chart"></div>
  </div>
</div>

<div class="grid">
  <div class="chart-container">
    <h2>Total Votes</h2>
    <p class="chart-note">More votes = faster CI convergence = standings lock in sooner.</p>
    <div id="chart-votes" class="chart"></div>
  </div>
  <div class="chart-container">
    <h2>Confidence Interval</h2>
    <p class="chart-note">Uncertainty (±points). Narrowing = scores are settling.</p>
    <div id="chart-ci" class="chart"></div>
  </div>
</div>

<div class="note">Data: data/timeseries/top20.jsonl &mdash; auto-regenerated on each leaderboard check</div>

<script>
const D = %(chart_data_json)s;
const CONTENDERS = D.contenders || [];
const LEADER = D.leader || '';
const ALL = [LEADER].concat(CONTENDERS).filter(Boolean);

const LAYOUT = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(22,33,62,0.5)',
  font: { color: '#e0e0e0', size: 11 },
  margin: { l: 55, r: 20, t: 10, b: 40 },
  legend: { orientation: 'h', y: -0.22, font: { size: 10 } },
  xaxis: { gridcolor: '#2a2a4a', type: 'date' },
  yaxis: { gridcolor: '#2a2a4a' },
  hovermode: 'x unified',
};
const CFG = { responsive: true, displayModeBar: false };

const COLORS = ['#e94560', '#53d8fb', '#f0a500', '#48c774', '#a55eea', '#fd9644'];
function color(i) { return COLORS[i %% COLORS.length]; }

// --- Score Gap chart ---
(function() {
  if (!CONTENDERS.length) return;
  const traces = CONTENDERS.map((name, i) => ({
    x: D.timestamps, y: D.score_gap[name], name: name,
    type: 'scatter', mode: 'lines+markers',
    line: { color: color(i), width: 2.5 }, marker: { size: 5 },
    connectgaps: false,
  }));
  // Zero reference line.
  traces.push({
    x: [D.timestamps[0], D.timestamps[D.timestamps.length - 1]],
    y: [0, 0], name: 'Tied', type: 'scatter', mode: 'lines',
    line: { color: '#666', width: 1, dash: 'dash' }, showlegend: false,
  });
  Plotly.newPlot('chart-gap', traces, {
    ...LAYOUT,
    yaxis: { ...LAYOUT.yaxis, title: 'Points behind #1' },
  }, CFG);
})();

// --- Overtake probability chart ---
(function() {
  const names = CONTENDERS.filter(n => D.overtake[n] && D.overtake[n].some(v => v != null));
  if (!names.length) {
    document.getElementById('chart-overtake').innerHTML =
      '<p style="text-align:center;color:#888;padding:40px;">No overtake data yet.</p>';
    return;
  }
  const traces = names.map((name, i) => ({
    x: D.timestamps, y: D.overtake[name].map(v => v != null ? v * 100 : null),
    name: name, type: 'scatter', mode: 'lines+markers',
    line: { color: color(i), width: 2.5 }, marker: { size: 5 },
    connectgaps: false,
  }));
  Plotly.newPlot('chart-overtake', traces, {
    ...LAYOUT,
    yaxis: { ...LAYOUT.yaxis, title: 'Overtake %%', rangemode: 'tozero' },
  }, CFG);
})();

// --- H2H win rate chart ---
(function() {
  const names = CONTENDERS.filter(n => D.h2h[n] && D.h2h[n].some(v => v != null));
  if (!names.length) {
    document.getElementById('chart-h2h').innerHTML =
      '<p style="text-align:center;color:#888;padding:40px;">No H2H data yet.</p>';
    return;
  }
  const traces = names.map((name, i) => ({
    x: D.timestamps, y: D.h2h[name].map(v => v != null ? v * 100 : null),
    name: name, type: 'scatter', mode: 'lines+markers',
    line: { color: color(i), width: 2.5 }, marker: { size: 5 },
    connectgaps: false,
  }));
  traces.push({
    x: [D.timestamps[0], D.timestamps[D.timestamps.length - 1]],
    y: [50, 50], name: '50%%', type: 'scatter', mode: 'lines',
    line: { color: '#666', width: 1, dash: 'dash' }, showlegend: false,
  });
  Plotly.newPlot('chart-h2h', traces, {
    ...LAYOUT,
    yaxis: { ...LAYOUT.yaxis, title: 'Win Rate vs #1 %%' },
  }, CFG);
})();

// --- Votes chart (leader + contenders) ---
(function() {
  if (!ALL.length) return;
  const traces = ALL.map((name, i) => ({
    x: D.timestamps, y: D.votes[name], name: name,
    type: 'scatter', mode: 'lines+markers',
    line: { color: color(i), width: name === LEADER ? 3 : 2, dash: name === LEADER ? 'dot' : 'solid' },
    marker: { size: name === LEADER ? 6 : 5 },
    connectgaps: false,
  }));
  Plotly.newPlot('chart-votes', traces, {
    ...LAYOUT,
    yaxis: { ...LAYOUT.yaxis, title: 'Total Votes' },
  }, CFG);
})();

// --- CI chart (leader + contenders) ---
(function() {
  if (!ALL.length) return;
  const traces = ALL.map((name, i) => ({
    x: D.timestamps, y: D.ci[name], name: name,
    type: 'scatter', mode: 'lines+markers',
    line: { color: color(i), width: name === LEADER ? 3 : 2, dash: name === LEADER ? 'dot' : 'solid' },
    marker: { size: name === LEADER ? 6 : 5 },
    connectgaps: false,
  }));
  Plotly.newPlot('chart-ci', traces, {
    ...LAYOUT,
    yaxis: { ...LAYOUT.yaxis, title: 'CI (±points)' },
  }, CFG);
})();
</script>
</body>
</html>
"""


def generate_dashboard(
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
    output_path: str | Path = DEFAULT_OUTPUT,
) -> Path:
    """Generate the HTML dashboard file.

    Returns the path to the generated HTML file.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    timeseries = load_timeseries(timeseries_dir)
    chart_data = extract_chart_data(timeseries)

    ct_now = datetime.now(ZoneInfo("America/Chicago"))
    generated_at = ct_now.strftime("%Y-%m-%d %H:%M:%S %Z")

    leader = chart_data.get("leader") or "No data"
    leader_label = f"Current #1: {html.escape(leader)}" if leader else "No data"

    html_content = _HTML_TEMPLATE % {
        "generated_at": html.escape(generated_at),
        "num_records": len(timeseries),
        "leader_label": leader_label,
        "chart_data_json": json.dumps(chart_data, ensure_ascii=False),
    }

    output_path = Path(output_path)
    output_path.write_text(html_content, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate leaderboard dashboard HTML.")
    parser.add_argument(
        "--timeseries-dir", type=Path, default=Path(DEFAULT_TIMESERIES_DIR),
        help=f"Directory with JSONL timeseries (default: {DEFAULT_TIMESERIES_DIR})",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path(DEFAULT_OUTPUT),
        help=f"Output HTML file path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    output = generate_dashboard(
        timeseries_dir=args.timeseries_dir,
        output_path=args.output,
    )
    print(f"Dashboard generated: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
