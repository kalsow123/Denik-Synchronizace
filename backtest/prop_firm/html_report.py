"""HTML report prop_firm_compliance.html."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pandas as pd


def _pick_existing_column(df: pd.DataFrame, *names: str) -> str:
    for name in names:
        if name in df.columns:
            return name
    raise KeyError(f"Missing expected prop-firm column, tried: {names}")


def write_prop_firm_html(
    df_long: pd.DataFrame,
    out_path: Path,
    preset_names: List[str],
) -> None:
    if df_long.empty:
        return

    bots = sorted(df_long["bot_name"].unique())
    presets = [p for p in preset_names if p in df_long["prop_firm_name"].unique()]

    matrix_rows = []
    for bot in bots:
        cells = []
        for preset in presets:
            mask = (df_long["bot_name"] == bot) & (df_long["prop_firm_name"] == preset)
            if not mask.any():
                cells.append("<td>—</td>")
                continue
            ok = bool(df_long.loc[mask, "challenge_passed"].iloc[0])
            sym = "✓" if ok else "✗"
            color = "#2e7d32" if ok else "#c62828"
            cells.append(f'<td style="text-align:center;color:{color};font-weight:bold">{sym}</td>')
        matrix_rows.append(f"<tr><td>{bot}</td>{''.join(cells)}</tr>")

    top_sections = []
    pnl_pct_col = _pick_existing_column(df_long, "scaled_net_pnl_acc_pct", "scaled_net_pnl_pct")
    dd_pct_col = _pick_existing_column(
        df_long, "scaled_max_dd_pct_vs_initial", "scaled_max_dd_pct"
    )
    for preset in presets:
        sub = df_long[df_long["prop_firm_name"] == preset].sort_values(
            pnl_pct_col, ascending=False
        ).head(10)
        rows = "".join(
            f"<tr><td>{r['bot_name']}</td><td>{r['scale_factor']:.3f}</td>"
            f"<td>{r[pnl_pct_col]:.2f}</td><td>{r[dd_pct_col]:.2f}</td>"
            f"<td>{r['binding_constraint']}</td>"
            f"<td>{'✓' if r['challenge_passed'] else '✗'}</td></tr>"
            for _, r in sub.iterrows()
        )
        top_sections.append(
            f"<h3>TOP 10 — {preset}</h3>"
            "<table><tr><th>bot</th><th>scale</th><th>scaled PnL %</th>"
            "<th>scaled DD %</th><th>binding</th><th>passed</th></tr>"
            f"{rows}</table>"
        )

    long_json = json.dumps(df_long.to_dict(orient="records"), default=str)
    bot_options = "".join(f'<option value="{b}">{b}</option>' for b in bots)
    preset_headers = "".join(f"<th>{p}</th>" for p in presets)

    html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8"/>
<title>Prop Firm Compliance</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; }}
table {{ border-collapse: collapse; margin: 12px 0 24px; width: 100%; max-width: 1200px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; font-size: 13px; }}
th {{ background: #f5f5f5; }}
.sim {{ max-width: 520px; padding: 16px; background: #fafafa; border: 1px solid #ddd; }}
label {{ display: block; margin: 8px 0 4px; }}
input[type=range] {{ width: 100%; }}
#simOut {{ margin-top: 12px; font-family: monospace; white-space: pre-wrap; }}
</style>
</head>
<body>
<h1>Prop Firm Compliance</h1>
<h2>1. Matrix — challenge passed</h2>
<table>
<tr><th>bot_name</th>{preset_headers}</tr>
{"".join(matrix_rows)}
</table>
<h2>2. TOP 10 podle scaled PnL %</h2>
{"".join(top_sections)}
<h2>3. Simulátor (custom limity)</h2>
<div class="sim">
<label>Účet (USD) <span id="vAcct"></span>
<input type="range" id="acct" min="25000" max="200000" step="5000" value="100000"/></label>
<label>Max risk / moment % <span id="vRisk"></span>
<input type="range" id="riskPct" min="0.5" max="5" step="0.1" value="3"/></label>
<label>Max daily DD % <span id="vDaily"></span>
<input type="range" id="dailyPct" min="1" max="10" step="0.5" value="5"/></label>
<label>Max overall DD % <span id="vOverall"></span>
<input type="range" id="overallPct" min="2" max="15" step="0.5" value="10"/></label>
<p>Kombinace: <select id="botSel">{bot_options}</select></p>
<div id="simOut"></div>
</div>
<script>
const fullLong = {long_json};
function recalc() {{
  const bot = document.getElementById('botSel').value;
  const recs = fullLong.filter(r => r.bot_name === bot);
  if (!recs.length) {{
    document.getElementById('simOut').textContent = 'Žádná data';
    return;
  }}
  const acct = +document.getElementById('acct').value;
  const maxRiskUsd = acct * (+document.getElementById('riskPct').value) / 100;
  const maxDaily = +document.getElementById('dailyPct').value;
  const maxOverall = +document.getElementById('overallPct').value;
  document.getElementById('vAcct').textContent = acct;
  document.getElementById('vRisk').textContent = document.getElementById('riskPct').value;
  document.getElementById('vDaily').textContent = maxDaily;
  document.getElementById('vOverall').textContent = maxOverall;
  const lines = recs.map(r => {{
    const peakRiskUsd = (r.peak_risk_pct / 100) * acct;
    const sRisk = peakRiskUsd > 0 ? Math.min(1, maxRiskUsd / peakRiskUsd) : 1;
    const sDaily = Math.abs(r.worst_day_loss_pct) > 0
      ? Math.min(1, maxDaily / Math.abs(r.worst_day_loss_pct)) : 1;
    const sOverall = Math.abs(r.peak_overall_dd_pct) > 0
      ? Math.min(1, maxOverall / Math.abs(r.peak_overall_dd_pct)) : 1;
    const scale = Math.min(1, sRisk, sDaily, sOverall);
    const scaledPnl = r.original_net_pnl_usd * scale;
    return r.prop_firm_name + ': scale=' + scale.toFixed(3)
      + ' scaled_pnl=' + scaledPnl.toFixed(2) + ' USD ('
      + (scaledPnl / acct * 100).toFixed(2) + '%)';
  }});
  document.getElementById('simOut').textContent = lines.join('\\n');
}}
['acct','riskPct','dailyPct','overallPct','botSel'].forEach(id =>
  document.getElementById(id).addEventListener('input', recalc));
recalc();
</script>
</body>
</html>"""
    Path(out_path).write_text(html, encoding="utf-8")
