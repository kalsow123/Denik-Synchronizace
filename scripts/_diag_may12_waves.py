import sys
import os
import pandas as pd

sys.path.insert(0, os.path.abspath('.'))

from backtest.engine import BacktestEngine
from config.bot_config import BotConfig, TPMode
from backtest.data_loader import load_csv, filter_by_date_range

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
        trend_hh_hl_filter_enabled=True,
        wave_counter_two_sided_enabled=True,
        wave_extension_pct=0.1,
    )
    cfg.fib_level = 0.55
    
    df = load_csv("data/EURUSD_M30.csv")
    df = filter_by_date_range(df, "2025-05-10", "2025-10-10")
    print(f"Loaded {len(df)} rows")
    
    engine = BacktestEngine(cfg)
    engine.run(df)
    
    print(f"Generated {len(engine._all_waves)} waves")
    
    # Find waves around May 12-15
    for w in engine._all_waves:
        wt = str(w.get("wave_time", ""))
        if "20250512" <= wt <= "20250516":
            dr = w.get("draw_right", 0)
            bar_time = df.iloc[dr]["time"] if dr < len(df) else "N/A"
            print(f"Wave {wt}: dir={w.get('dir')}, is_ext={w.get('is_ext')}, "
                  f"idx={w.get('index_in_trend')}, is_bos={w.get('is_bos_wave')}, "
                  f"bar_time={bar_time}")

if __name__ == "__main__":
    run_diag()
