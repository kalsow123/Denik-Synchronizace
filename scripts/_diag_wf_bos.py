"""Diagnose WF + BOS wave attribution for visual HTML scenario."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.engine import BacktestEngine
from config.bot_config import LIVE_BOT_CONFIG
from strategy.trend_bos import compute_bos_wave_flip_map, compute_bos_wave_times
from strategy.wave_detection import detect_waves
from strategy.wave_detection_pine import compute_wave_birth_bars_pine
from strategy.wave_sequence import compute_wave_sequence_info_per_wave
from strategy.wick_fakeout import WAVE_ORIGIN_WF


def cfg_wave_target_n():
    return LIVE_BOT_CONFIG


def load_df() -> pd.DataFrame:
    df = pd.read_csv(ROOT / "data" / "EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    return df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)


def dump_waves(waves, birth, seq, bos_times, label: str, t_from: str, t_to: str) -> None:
    print(f"\n=== {label} {t_from} .. {t_to} ===")
    t0 = pd.Timestamp(t_from)
    t1 = pd.Timestamp(t_to)
    for w in sorted(waves, key=lambda x: birth.get(str(x["wave_time"]), 0)):
        wt = str(w["wave_time"])
        b = birth.get(wt)
        if b is None:
            continue
        t = pd.Timestamp(w.get("wave_time_dt") or wt)
        if hasattr(w.get("wave_time_dt"), "to_pydatetime"):
            pass
        try:
            tdt = pd.to_datetime(wt, format="%Y%m%d%H%M", errors="coerce")
        except Exception:
            tdt = pd.NaT
        if pd.isna(tdt):
            continue
        if not (t0 <= tdt <= t1):
            continue
        info = seq.get(wt)
        idx = getattr(info, "index_in_trend", None) if info else None
        origin = w.get("wave_origin", "normal")
        flags = []
        if origin == WAVE_ORIGIN_WF or w.get("wf_wave_position"):
            flags.append("WF")
        if w.get("wf_continued_classic"):
            flags.append("wf_classic")
        if wt in bos_times or w.get("is_bos_wave"):
            flags.append("BOS_WAVE")
        if w.get("hh_hl_pass") is False:
            flags.append("hh_hl_fail")
        dr = w.get("draw_right")
        dl = w.get("draw_left")
        print(
            f"  {wt} dir={w.get('dir')} birth={b} dr={dr} dl={dl} "
            f"idx={idx} origin={origin} flags={','.join(flags) or '-'}"
        )


def parse_html_wf_regions(html_path: Path) -> None:
    s = html_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'Plotly\.newPlot\(\s*"[^"]+",\s*(\[)', s)
    start = m.start(1)
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "[":
            depth += 1
        elif s[i] == "]":
            depth -= 1
            if depth == 0:
                traces = json.loads(s[start : i + 1])
                break
    else:
        return
    print("\n=== HTML WF markers + nearby index labels ===")
    wf_x = []
    for tr in traces:
        ht = tr.get("hovertemplate") or ""
        if "WF continuation" in ht and tr.get("x"):
            wf_x.extend(tr["x"])
    for wx in sorted(set(wf_x)):
        print(f"WF at {wx}")
        # find index labels near this time
        for tr in traces:
            text = tr.get("text")
            if not text or not isinstance(text, list):
                continue
            xs = tr.get("x") or []
            for txt, x in zip(text, xs):
                if not re.match(r"^\d+$", str(txt)):
                    continue
                if abs((pd.Timestamp(x) - pd.Timestamp(wx)).total_seconds()) < 7 * 86400:
                    print(f"  label {txt} at {x}")


def main() -> None:
    html = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if html and html.exists():
        parse_html_wf_regions(html)

    cfg = cfg_wave_target_n()
    df = load_df()
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    all_waves = eng.last_waves
    birth = eng.wave_birth_by_time
    seq = compute_wave_sequence_info_per_wave(df, all_waves, cfg)
    bos_map = compute_bos_wave_flip_map(df, all_waves, cfg, wave_birth_bars=birth)
    bos_times = set(bos_map.values()) | set(compute_bos_wave_times(df, all_waves, cfg, wave_birth_bars=birth))

    print(f"\nEngine wf_activations={eng.wave_debug.get('wf_activations')}")
    print(f"BOS flip map ({len(bos_map)} flips):")
    times = df["time"]
    for bar, wt in sorted(bos_map.items()):
        print(f"  bar {bar} {times.iloc[bar]} -> bos_wave {wt}")

    # Focus windows around each WF from engine
    for w in all_waves:
        if str(w.get("wave_origin", "")) != WAVE_ORIGIN_WF and not w.get("wf_wave_position"):
            continue
        wt = str(w["wave_time"])
        b = birth.get(wt, 0)
        t = pd.to_datetime(wt, format="%Y%m%d%H%M")
        dump_waves(
            all_waves,
            birth,
            seq,
            bos_times,
            "WF window",
            str(t - pd.Timedelta(days=5)),
            str(t + pd.Timedelta(days=10)),
        )
        info = seq.get(wt)
        print(f"  >> WF wave {wt} idx={getattr(info, 'index_in_trend', None)} is_bos={w.get('is_bos_wave')} in_bos_map={wt in bos_times}")


if __name__ == "__main__":
    main()
