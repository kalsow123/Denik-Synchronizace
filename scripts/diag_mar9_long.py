"""Diagnostika: proc nevznikl LONG po EXT 9.3.2026."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from config.enums import EntryMode, TPMode
from strategy.wave_detection import detect_waves
from strategy.wave_detection_pine import compute_wave_birth_bars_pine
from strategy.trend_bos import compute_trend_states_per_wave, wave_allowed_for_entry
from strategy.ext_logic import (
    is_ext_wave,
    apply_first_opposite_wave_sl_after_ext,
    sl_at_ext_extreme_for_opposite_wave,
)


def main() -> None:
    cfg = BotConfig(
        bot_name="DIAG",
        symbol="EURUSD",
        timeframe=30,
        wave_min_pct=0.26,
        min_opp_bars=3,
        rrr=2.0,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        entry_mode=EntryMode.MARKET_FALLBACK,
        abort_fib_level="shift_sl",
        wave_plus=True,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
        tp_mode=TPMode.BOS_EXIT,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        ext_trade_both_sides_in_range=True,
        ext_range_wave_min_pct=0.13,
        two_sided_entry_enabled=False,
    )
    spread_half = 0.0001 / 2

    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-05") & (df["time"] <= "2026-03-12")].reset_index(
        drop=True
    )

    waves = detect_waves(df, cfg)
    birth = compute_wave_birth_bars_pine(df, cfg)
    ts_map = compute_trend_states_per_wave(df, waves, cfg)

    print("=== VLNY 5.-12.3.2026 (M30, EXAMPLE) ===")
    ext_anchor = None
    for w in waves:
        wt = w["wave_time"]
        bi = birth.get(wt)
        tbar = df["time"].iloc[bi] if bi is not None else None
        ext = is_ext_wave(w, cfg)
        ts = ts_map.get(wt)
        trend = getattr(ts, "direction", None) if ts else None
        allowed, reason = wave_allowed_for_entry(w, ts, cfg)
        sl_note = ""
        w_eval = w
        if ext:
            ext_anchor = w
        elif ext_anchor is not None and int(w["dir"]) == -int(ext_anchor["dir"]):
            sl_ext = sl_at_ext_extreme_for_opposite_wave(w, ext_anchor)
            w_eval, ext_anchor = apply_first_opposite_wave_sl_after_ext(
                w, ext_anchor=ext_anchor, cfg=cfg
            )
            sl_note = (
                f" | SL_ext={sl_ext} applied={w_eval['sl']:.5f}"
                if sl_ext is not None
                else " | SL_ext=INVALID"
            )
        print(
            f"{tbar} | {wt} | dir={int(w['dir']):+d} | move={float(w['move_pct']):.2f}% "
            f"| ext={ext} | trend={trend} | entry={allowed} ({reason}) "
            f"| fib50={float(w['fib50']):.5f} sl={float(w_eval['sl']):.5f}{sl_note}"
        )

    print("\n=== UP vlny 9.3. — bar narozeni ===")
    for w in waves:
        wt = str(w["wave_time"])
        if not wt.startswith("20260309") or int(w["dir"]) != 1:
            continue
        bi = birth[wt]
        bar = df.iloc[bi]
        ask = float(bar["close"]) + spread_half
        bid = float(bar["close"]) - spread_half
        ep = float(w["fib50"])
        sl_std = float(w["sl"])
        ext_anchor_local = None
        for pw in waves:
            if pw["wave_time"] == wt:
                break
            if is_ext_wave(pw, cfg):
                ext_anchor_local = pw
        sl_eff = sl_std
        if ext_anchor_local and int(w["dir"]) == -int(ext_anchor_local["dir"]):
            se = sl_at_ext_extreme_for_opposite_wave(w, ext_anchor_local)
            if se is not None:
                sl_eff = se
        print(
            f"\n{wt} birth={bar['time']} O={bar['open']:.5f} H={bar['high']:.5f} "
            f"L={bar['low']:.5f} C={bar['close']:.5f}"
        )
        print(f"  fib50={ep:.5f} sl_std={sl_std:.5f} sl_eff={sl_eff:.5f}")
        print(f"  ask={ask:.5f} ask>ep (LIMIT)={ask > ep} ask<=sl_eff (skip)={ask <= sl_eff}")
        fa = w.get("fib_abort")
        if fa is not None:
            print(f"  fib_abort={float(fa):.5f} past_abort={ask <= float(fa)}")
        ts = ts_map.get(wt)
        if ts:
            a, r = wave_allowed_for_entry(w, ts, cfg)
            print(f"  trend={ts.direction} allowed={a} reason={r}")


if __name__ == "__main__":
    main()
