"""
Cilena sonda: jak ENGINE vstoupi do cilovych vln (bypass? trend-state v okamziku?
_bos_wave_times v okamziku?) vs co vidi LIVE (post-WF) trend-state + bos mnozina.

Spusteni: .venv\\Scripts\\python.exe scripts/_diag_fix_probe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"
TARGETS = {"202603250100", "202603310430", "202603051030"}


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    import backtest.engine as eng_mod
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from strategy.trend_bos import (
        compute_trend_states_per_wave, wave_allowed_for_entry,
        _detect_close_bos_timeline_flips, compute_bos_wave_flip_map,
        reconcile_bos_flip_map_with_wave_sequence,
    )
    from strategy.wave_sequence import sync_wave_sequence_state
    from runtime.wf_live import WfLiveRuntime

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)

    # ---- ENGINE: spy na _process_new_wave pro cilove vlny ----
    calls = []
    orig_pnw = eng_mod.BacktestEngine._process_new_wave

    def spy(self, wave, bar_idx, bar_time, bar, *, bypass_trend_filter=False,
            is_two_sided_mirror=False):
        wt = str(wave.get("wave_time", "") or "")
        if wt in TARGETS:
            ts = self.trend_states_per_wave.get(wave["wave_time"])
            allowed, reason = wave_allowed_for_entry(wave, ts, cfg)
            calls.append({
                "wt": wt, "bar": int(bar_idx), "bypass": bool(bypass_trend_filter),
                "mirror": bool(is_two_sided_mirror),
                "ts_dir": getattr(ts, "direction", None),
                "is_bos_pending": getattr(ts, "is_bos_wave_pending", None),
                "allowed": allowed, "reason": reason,
                "in_bos_times": wt in self._bos_wave_times,
                "in_flip_map": wt in {str(w.get("wave_time")) for w in self._bos_flip_wave_by_bar.values()},
            })
        return orig_pnw(self, wave, bar_idx, bar_time, bar,
                        bypass_trend_filter=bypass_trend_filter,
                        is_two_sided_mirror=is_two_sided_mirror)

    eng_mod.BacktestEngine._process_new_wave = spy
    eng = BacktestEngine(cfg)
    closed = eng.run(df, retain_wave_snapshot=True)
    eng_mod.BacktestEngine._process_new_wave = orig_pnw

    eng_trades = {}
    for t in closed:
        eng_trades.setdefault(str(t.wave_time), []).append(t)

    print("=" * 78)
    print("ENGINE _process_new_wave volani pro cilove vlny:")
    for c in calls:
        print(f"  {c['wt']} bar={c['bar']} bypass={c['bypass']} mirror={c['mirror']} "
              f"ts_dir={c['ts_dir']} bos_pending={c['is_bos_pending']} "
              f"allowed={c['allowed']}({c['reason']}) in_bos_times={c['in_bos_times']} "
              f"in_flip_map={c['in_flip_map']}")

    # ---- LIVE post-WF trend-state + bos mnozina ----
    waves = detect_waves(df, cfg)
    wave_birth = compute_wave_birth_bars_pine(df, cfg)
    detect_tspw = compute_trend_states_per_wave(df, waves, cfg)

    wf = WfLiveRuntime()
    wf.process(df, cfg, waves, wave_birth_by_time=wave_birth)
    post_tspw = compute_trend_states_per_wave(df, waves, cfg)
    post_wt = {str(w["wave_time"]): w for w in waves}
    seq_info, _ = sync_wave_sequence_state(df, waves, cfg)
    flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=wave_birth)
    post_rec = reconcile_bos_flip_map_with_wave_sequence(
        compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=wave_birth),
        flips, waves, seq_info, wave_birth,
    )
    post_bos_times = set(post_rec.values())

    print("\n" + "=" * 78)
    print("LIVE (post-WF) per-wave trend + bos mnozina pro cilove vlny:")
    for wt in sorted(TARGETS):
        w = post_wt.get(wt)
        dts = detect_tspw.get(wt)
        pts = post_tspw.get(wt)
        da, dr = wave_allowed_for_entry(w, dts, cfg) if w else (None, "no_wave_detect_ts")
        pa, pr = wave_allowed_for_entry(w, pts, cfg) if w else (None, "no_wave")
        seq = seq_info.get(wt)
        print(f"  {wt}: birth={wave_birth.get(wt)} dir={w.get('dir') if w else '?'}")
        print(f"     detect-ts dir={getattr(dts,'direction',None)} allowed={da}({dr})")
        print(f"     postWF-ts dir={getattr(pts,'direction',None)} allowed={pa}({pr})")
        print(f"     in post bos_times={wt in post_bos_times}  "
              f"is_bos_wave(seq)={getattr(seq,'is_bos_wave',None)}  "
              f"flip_bars={sorted(b for b,v in post_rec.items() if v==wt)}")


if __name__ == "__main__":
    main()
