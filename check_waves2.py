import pandas as pd
from config.bot_config import BotConfig
from config.enums import TPMode
from backtest.engine import BacktestEngine

cfg = BotConfig(
    symbol='EU50p', timeframe=30, wave_min_pct=0.26, 
    ext_enabled=True, ext_wave_min_pct=0.76,
    ext_post_confirmed_trend_count=2,
    ext_post_confirmed_trend_lock_enabled=True,
    ext_post_confirmed_trend_lock_blocks_both_sides=True,
    wave_position_enabled=True,
    tp_mode=TPMode.BOS_EXIT,
    ext_trade_both_sides_in_range=True,
    ext_range_wave_min_pct=0.13,
    trend_filter_enabled=True,
    trend_hh_hl_filter_enabled=True,
    wave_plus=True,
)
df = pd.read_csv('data/EURUSD.x_M30.csv', parse_dates=['datetime']).rename(columns={'datetime':'time'})
df = df[(df['time']>='2026-03-22') & (df['time']<='2026-03-26 23:59:59')].reset_index(drop=True)
eng = BacktestEngine(cfg)
eng.run(df, retain_wave_snapshot=True)

for w in eng.last_waves_for_visual:
    print(f"{w['wave_time']} dir={w['dir']} is_ext={w.get('is_ext')} lock={w.get('post_ext_confirmed_trend_lock')} supp={w.get('post_ext_trend_suppressed')} seed={w.get('ext_post_trend_seed_dir')} origin={w.get('wave_origin')}")

vis = {str(w['wave_time']) for w in eng.last_waves_for_visual}
print("VIS:", vis)
print("TWO SIDED:", eng._two_sided_fired_wave_times)
