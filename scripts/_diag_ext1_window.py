import sys
import os
import pandas as pd

sys.path.insert(0, os.path.abspath('.'))

from backtest.engine import BacktestEngine
from config.bot_config import BotConfig, TPMode
from backtest.data_loader import load_csv, filter_by_date_range
from strategy.wave_sequence import compute_wave_sequence_info_per_wave

def run_diag():
    cfg = BotConfig(
        symbol="EURUSD.x",
        timeframe=30,
        wave_min_pct=0.26,
        min_opp_bars=3,
        rrr=2.5,
        entry_mode="market_fallback",
        tp_mode=TPMode.WAVE_TARGET_N,
        ext_enabled=True,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=False,
        wave_counter_two_sided_enabled=True,
        ext_trade_both_sides_in_range=True,
        wave_extension_pct=0.1,
        ext_post_both_sides_wave_min_pct=0.13,
        ext_post_both_sides_default_sl_pct=0.1,
        ext_counter_enabled=True,
        ext_counter_time="23:00",
        ext_wave_min_pct=0.76,
        ext_weekend_gap_relax_factor=0.5,
    )
    cfg.fib_level = 0.55
    
    df = load_csv("data/EURUSD_M30.csv")
    df = filter_by_date_range(df, "2025-05-10", "2025-10-10")
    
    engine = BacktestEngine(cfg)
    engine.run(df)
    
    for w in engine._all_waves:
        if str(w.get("wave_time")) == "202505140130":
            print(f"Before compute: is_ext={w.get('is_ext')}, suppressed={w.get('post_ext_trend_suppressed')}, two_sided={w.get('is_two_sided_counter')}")
    
    for w in engine._all_waves:
        wt = str(w.get("wave_time"))
        if "20250523" <= wt <= "202505290530":
            print(f"Wave {wt}: dir={w.get('dir')}, is_ext={w.get('is_ext')}, box_bottom={w.get('box_bottom')}, box_top={w.get('box_top')}")
    
    print("--- MANUAL CALL 1 ---")
    compute_wave_sequence_info_per_wave(df, engine._all_waves, cfg)

    print("--- MANUAL CALL 2 ---")
    res = compute_wave_sequence_info_per_wave(df, engine._all_waves, cfg)
    print(f"DEBUG: res.get('202505291300') = {res.get('202505291300')}")
    
    count_1300 = sum(1 for w in engine._all_waves if str(w.get("wave_time")) == "202505291300")
    print(f"DEBUG: count of 202505291300 in engine._all_waves = {count_1300}")

    print("Waves:")
    for w in engine._all_waves:
        wt = str(w.get("wave_time", ""))
        if "20250528" <= wt <= "20250531":
            info = engine.wave_sequence_info.get(wt) if hasattr(engine, "wave_sequence_info") else None
            idx = info.index_in_trend if info else None
            w_idx = w.get("index_in_trend")
            is_bos = info.is_bos_wave if info else None
            print(f"Wave {wt}: dir={w.get('dir')}, is_ext={w.get('is_ext')}, "
                  f"engine_idx={idx}, w_idx={w_idx}, is_bos={is_bos}, "
                  f"hh_hl={w.get('hh_hl_pass')}, "
                  f"suppressed={w.get('post_ext_trend_suppressed')}, "
                  f"box_bottom={w.get('box_bottom')}, box_top={w.get('box_top')}, "
                  f"draw_right={w.get('draw_right')}, is_wf={w.get('is_wf')}")

if __name__ == "__main__":
    run_diag()
