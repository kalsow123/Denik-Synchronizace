"""Ověření two-sided: bear→bull i bull→bear na reálných datech."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from config.enums import EntryMode, TPMode
from backtest.engine import BacktestEngine
from strategy.wave_detection import detect_waves
from strategy.two_sided import (
    TwoSidedTracker,
    find_parent_wave_for_two_sided,
    parent_wave_qualifies,
    retracement_fib_price,
    should_open_two_sided_counter,
    two_sided_enabled,
)
from strategy.trend_bos import compute_trend_states_per_wave
from strategy.wave_detection_pine import compute_wave_birth_bars_pine


def main() -> None:
    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        two_sided_entry_enabled=True,
        two_sided_entry_min_wave_pct=0.55,
        entry_mode=EntryMode.MARKET_FALLBACK,
        tp_mode=TPMode.BOS_EXIT,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2026-03-01") & (df["time"] <= "2026-05-10")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    birth = compute_wave_birth_bars_pine(df, cfg)
    # Trend state snapshot pro kazdou vlnu — vyzaduje ho novy trend filter
    # uvnitr two-sided (parent musi byt v trend-direction, counter counter-trend).
    trend_states = (
        compute_trend_states_per_wave(df, waves, cfg)
        if two_sided_enabled(cfg)
        else {}
    )
    tracker = TwoSidedTracker()
    for w in waves:
        b = birth.get(str(w["wave_time"]))
        ts = trend_states.get(str(w["wave_time"]))
        if b is not None and parent_wave_qualifies(w, cfg, trend_state=ts):
            tracker.register_parent(w, int(b), cfg, trend_state=ts)
    for i in range(len(df)):
        tracker.update_bar(
            float(df.iloc[i]["high"]), float(df.iloc[i]["low"]), i
        )

    bear_to_bull = 0
    bull_to_bear = 0
    examples: list[str] = []

    for w in waves:
        parent = find_parent_wave_for_two_sided(
            waves, w, cfg, trend_states_per_wave=trend_states
        )
        if parent is None:
            continue
        pwt = str(parent["wave_time"])
        touched = tracker.fib_was_touched(pwt)
        if not should_open_two_sided_counter(
            parent, w, cfg,
            parent_fib_touched=touched,
            parent_trend_state=trend_states.get(pwt),
            counter_trend_state=trend_states.get(str(w["wave_time"])),
        ):
            continue
        pdir = int(parent["dir"])
        cdir = int(w["dir"])
        t = df.iloc[int(w["draw_left"])]["time"]
        if pdir == -1 and cdir == 1:
            bear_to_bull += 1
            if len([e for e in examples if "B2U" in e]) < 3:
                examples.append(
                    f"B2U {t} parent {pwt} {float(parent['move_pct']):.2f}% "
                    f"-> {w['wave_time']} {float(w['move_pct']):.2f}% fib_touch={touched}"
                )
        elif pdir == 1 and cdir == -1:
            bull_to_bear += 1
            if len([e for e in examples if "U2D" in e]) < 3:
                examples.append(
                    f"U2D {t} parent {pwt} {float(parent['move_pct']):.2f}% "
                    f"-> {w['wave_time']} {float(w['move_pct']):.2f}% fib_touch={touched}"
                )

    print("=== Kandidáti two-sided (logika) ===")
    print("bear -> bull (SHORT parent, LONG counter):", bear_to_bull)
    print("bull -> bear (LONG parent, SHORT counter):", bull_to_bear)
    for line in examples:
        print(" ", line)

    eng = BacktestEngine(cfg)
    eng.run(df)
    ts_b2u = [
        t
        for t in eng.closed_trades
        if getattr(t, "is_two_sided_mirror", False) and int(t.dir) == 1
    ]
    ts_u2d = [
        t
        for t in eng.closed_trades
        if getattr(t, "is_two_sided_mirror", False) and int(t.dir) == -1
    ]
    print("\n=== Provedené obchody (engine) ===")
    print("two-sided LONG (po bear):", len(ts_b2u))
    print("two-sided SHORT (po bull):", len(ts_u2d))
    for t in ts_b2u[:2]:
        print("  LONG", t.entry_time, t.wave_time, t.entry_type)
    for t in ts_u2d[:2]:
        print("  SHORT", t.entry_time, t.wave_time, t.entry_type)


if __name__ == "__main__":
    main()
