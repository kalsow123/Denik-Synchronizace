"""Bod 2: kvantifikace trend-source divergence live vs engine.
Porovnava wave-set (pre-WF / live post-WF / engine all_waves) a trend_states_per_wave."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"


def main() -> None:
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from scripts.e2e_live_broker_sim import install_fake

    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    install_fake(cfg.symbol, cfg.contract_size)

    from backtest.data_loader import filter_by_date_range, load_csv
    from backtest.engine import BacktestEngine
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from strategy.trend_bos import compute_trend_states_per_wave
    from runtime.wf_live import WfLiveRuntime

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)

    # ENGINE
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)
    eng_waves = {str(w["wave_time"]): w for w in eng._all_waves}
    eng_trend = {str(k): v for k, v in eng.trend_states_per_wave.items()}

    # LIVE (run_e2e zacatek)
    waves = detect_waves(df, cfg)
    pre_wf = {str(w["wave_time"]) for w in waves}
    wave_birth = compute_wave_birth_bars_pine(df, cfg)
    wf = WfLiveRuntime()
    wf.process(df, cfg, waves, wave_birth_by_time=wave_birth)
    live_waves = {str(w["wave_time"]): w for w in waves}
    live_trend = compute_trend_states_per_wave(df, waves, cfg)
    live_trend = {str(k): v for k, v in live_trend.items()}

    print(f"wave-set counts: detect(pre-WF)={len(pre_wf)}  live(post-WF)={len(live_waves)}  "
          f"engine_all={len(eng_waves)}")
    only_eng = set(eng_waves) - set(live_waves)
    only_live = set(live_waves) - set(eng_waves)
    common = set(eng_waves) & set(live_waves)
    print(f"  jen engine: {len(only_eng)}   jen live: {len(only_live)}   spolecne: {len(common)}")

    # trend dir divergence na spolecnych vlnach
    def tdir(ts):
        return getattr(ts, "direction", None) if ts is not None else None

    diff_dir = []
    miss_live = []
    for wt in sorted(common):
        et = eng_trend.get(wt)
        lt = live_trend.get(wt)
        if lt is None and et is not None:
            miss_live.append(wt)
            continue
        if tdir(et) != tdir(lt):
            diff_dir.append((wt, tdir(et), tdir(lt)))

    print(f"\nTREND na {len(common)} spolecnych vlnach:")
    print(f"  rozdilny direction: {len(diff_dir)}")
    print(f"  live nema trend snapshot (engine ma): {len(miss_live)}")
    for wt, e, l in diff_dir[:40]:
        print(f"    {wt}: engine={e}  live={l}")
    if miss_live[:40]:
        print("  miss_live:", miss_live[:40])

    # rozdil ordering/index_in_trend
    diff_idx = 0
    for wt in common:
        ew = eng_waves[wt]; lw = live_waves[wt]
        if ew.get("index_in_trend") != lw.get("index_in_trend"):
            diff_idx += 1
    print(f"\n  rozdilny index_in_trend na spolecnych: {diff_idx}")

    def desc(wmap, wt):
        w = wmap.get(wt, {})
        return (f"dl={w.get('draw_left')} dr={w.get('draw_right')} "
                f"dir={w.get('w_dir', w.get('dir'))} origin={w.get('wave_origin')} "
                f"wf_cont={w.get('wf_continued_classic')} wfpos={w.get('wf_wave_position')} "
                f"is_ext={w.get('is_ext')}")

    print(f"\n  JEN ENGINE ({len(only_eng)}):")
    for wt in sorted(only_eng):
        print(f"    {wt}  {desc(eng_waves, wt)}")
    print(f"\n  JEN LIVE ({len(only_live)}):")
    for wt in sorted(only_live):
        print(f"    {wt}  {desc(live_waves, wt)}")


if __name__ == "__main__":
    main()
