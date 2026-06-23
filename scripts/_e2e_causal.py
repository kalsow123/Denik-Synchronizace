"""KAUZALNI (rolling) E2E — mimikuje produkci: kazdy bar prepocita struktury nad
prefixem df[0:i+1] (BEZ budoucich baru, jako get_bars(startup_bars) konciciho na
aktualnim baru), pak zpracuje bar i pres STEJNOU replay cestu jako run_e2e.

Cil: zmerit look-ahead efekt vs run_e2e (ktery predpocita vse pres cele df).

ENV:
  CAUSAL_MAX_BAR   limit poctu baru (default vse)
  CAUSAL_WINDOW    rolling window (default 1440 = produkce startup_bars; 0 = cely prefix)
  CAUSAL_RECALC_EVERY  prepocet vln kazdych N baru (default 1)
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"


def main() -> None:
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from scripts.e2e_live_broker_sim import install_fake, _clean_wave_time

    engine_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    fake = install_fake(engine_cfg.symbol, engine_cfg.contract_size)

    import pandas as pd
    from backtest.data_loader import filter_by_date_range, load_csv
    from runtime.live_wave_isolation import resolve_live_execution_config
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from strategy.trend_bos import (
        _detect_close_bos_timeline_flips, compute_bos_wave_flip_map,
        compute_trend_states_per_bar, compute_trend_states_per_wave,
        reconcile_bos_flip_map_with_wave_sequence,
    )
    from strategy.wave_sequence import sync_wave_sequence_state
    from strategy.ext_range import ext_range_enabled, reapply_ext_range_tags
    from strategy.two_sided import two_sided_enabled
    from runtime.ext_live import ExtLiveRuntime
    from runtime.wf_live import WfLiveRuntime
    from runtime.missed_bar_replay import MissedBarReplayState, replay_missed_closed_bar
    from infra.orders import get_active_counter_wave_times
    from config.enums import PendingCancelMode
    import runtime.live_loop as ll
    from core.logging_utils import log_event

    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)

    n = len(df)
    max_bar = int(os.environ.get("CAUSAL_MAX_BAR", n))
    window = int(os.environ.get("CAUSAL_WINDOW", 1440))
    recalc_every = int(os.environ.get("CAUSAL_RECALC_EVERY", 1))
    print(f"baru={n} max_bar={max_bar} window={window} recalc_every={recalc_every}", flush=True)

    pcm = (PendingCancelMode(cfg.pending_cancel_mode)
           if isinstance(cfg.pending_cancel_mode, str) else cfg.pending_cancel_mode)

    # Stateful runtimes (jako produkce: vytvoreny jednou, persistuji pres cykly)
    wf_runtime = WfLiveRuntime()
    ext_runtime = ExtLiveRuntime()
    ext_runtime.sync_from_mt5(cfg)
    if two_sided_enabled(cfg):
        ll._live_two_sided_tracker.clear_all()

    sent_signals: set = set()
    failed_signals: dict = {}
    state = MissedBarReplayState(
        last_known_trend_dir=None, prev_cycle_last_bar_time=None,
        processed_tp_wave_times=set(), forming_tp_watch=None,
        ext_sl_anchor=None, retro_bos_attempted=set(),
    )

    from backtest.wave_sim_cache import clear_pine_sim_cache
    t_start = time.time()
    for bar_idx in range(1, max_bar):
        # ROLLING: kazde okno ma unikatni klic -> cache nikdy nehitne napric bary,
        # jen roste (memory -> GC -> zpomaleni). Vycisti pred kazdym barem: v ramci
        # baru se sdili (detect/birth/engine volaji stejny df_win -> hit), napric NE.
        clear_pine_sim_cache()
        # KAUZALNI okno: prefix konci aktualnim barem (zadne budouci bary)
        lo = 0 if window <= 0 else max(0, bar_idx + 1 - window)
        df_win = df.iloc[lo:bar_idx + 1].reset_index(drop=True)
        cur = len(df_win) - 1  # index aktualniho baru ve windowu

        waves = detect_waves(df_win, cfg)
        if not waves:
            continue
        wave_birth = compute_wave_birth_bars_pine(df_win, cfg)
        wf_runtime.process(df_win, cfg, waves, wave_birth_by_time=wave_birth)
        wf_queue = wf_runtime.pop_activation_results()

        if cfg.trend_filter_enabled or two_sided_enabled(cfg):
            trend_states_per_wave = compute_trend_states_per_wave(df_win, waves, cfg)
        else:
            trend_states_per_wave = {}
        seq_info, protected_waves = sync_wave_sequence_state(df_win, waves, cfg)
        if ext_range_enabled(cfg):
            reapply_ext_range_tags(waves, cfg, df=df_win, wave_birth=wave_birth)
            seq_info, protected_waves = sync_wave_sequence_state(df_win, waves, cfg)

        bos_flip_map = {}
        bos_wave_times = set()
        if cfg.trend_filter_enabled:
            flips = _detect_close_bos_timeline_flips(df_win, waves, cfg, wave_birth_bars=wave_birth)
            bos_flip_map = reconcile_bos_flip_map_with_wave_sequence(
                compute_bos_wave_flip_map(df_win, waves, cfg, wave_birth_bars=wave_birth),
                flips, waves, seq_info, wave_birth,
            )
            bos_wave_times = set(bos_flip_map.values())

        ext_runtime.refresh_simulation(df_win, cfg, seq_info=seq_info,
                                       protected_waves=protected_waves, waves=waves)
        ext_runtime.run_ext1_rrr_better_exit(cfg, df_win)
        ext1_per_bar = ext_runtime._ext1_protection_per_bar
        bar_trend_states = compute_trend_states_per_bar(df_win, waves, cfg)

        row = df_win.iloc[cur]
        bt = pd.Timestamp(row["time"]).to_pydatetime()
        # broker bar context = ABSOLUTNI index (kvuli stabilite _entry_bar napric okny)
        fake.set_bar(bar_idx, bt, float(row["close"]))
        fake.check_resting_sltp(bar_idx, float(row["high"]), float(row["low"]))

        # mapuj entry_bar pozic z absolutniho na window-relativni pro replay gate
        state = replay_missed_closed_bar(
            cfg=cfg, df=df_win, waves=waves, bar_idx=cur, state=state,
            bar_trend_states=bar_trend_states, seq_info=seq_info,
            protected_waves=protected_waves, bos_flip_map=bos_flip_map,
            bos_wave_times=bos_wave_times, trend_states_per_wave=trend_states_per_wave,
            ext1_per_bar=ext1_per_bar, ext_runtime=ext_runtime,
            wf_activations=wf_queue, sent_signals=sent_signals,
            failed_signals=failed_signals, signal_digits=5,
            entries_allowed=True, wave_birth_by_time=wave_birth,
            active_counter_wave_times=get_active_counter_wave_times(cfg), pcm=pcm,
            place_live_bos_reentry=ll._place_live_bos_reentry,
            place_live_counter_from_g_extension=ll._place_live_counter_from_g_extension,
            g_extension_hit_closed_positions=ll._g_extension_hit_closed_positions,
            place_live_counter_position=ll._place_live_counter_position,
            log_event_fn=log_event, two_sided_tracker=ll._live_two_sided_tracker,
        )
        fake.fill_pendings(bar_idx, float(row["high"]), float(row["low"]),
                           float(row["open"]), float(row["close"]))

        if bar_idx % 500 == 0:
            el = time.time() - t_start
            print(f"  bar {bar_idx}/{max_bar}  closed={len(fake._closed)}  "
                  f"elapsed={el:.0f}s  rate={bar_idx/el:.1f} bar/s", flush=True)

    closed = fake._closed
    total = sum(t.pnl_usd for t in closed)
    wts = sorted({_clean_wave_time(getattr(t, "comment", "")) for t in closed})
    print(f"\nCAUSAL E2E: {len(closed)} obchodu / {total:.0f} USD  "
          f"unik_wt={len(wts)}", flush=True)
    # dump wave_times pro porovnani
    print("WT:", ",".join(wts), flush=True)


if __name__ == "__main__":
    main()
