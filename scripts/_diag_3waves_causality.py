"""
Kauzalita 3 velkych BT-only vln (85 % gapu):
  202603250100 (trend_filter, +6122)
  202603310430 (not_in_detect, +3340)
  202603051030 (not_in_detect, +3227)

Cil: zjistit ZDA engine pouziva budouci bary (look-ahead), nebo je vlna
kauzalne dostupna i live (jen jinou cestou: WF / retro-BOS / trend-state).

Spusteni: .venv\\Scripts\\python.exe scripts/_diag_3waves_causality.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"
TARGETS = ["202603250100"]


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from strategy.trend_bos import (
        compute_trend_states_per_bar, compute_bos_wave_flip_map,
        _detect_close_bos_timeline_flips, reconcile_bos_flip_map_with_wave_sequence,
    )
    from strategy.wave_sequence import sync_wave_sequence_state
    from runtime.wf_live import WfLiveRuntime

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)

    eng = BacktestEngine(cfg)
    closed = eng.run(df, retain_wave_snapshot=True)
    eng_wave = eng.waves_by_wave_time
    eng_birth = eng.wave_birth_by_time
    eng_flip_by_bar = {b: str(w.get("wave_time")) for b, w in eng._bos_flip_wave_by_bar.items()}
    eng_tspw = {str(k): v for k, v in getattr(eng, "trend_states_per_wave", {}).items()}

    # trades by wave_time (engine)
    eng_trades: dict[str, list] = {}
    for t in closed:
        eng_trades.setdefault(str(t.wave_time), []).append(t)

    # ---- live cesta (jako run_e2e) ----
    waves = detect_waves(df, cfg)
    wave_birth = compute_wave_birth_bars_pine(df, cfg)
    detect_wt = {str(w["wave_time"]): w for w in waves}

    wf = WfLiveRuntime()
    wf.process(df, cfg, waves, wave_birth_by_time=wave_birth)
    wf_queue = wf.pop_activation_results()
    wf_wt = set()
    for item in wf_queue:
        w = getattr(item, "wave", None) or (item.get("wave") if isinstance(item, dict) else None)
        if w:
            wf_wt.add(str(w.get("wave_time")))
        else:
            wt = getattr(item, "wave_time", None) or (item.get("wave_time") if isinstance(item, dict) else None)
            if wt:
                wf_wt.add(str(wt))

    seq_info, protected = sync_wave_sequence_state(df, waves, cfg)
    bar_trend = compute_trend_states_per_bar(df, waves, cfg)
    from strategy.trend_bos import compute_trend_states_per_wave, wave_allowed_for_entry
    live_tspw = {str(k): v for k, v in compute_trend_states_per_wave(df, waves, cfg).items()}

    live_flip_map = {}
    if cfg.trend_filter_enabled:
        flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=wave_birth)
        live_flip_map = reconcile_bos_flip_map_with_wave_sequence(
            compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=wave_birth),
            flips, waves, seq_info, wave_birth,
        )
    live_flip_by_bar = {int(b): str(wt) for b, wt in live_flip_map.items()}

    def trend_at(bar):
        try:
            v = bar_trend[bar]
            return v if not isinstance(v, (list, tuple)) else v
        except Exception:
            return "n/a"

    for wt in TARGETS:
        print("\n" + "=" * 78)
        print(f"VLNA {wt}")
        print("=" * 78)
        ew = eng_wave.get(wt)
        trades = eng_trades.get(wt, [])
        bbar = eng_birth.get(wt)
        print(f"  ENGINE wave: present={ew is not None}")
        if ew:
            print(f"    draw_left={ew.get('draw_left')} draw_right={ew.get('draw_right')} "
                  f"w_dir={ew.get('w_dir', ew.get('dir'))} is_ext={ew.get('is_ext')} "
                  f"wf_origin={ew.get('wf_origin', ew.get('is_wf_origin'))}")
            print(f"    birth_bar(engine)={bbar}  index_in_trend={ew.get('index_in_trend')}")
        def f5(x):
            return f"{x:.5f}" if isinstance(x, (int, float)) else str(x)
        for t in trades:
            eb = int(t.close_bar) - int(t.bars_held)
            print(f"    TRADE entry_bar={eb} dir={t.dir} ep={f5(t.entry_price)} sl={f5(t.sl)} "
                  f"tp={f5(t.tp)} close_bar={t.close_bar} reason={t.close_reason} "
                  f"pnl={t.pnl_usd:.0f} entry_type={t.entry_type} tag={t.entry_tag} "
                  f"origin={t.wave_origin} bos_reentry={t.is_bos_reentry}")
            # retro-BOS? engine flip bar == entry bar a flip vlna == tato vlna
            for fb in (eb, eb - 1):
                if eng_flip_by_bar.get(fb) == wt:
                    print(f"      -> ENGINE retro-BOS flip na baru {fb} (bypass trend filtru)")

        # per-wave trend snapshot + filter rozhodnuti
        et = eng_tspw.get(wt); lt = live_tspw.get(wt)
        print(f"  ENGINE per-wave trend.dir = {getattr(et, 'direction', None)}  "
              f"(neutral_first={getattr(et, 'is_bos_wave_pending', None)})")
        print(f"  LIVE   per-wave trend.dir = {getattr(lt, 'direction', None)}")
        if ew is not None:
            ea, er = wave_allowed_for_entry(ew, et, cfg)
            lwd = detect_wt.get(wt)
            la, lr = wave_allowed_for_entry(lwd, lt, cfg) if lwd else (None, "no_wave")
            print(f"  wave_allowed_for_entry  ENGINE=({ea},{er})  LIVE=({la},{lr})")
            print(f"  ext flags: post_ext_lock(eng)={ew.get('post_ext_confirmed_trend_lock')} "
                  f"ext_range_active(eng)={ew.get('ext_range_active')} "
                  f"idx_eng={ew.get('index_in_trend')} idx_live={detect_wt.get(wt, {}).get('index_in_trend')}")
        print(f"  LIVE detect_waves: present={wt in detect_wt}")
        if wt in detect_wt:
            lw = detect_wt[wt]
            print(f"    draw_left={lw.get('draw_left')} draw_right={lw.get('draw_right')} "
                  f"w_dir={lw.get('w_dir', lw.get('dir'))} is_ext={lw.get('is_ext')}")
        print(f"  LIVE WF aktivace: present={wt in wf_wt}")
        print(f"  >>> BOS_WAVE_TIMES (bypass trend filtru): "
              f"ENGINE={wt in getattr(eng, '_bos_wave_times', set())}  "
              f"LIVE={wt in set(live_flip_by_bar.values())}")
        print(f"  LIVE BOS-flip mapa obsahuje vlnu: "
              f"{wt in set(live_flip_by_bar.values())}  "
              f"(engine _bos_flip_wave_by_bar: {wt in set(eng_flip_by_bar.values())})")
        if bbar is not None:
            print(f"  TREND-STATE kolem birth_bar={bbar}:")
            for b in range(max(0, bbar - 2), bbar + 3):
                ef = eng_flip_by_bar.get(b)
                lf = live_flip_by_bar.get(b)
                print(f"    bar {b}: live_trend={trend_at(b)}  "
                      f"eng_flip={ef or '-'}  live_flip={lf or '-'}")


if __name__ == "__main__":
    main()
