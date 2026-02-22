#!/usr/bin/env python3
"""Generate a self-contained HTML dashboard from leaderboard time series data.

Reads the JSONL time series (and optionally full snapshots) and outputs a
single HTML file with interactive Plotly.js charts.  Open it in any browser —
no server required.

Usage:
    python dashboard.py                        # default paths
    python dashboard.py -o my_dashboard.html   # custom output
    python dashboard.py --top 10               # show top 10 models
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from snapshot_store import load_timeseries, DEFAULT_TIMESERIES_DIR

DEFAULT_OUTPUT = "dashboard.html"
DEFAULT_TOP_N = 10


# ---------------------------------------------------------------------------
# Data extraction from timeseries records
# ---------------------------------------------------------------------------

def extract_chart_data(
    timeseries: list[dict],
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """Transform JSONL timeseries records into chart-ready data structures.

    Returns a dict with keys for each chart type, each containing lists
    of timestamps and per-model data series.
    """
    if not timeseries:
        return {"timestamps": [], "models": {}, "overtake": {}, "leader_prob": []}

    timestamps: list[str] = []
    # model_name -> list of {score, rank, ci, votes} per timestamp (None if absent)
    model_data: dict[str, list[dict | None]] = {}
    # model_name -> list of overtake prob per timestamp
    overtake_data: dict[str, list[float | None]] = {}
    # leader probability of staying #1
    leader_prob: list[float | None] = []
    # H2H win rates: model_name -> list of win rate per timestamp
    h2h_data: dict[str, list[float | None]] = {}

    # Collect all model names that ever appeared in top N.
    all_model_names: set[str] = set()
    for record in timeseries:
        for m in record.get("models", [])[:top_n]:
            name = m.get("name")
            if name:
                all_model_names.add(name)

    # Also collect all model names from overtake data.
    for record in timeseries:
        for o in record.get("overtake_top5", []):
            name = o.get("name")
            if name:
                all_model_names.add(name)

    # Also collect H2H model names.
    for record in timeseries:
        for h in record.get("h2h_top5", []):
            name = h.get("name")
            if name:
                all_model_names.add(name)

    # Initialize data structures.
    for name in all_model_names:
        model_data[name] = []
        overtake_data[name] = []
        h2h_data[name] = []

    for record in timeseries:
        ts = record.get("ts", "")
        timestamps.append(ts)

        # Build lookup for this record's models.
        record_models = {}
        for m in record.get("models", []):
            name = m.get("name")
            if name:
                record_models[name] = m

        # Fill model data.
        for name in all_model_names:
            m = record_models.get(name)
            if m:
                model_data[name].append({
                    "score": m.get("score"),
                    "rank": m.get("rank"),
                    "ci": m.get("ci"),
                    "votes": m.get("votes"),
                })
            else:
                model_data[name].append(None)

        # Overtake data.
        overtake_lookup = {}
        for o in record.get("overtake_top5", []):
            if o.get("name"):
                overtake_lookup[o["name"]] = o.get("prob")
        for name in all_model_names:
            overtake_data[name].append(overtake_lookup.get(name))

        # Leader prob.
        leader_prob.append(record.get("leader_prob_staying_1"))

        # H2H data.
        h2h_lookup = {}
        for h in record.get("h2h_top5", []):
            if h.get("name"):
                h2h_lookup[h["name"]] = h.get("wr")
        for name in all_model_names:
            h2h_data[name].append(h2h_lookup.get(name))

    return {
        "timestamps": timestamps,
        "models": model_data,
        "overtake": overtake_data,
        "leader_prob": leader_prob,
        "h2h": h2h_data,
    }


# ---------------------------------------------------------------------------
# HTML generation
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
<h1>Arena Leaderboard Dashboard</h1>
<div class="subtitle">Generated: %(generated_at)s &mdash; %(num_records)d data points</div>

<div class="chart-container">
  <h2>Arena Score Over Time</h2>
  <div id="chart-score" class="chart"></div>
</div>

<div class="grid">
  <div class="chart-container">
    <h2>Rank Over Time</h2>
    <div id="chart-rank" class="chart"></div>
  </div>
  <div class="chart-container">
    <h2>Overtake Probability vs #1</h2>
    <div id="chart-overtake" class="chart"></div>
  </div>
</div>

<div class="grid">
  <div class="chart-container">
    <h2>Vote Accumulation</h2>
    <div id="chart-votes" class="chart"></div>
  </div>
  <div class="chart-container">
    <h2>Confidence Interval (CI) Over Time</h2>
    <div id="chart-ci" class="chart"></div>
  </div>
</div>

<div class="chart-container">
  <h2>Head-to-Head Win Rate vs #1</h2>
  <div id="chart-h2h" class="chart"></div>
</div>

<div class="note">Data source: data/timeseries/top20.jsonl &mdash; auto-regenerated on each leaderboard check</div>

<script>
const DATA = %(chart_data_json)s;

const LAYOUT_DEFAULTS = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(22,33,62,0.5)',
  font: { color: '#e0e0e0', size: 11 },
  margin: { l: 50, r: 20, t: 10, b: 40 },
  legend: { orientation: 'h', y: -0.2, font: { size: 10 } },
  xaxis: { gridcolor: '#2a2a4a', type: 'date' },
  yaxis: { gridcolor: '#2a2a4a' },
  hovermode: 'x unified',
};

const CONFIG = { responsive: true, displayModeBar: false };

// Distinct colors for model traces.
const COLORS = [
  '#e94560', '#0f3460', '#53d8fb', '#f0a500', '#48c774',
  '#ff6b6b', '#4ecdc4', '#ffe66d', '#a55eea', '#fd9644',
  '#2bcbba', '#fc5c65', '#778beb', '#f19066', '#cf6a87',
  '#786fa6', '#63cdda', '#ea8685', '#596275', '#f3a683',
];

function getColor(i) { return COLORS[i %% COLORS.length]; }

// Filter to models that have at least one non-null value for a given field.
function modelsWithData(field) {
  const names = Object.keys(DATA.models);
  return names.filter(name => {
    if (field === 'overtake') return DATA.overtake[name] && DATA.overtake[name].some(v => v != null);
    if (field === 'h2h') return DATA.h2h[name] && DATA.h2h[name].some(v => v != null);
    return DATA.models[name] && DATA.models[name].some(v => v != null);
  });
}

function extractField(modelName, field) {
  return DATA.models[modelName].map(d => d ? d[field] : null);
}

// --- Score chart ---
(function() {
  const names = modelsWithData('score');
  const traces = names.map((name, i) => ({
    x: DATA.timestamps,
    y: extractField(name, 'score'),
    name: name,
    type: 'scatter',
    mode: 'lines+markers',
    line: { color: getColor(i), width: 2 },
    marker: { size: 4 },
    connectgaps: false,
  }));
  Plotly.newPlot('chart-score', traces, {
    ...LAYOUT_DEFAULTS,
    yaxis: { ...LAYOUT_DEFAULTS.yaxis, title: 'Arena Score' },
  }, CONFIG);
})();

// --- Rank chart (inverted y-axis) ---
(function() {
  const names = modelsWithData('rank');
  const traces = names.map((name, i) => ({
    x: DATA.timestamps,
    y: extractField(name, 'rank'),
    name: name,
    type: 'scatter',
    mode: 'lines+markers',
    line: { color: getColor(i), width: 2 },
    marker: { size: 4 },
    connectgaps: false,
  }));
  Plotly.newPlot('chart-rank', traces, {
    ...LAYOUT_DEFAULTS,
    yaxis: { ...LAYOUT_DEFAULTS.yaxis, title: 'Rank', autorange: 'reversed', dtick: 1 },
  }, CONFIG);
})();

// --- Overtake probability chart ---
(function() {
  const names = modelsWithData('overtake');
  const traces = names.map((name, i) => ({
    x: DATA.timestamps,
    y: DATA.overtake[name].map(v => v != null ? v * 100 : null),
    name: name,
    type: 'scatter',
    mode: 'lines+markers',
    line: { color: getColor(i), width: 2 },
    marker: { size: 4 },
    connectgaps: false,
  }));
  Plotly.newPlot('chart-overtake', traces, {
    ...LAYOUT_DEFAULTS,
    yaxis: { ...LAYOUT_DEFAULTS.yaxis, title: 'Overtake %%', rangemode: 'tozero' },
  }, CONFIG);
})();

// --- Votes chart ---
(function() {
  const names = modelsWithData('votes');
  const traces = names.map((name, i) => ({
    x: DATA.timestamps,
    y: extractField(name, 'votes'),
    name: name,
    type: 'scatter',
    mode: 'lines+markers',
    line: { color: getColor(i), width: 2 },
    marker: { size: 4 },
    connectgaps: false,
  }));
  Plotly.newPlot('chart-votes', traces, {
    ...LAYOUT_DEFAULTS,
    yaxis: { ...LAYOUT_DEFAULTS.yaxis, title: 'Total Votes' },
  }, CONFIG);
})();

// --- CI chart ---
(function() {
  const names = modelsWithData('ci');
  const traces = names.map((name, i) => ({
    x: DATA.timestamps,
    y: extractField(name, 'ci'),
    name: name,
    type: 'scatter',
    mode: 'lines+markers',
    line: { color: getColor(i), width: 2 },
    marker: { size: 4 },
    connectgaps: false,
  }));
  Plotly.newPlot('chart-ci', traces, {
    ...LAYOUT_DEFAULTS,
    yaxis: { ...LAYOUT_DEFAULTS.yaxis, title: 'CI (±points)' },
  }, CONFIG);
})();

// --- H2H win rate chart ---
(function() {
  const names = modelsWithData('h2h');
  if (names.length === 0) {
    document.getElementById('chart-h2h').innerHTML =
      '<p style="text-align:center;color:#888;padding:40px;">No H2H data yet — will appear after next leaderboard update.</p>';
    return;
  }
  const traces = names.map((name, i) => ({
    x: DATA.timestamps,
    y: DATA.h2h[name].map(v => v != null ? v * 100 : null),
    name: name,
    type: 'scatter',
    mode: 'lines+markers',
    line: { color: getColor(i), width: 2 },
    marker: { size: 4 },
    connectgaps: false,
  }));
  // Add 50%% reference line.
  traces.push({
    x: [DATA.timestamps[0], DATA.timestamps[DATA.timestamps.length - 1]],
    y: [50, 50],
    name: '50%% line',
    type: 'scatter',
    mode: 'lines',
    line: { color: '#666', width: 1, dash: 'dash' },
    showlegend: false,
  });
  Plotly.newPlot('chart-h2h', traces, {
    ...LAYOUT_DEFAULTS,
    yaxis: { ...LAYOUT_DEFAULTS.yaxis, title: 'Win Rate vs #1 %%' },
  }, CONFIG);
})();
</script>
</body>
</html>
"""


def generate_dashboard(
    timeseries_dir: str | Path = DEFAULT_TIMESERIES_DIR,
    output_path: str | Path = DEFAULT_OUTPUT,
    top_n: int = DEFAULT_TOP_N,
) -> Path:
    """Generate the HTML dashboard file.

    Args:
        timeseries_dir: Directory containing the JSONL timeseries file.
        output_path: Where to write the HTML output.
        top_n: Number of top models to include in charts.

    Returns:
        The path to the generated HTML file.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    timeseries = load_timeseries(timeseries_dir)
    chart_data = extract_chart_data(timeseries, top_n=top_n)

    ct_now = datetime.now(ZoneInfo("America/Chicago"))
    generated_at = ct_now.strftime("%Y-%m-%d %H:%M:%S %Z")

    html_content = _HTML_TEMPLATE % {
        "generated_at": html.escape(generated_at),
        "num_records": len(timeseries),
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
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP_N,
        help=f"Number of top models to chart (default: {DEFAULT_TOP_N})",
    )
    args = parser.parse_args()

    output = generate_dashboard(
        timeseries_dir=args.timeseries_dir,
        output_path=args.output,
        top_n=args.top,
    )
    print(f"Dashboard generated: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
