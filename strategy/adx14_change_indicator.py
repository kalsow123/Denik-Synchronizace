"""
ADX14 change indicator for the EURUSD bot.

Purpose
-------
This module computes the same ADX14-change signal that was shown in the
Perplexity diagnostic HTML:

1. Build daily OHLC from raw bars.
2. Compute ADX14 using the same rolling-sum / rolling-mean method.
3. Compute ADX14 % change against the previous 30 daily ADX14 median.
4. Convert that % change into the visual "normalized signal" used in the HTML.

Important
---------
The HTML visual used a normalizer (median/IQR) over the displayed history.
For live use, do NOT refit the normalizer every day, otherwise the 1.3 threshold
will drift. Fit once from the same backtest history used for the HTML and save
the JSON profile. Live then uses that frozen profile.

No third-party packages are required.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Iterable, Optional
import csv
import html
import json
import math


@dataclass
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class ADX14Point:
    day: str
    adx14: Optional[float]
    adx14_change_pct: Optional[float]
    adx14_signal: Optional[float]


@dataclass
class ADX14Normalizer:
    change_median: float
    change_iqr: float
    source: str = "fit_from_backtest_history"

    def normalize(self, change_pct: Optional[float]) -> Optional[float]:
        if change_pct is None or math.isnan(change_pct):
            return None
        scale = self.change_iqr if abs(self.change_iqr) > 1e-12 else 1.0
        value = (change_pct - self.change_median) / scale
        return max(-4.0, min(4.0, value))


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).replace("T", " ").split(".")[0]
    return datetime.fromisoformat(text)


def load_bars_from_csv(path: str | Path) -> list[dict]:
    """Load bars from CSV with columns datetime,open,high,low,close,volume.

    Works with comma decimal and semicolon decimal formats.
    """
    path = Path(path)
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:2048]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f, dialect=dialect)
        for r in reader:
            def num(x, default=0.0):
                if x is None or x == "":
                    return default
                return float(str(x).replace(",", "."))

            rows.append(
                {
                    "datetime": r.get("datetime") or r.get("time") or r.get("date"),
                    "open": num(r.get("open")),
                    "high": num(r.get("high")),
                    "low": num(r.get("low")),
                    "close": num(r.get("close")),
                    "volume": num(r.get("volume"), 0.0),
                }
            )
    return rows


def resample_to_daily(bars: Iterable[dict]) -> list[DailyBar]:
    by_day: dict[date, list[dict]] = {}
    for bar in bars:
        dt = _to_datetime(bar["datetime"])
        by_day.setdefault(dt.date(), []).append(bar)

    daily: list[DailyBar] = []
    for d in sorted(by_day):
        rows = sorted(by_day[d], key=lambda x: _to_datetime(x["datetime"]))
        daily.append(
            DailyBar(
                day=d,
                open=float(rows[0]["open"]),
                high=max(float(r["high"]) for r in rows),
                low=min(float(r["low"]) for r in rows),
                close=float(rows[-1]["close"]),
                volume=sum(float(r.get("volume", 0.0) or 0.0) for r in rows),
            )
        )
    return daily


def _rolling_sum(values: list[Optional[float]], idx: int, window: int) -> Optional[float]:
    if idx + 1 < window:
        return None
    chunk = values[idx - window + 1 : idx + 1]
    if any(v is None or math.isnan(v) for v in chunk):
        return None
    return float(sum(chunk))  # type: ignore[arg-type]


def _rolling_mean(values: list[Optional[float]], idx: int, window: int) -> Optional[float]:
    s = _rolling_sum(values, idx, window)
    return None if s is None else s / window


def _rolling_median_previous(values: list[Optional[float]], idx: int, window: int) -> Optional[float]:
    start = max(0, idx - window)
    chunk = [v for v in values[start:idx] if v is not None and not math.isnan(v)]
    if len(chunk) < 10:
        return None
    return float(median(chunk))


def compute_adx14_points(
    bars: Iterable[dict],
    normalizer: Optional[ADX14Normalizer] = None,
) -> list[ADX14Point]:
    daily = resample_to_daily(bars)
    if not daily:
        return []

    true_range: list[Optional[float]] = []
    plus_dm: list[Optional[float]] = []
    minus_dm: list[Optional[float]] = []

    for i, b in enumerate(daily):
        if i == 0:
            prev_close = b.close
            true_range.append(b.high - b.low)
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            continue

        prev = daily[i - 1]
        tr = max(abs(b.high - b.low), abs(b.high - prev.close), abs(b.low - prev.close))
        upmove = b.high - prev.high
        downmove = prev.low - b.low
        pdm = upmove if (upmove > downmove and upmove > 0) else 0.0
        mdm = downmove if (downmove > upmove and downmove > 0) else 0.0
        true_range.append(tr)
        plus_dm.append(pdm)
        minus_dm.append(mdm)

    dx: list[Optional[float]] = []
    for i in range(len(daily)):
        atr_sum = _rolling_sum(true_range, i, 14)
        plus_sum = _rolling_sum(plus_dm, i, 14)
        minus_sum = _rolling_sum(minus_dm, i, 14)
        if not atr_sum or plus_sum is None or minus_sum is None:
            dx.append(None)
            continue
        plus_di = 100.0 * plus_sum / atr_sum
        minus_di = 100.0 * minus_sum / atr_sum
        denom = plus_di + minus_di
        dx.append(None if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom)

    adx14: list[Optional[float]] = [_rolling_mean(dx, i, 14) for i in range(len(daily))]
    changes: list[Optional[float]] = []
    for i, value in enumerate(adx14):
        base = _rolling_median_previous(adx14, i, 30)
        if value is None or base is None or base == 0:
            changes.append(None)
        else:
            changes.append((value / base - 1.0) * 100.0)

    if normalizer is None:
        normalizer = fit_normalizer_from_changes(changes)

    points: list[ADX14Point] = []
    for b, a, c in zip(daily, adx14, changes):
        points.append(
            ADX14Point(
                day=b.day.isoformat(),
                adx14=None if a is None else round(a, 6),
                adx14_change_pct=None if c is None else round(c, 6),
                adx14_signal=None if c is None else round(normalizer.normalize(c), 6),
            )
        )
    return points


def fit_normalizer_from_changes(changes: Iterable[Optional[float]]) -> ADX14Normalizer:
    vals = sorted(float(v) for v in changes if v is not None and not math.isnan(v))
    if not vals:
        return ADX14Normalizer(0.0, 1.0, "empty_default")
    q1 = vals[int((len(vals) - 1) * 0.25)]
    q3 = vals[int((len(vals) - 1) * 0.75)]
    iqr = q3 - q1
    return ADX14Normalizer(float(median(vals)), float(iqr if abs(iqr) > 1e-12 else 1.0))


def fit_normalizer_from_csv(csv_path: str | Path, output_json: str | Path) -> ADX14Normalizer:
    bars = load_bars_from_csv(csv_path)
    # First pass without saved normalizer fits from the whole history, matching the HTML style.
    points = compute_adx14_points(bars, normalizer=None)
    normalizer = fit_normalizer_from_changes([p.adx14_change_pct for p in points])
    save_normalizer(normalizer, output_json)
    return normalizer


def save_normalizer(normalizer: ADX14Normalizer, path: str | Path) -> None:
    Path(path).write_text(json.dumps(asdict(normalizer), indent=2), encoding="utf-8")


def load_normalizer(path: str | Path) -> ADX14Normalizer:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ADX14Normalizer(**data)


def latest_signal_from_csv(csv_path: str | Path, normalizer_json: str | Path) -> Optional[ADX14Point]:
    normalizer = load_normalizer(normalizer_json)
    points = compute_adx14_points(load_bars_from_csv(csv_path), normalizer=normalizer)
    valid = [p for p in points if p.adx14_signal is not None]
    return valid[-1] if valid else None


def write_adx14_html(
    points: list[ADX14Point],
    output_html: str | Path,
    title: str = "ADX14 změna",
    threshold: float = 1.3,
) -> None:
    x = [p.day for p in points if p.adx14_signal is not None]
    y = [p.adx14_signal for p in points if p.adx14_signal is not None]
    raw = [p.adx14 for p in points if p.adx14_signal is not None]
    change = [p.adx14_change_pct for p in points if p.adx14_signal is not None]
    payload = {
        "x": x,
        "y": y,
        "raw": raw,
        "change": change,
        "title": title,
    }
    thr = float(threshold)
    html_text = f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="utf-8"/>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <title>{html.escape(title)}</title>
</head>
<body style="font-family:Arial,sans-serif;margin:24px;">
  <h2>{html.escape(title)}</h2>
  <div id="chart" style="width:100%;height:620px;"></div>
  <script>
    const p = {json.dumps(payload)};
    const trace = {{
      name: "ADX14 změna",
      x: p.x,
      y: p.y,
      customdata: p.raw.map((v, i) => [v, p.change[i]]),
      mode: "lines",
      type: "scatter",
      line: {{color:"#437A22", width:2}},
      hovertemplate: "%{{x}}<br>ADX14 signal: %{{y:.2f}}<br>ADX14: %{{customdata[0]:.2f}}<br>změna: %{{customdata[1]:.1f}}%<extra></extra>"
    }};
    const thresholdLine = {{
      type: "scatter", mode: "lines", name: "vypnutí threshold {thr}",
      x: p.x, y: p.x.map(() => {thr}),
      line: {{color:"#A13544", width:2, dash:"dash"}}
    }};
    Plotly.newPlot("chart", [trace, thresholdLine], {{
      title: p.title,
      yaxis: {{title: "normalizovaný ADX14 signál", zeroline:true}},
      xaxis: {{title: "datum"}},
      hovermode: "x unified"
    }}, {{responsive:true, scrollZoom:true, displaylogo:false}});
  </script>
</body>
</html>"""
    Path(output_html).write_text(html_text, encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Compute ADX14 change signal.")
    parser.add_argument("--csv", required=True, help="CSV with datetime,open,high,low,close,volume")
    parser.add_argument("--normalizer-json", required=True, help="Saved/frozen normalizer JSON")
    parser.add_argument("--fit", action="store_true", help="Fit and overwrite normalizer from CSV history")
    parser.add_argument("--html", help="Optional output HTML path")
    args = parser.parse_args()

    if args.fit or not Path(args.normalizer_json).exists():
        normalizer = fit_normalizer_from_csv(args.csv, args.normalizer_json)
    else:
        normalizer = load_normalizer(args.normalizer_json)

    points = compute_adx14_points(load_bars_from_csv(args.csv), normalizer=normalizer)
    latest = [p for p in points if p.adx14_signal is not None][-1]
    print(json.dumps(asdict(latest), ensure_ascii=False))
    if args.html:
        write_adx14_html(points, args.html)
