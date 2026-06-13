import pandas as pd
from config.bot_config import BotConfig
from backtest.engine import BacktestEngine

cfg = BotConfig(
    symbol='EU50p', timeframe=30, wave_min_pct=0.26, 
    ext_enabled=True, ext_wave_min_pct=0.76,
    ext_post_confirmed_trend_count=2,
    ext_post_confirmed_trend_lock_enabled=True,
    ext_post_confirmed_trend_lock_blocks_both_sides=True
)
df = pd.read_csv('data/EURUSD.x_M30.csv', parse_dates=['datetime']).rename(columns={'datetime':'time'})
df = df[(df['time']>='2026-03-22') & (df['time']<='2026-03-26 23:59:59')].reset_index(drop=True)
eng = BacktestEngine(cfg)
eng.run(df, retain_wave_snapshot=True)

for w in eng.last_waves:
    print(f"{w['wave_time']} dir={w['dir']} is_ext={w.get('is_ext')} lock={w.get('post_ext_confirmed_trend_lock')} supp={w.get('post_ext_trend_suppressed')} seed={w.get('ext_post_trend_seed_dir')}")
