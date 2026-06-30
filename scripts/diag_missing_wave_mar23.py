"""Diagnostika chybejici vlny okolo 23. brezna 2026 12:00 (EURUSD M30)."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from config.enums import EntryMode, TPMode
from strategy.wave_detection_pine import (
    _apply_wave_plus_extend,
    _compute_after_data_gap_mask,
    _merge_waves_across_data_gaps,
    _remove_wick_invalidated_corrections,
    run_pine_wave_simulation,
)


def main() -> None:
    cfg = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        entry_mode=EntryMode.MARKET_FALLBACK,
        tp_mode=TPMode.WAVE_TARGET_N,
        tp_target_wave_index=4,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2026-03-20") & (df["time"] <= "2026-03-25")].reset_index(
        drop=True
    )

    print(f"=== Data window: {df['time'].iloc[0]} -> {df['time'].iloc[-1]} ({len(df)} bars) ===\n")

    # Najdi minimum/maximum kolem 23.3. 09:00-14:00
    win = df[(df["time"] >= "2026-03-23 09:00") & (df["time"] <= "2026-03-23 14:00")]
    print("=== OHLC 23.3. 09:00-14:00 ===")
    for _, row in win.iterrows():
        print(
            f"  {row['time']}  O={row['open']:.5f}  H={row['high']:.5f}  "
            f"L={row['low']:.5f}  C={row['close']:.5f}"
        )
    if not win.empty:
        lo_idx = win["low"].idxmin()
        print(
            f"\n  EXTREM LOW @ {df.loc[lo_idx, 'time']} = {df.loc[lo_idx, 'low']:.5f}"
        )
    print()

    # Krok 1: detekce pred mergem / plus / wick removal
    waves, birth, _, _ = run_pine_wave_simulation(df, cfg)
    print(f"=== Final waves (po wave_plus & wick removal): {len(waves)} ===")
    for i, w in enumerate(waves):
        dl, dr = int(w["draw_left"]), int(w["draw_right"])
        t_l = df["time"].iloc[dl] if dl < len(df) else "?"
        t_r = df["time"].iloc[dr] if dr < len(df) else "?"
        print(
            f"  [{i:2d}] dir={int(w['dir']):+d}  wave_time={w['wave_time']}  "
            f"left={t_l} ({dl}) -> right={t_r} ({dr})  "
            f"top={w['box_top']:.5f}  bot={w['box_bottom']:.5f}  "
            f"move={float(w['move_pct']):.3f}%  "
            f"is_ext={bool(w.get('is_ext', False))}"
        )
    print()

    # Krok 2: detekce BEZ post-process (jen syrove vlny z Pine emulatoru)
    # Spustime simulaci s docasne vypnutym wave_plus a wick removal, abychom
    # videli, jestli problem vznika v emulatoru nebo v post-procesech.
    cfg_raw = BotConfig(**{**cfg.__dict__})
    cfg_raw.wave_plus = False

    waves_raw, birth_raw, _, _ = run_pine_wave_simulation(df, cfg_raw)
    print(f"=== Raw Pine waves (wave_plus=False, vc. wick removal): {len(waves_raw)} ===")
    for i, w in enumerate(waves_raw):
        dl, dr = int(w["draw_left"]), int(w["draw_right"])
        t_l = df["time"].iloc[dl] if dl < len(df) else "?"
        t_r = df["time"].iloc[dr] if dr < len(df) else "?"
        print(
            f"  [{i:2d}] dir={int(w['dir']):+d}  wave_time={w['wave_time']}  "
            f"left={t_l} -> right={t_r}  "
            f"top={w['box_top']:.5f}  bot={w['box_bottom']:.5f}  "
            f"move={float(w['move_pct']):.3f}%"
        )
    print()

    # Krok 3: prochazime krok-po-kroku co se s vlnami stalo
    # Volame vnitrne pine simulation, ale bez post-procesu.
    from strategy.wave_detection_pine import (
        _apply_wave_plus_extend,
        _merge_waves_across_data_gaps,
        _remove_wick_invalidated_corrections,
    )

    # Spustime simulaci bez post-procesu (manualne)
    # Ulozime puvodni post-process funkce a zarazime je v poradi.

    # Skontaktujeme reload simulace bez post-procesu - run_pine_wave_simulation
    # je monoliticka. Misto toho zkusime jen samostatne post-procesy na cistych.

    after_gap = _compute_after_data_gap_mask(df["time"])
    print("=== After-gap bars in window ===")
    for i, flag in enumerate(after_gap):
        if flag:
            print(f"  [{i:3d}] {df['time'].iloc[i]}  after-gap")
    print()

    # Krok 4: trend state per wave + index v trendu (klice pro WAVE_TARGET_N)
    from strategy.trend_bos import (
        compute_trend_states_per_wave,
        wave_allowed_for_entry,
    )
    from strategy.wave_sequence import compute_wave_sequence_info_per_wave

    # Pripravime cfg s trend filtrem zapnutym, abychom videli trend state.
    cfg_tr = BotConfig(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        entry_mode=EntryMode.MARKET_FALLBACK,
        tp_mode=TPMode.WAVE_TARGET_N,
        tp_target_wave_index=4,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
    )
    trend_states = compute_trend_states_per_wave(df, waves, cfg_tr)
    seq_info = compute_wave_sequence_info_per_wave(df, waves, cfg_tr)
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    birth = compute_wave_birth_bars_pine(df, cfg_tr)
    print("=== Birth bar + trend state per wave ===")
    for i, w in enumerate(waves):
        wt = str(w["wave_time"])
        ts = trend_states.get(wt)
        si = seq_info.get(wt)
        allowed, reason = wave_allowed_for_entry(w, ts, cfg_tr)
        b = birth.get(wt)
        b_time = df["time"].iloc[b] if b is not None and b < len(df) else "?"
        b_close = df["close"].iloc[b] if b is not None and b < len(df) else None
        print(
            f"  [{i:2d}] {wt}  dir={int(w['dir']):+d}  "
            f"extreme={w.get('box_bottom' if w['dir']==-1 else 'box_top'):.5f}  "
            f"birth_bar={b} ({b_time}) close={b_close:.5f}  "
            f"trend@birth={ts.direction if ts else '-'}  "
            f"idx_in_trend={si.index_in_trend if si else '-'}  "
            f"allowed={allowed}  reason={reason}  "
            f"is_ext={bool(w.get('is_ext', False))}"
        )

    # KLICOVE: kde a kdy doslo k BOS flipu, ktery zpusobil ze wave [7] vidi 'bull'
    print()
    print("=== BOS flip timeline (bar po baru, kontrolujeme close vs swing levels) ===")
    from dataclasses import replace
    from strategy.trend_bos import (
        TrendState,
        bos_triggered_close,
        maybe_update_trend_state_with_wave,
        _maybe_seed_state_from_ext_post_trend,
    )

    waves_by_birth_bar = {}
    for w in waves:
        b = birth.get(w["wave_time"])
        if b is not None:
            waves_by_birth_bar.setdefault(int(b), []).append(w)

    state = TrendState()
    closes = df["close"].astype(float).to_numpy()
    flips_logged = 0
    for i in range(len(df)):
        bar_close = float(closes[i])
        prev_dir = state.direction
        if state.direction == "bull":
            if bos_triggered_close("bull", state.last_up_box_bottom, bar_close):
                state = TrendState(direction="bear")
        elif state.direction == "bear":
            if bos_triggered_close("bear", state.last_down_box_top, bar_close):
                state = TrendState(direction="bull")
        if prev_dir != state.direction:
            flips_logged += 1
            print(
                f"  *** BOS FLIP at bar {i} ({df['time'].iloc[i]}): "
                f"{prev_dir} -> {state.direction}  close={bar_close:.5f}  "
                f"swing(prev_last_down_top OR last_up_bot crossed)"
            )

        for w in waves_by_birth_bar.get(i, []):
            wt = str(w["wave_time"])
            wdir = int(w["dir"])
            state = _maybe_seed_state_from_ext_post_trend(state, w)
            tag = f"trend@birth={state.direction}"
            if i >= 60 and i <= 90:
                print(
                    f"  BORN bar {i} ({df['time'].iloc[i]})  wave[{wt}] dir={wdir:+d}  "
                    f"{tag}  last_up_bot={state.last_up_box_bottom}  "
                    f"last_down_top={state.last_down_box_top}"
                )
            maybe_update_trend_state_with_wave(state, w, cfg_tr)
    print(f"\nTotal BOS flips: {flips_logged}")

    # Detail: ktery bar je 75/76/79 ve dni Mar 23
    print("\n=== Bar number to time mapping (vc. 65,70,75,76,79) ===")
    for b in [62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 83, 85, 89]:
        if b < len(df):
            t = df["time"].iloc[b]
            o = df["open"].iloc[b]
            h = df["high"].iloc[b]
            lo = df["low"].iloc[b]
            c = df["close"].iloc[b]
            print(
                f"  bar {b:3d} = {t}  O={o:.5f} H={h:.5f} L={lo:.5f} C={c:.5f}  "
                f"close{'>=' if c >= o else '<'}open"
            )


if __name__ == "__main__":
    main()
