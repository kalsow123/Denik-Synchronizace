"""Integrace: EXT pozice na sve parent vlne se NEZAVIRA prez TP_WAVE_N / BOS / EXT_BOS."""
import pandas as pd
from backtest.engine import BacktestEngine
from config.bot_config import LIVE_BOT_CONFIG


def _load_data():
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-01") & (df["time"] <= "2026-04-15")].reset_index(drop=True)
    return df


def test_no_ext_closed_within_own_parent_window_except_sl():
    """ZADNA EXT block pozice se nezavre v okne sve parent vlny mimo SL/END_OF_DATA."""
    df = _load_data()
    cfg = LIVE_BOT_CONFIG
    cfg.tp_mode = "wave_target_n"
    
    eng = BacktestEngine(cfg)
    eng.run(df)
    
    # Najit vsechny EXT block uzavrene pozice s reason != SL/END_OF_DATA
    bad_closes = []
    for ct in eng.closed_trades:
        if not getattr(ct, "is_ext", False):
            continue
        if ct.close_reason in ("SL", "END_OF_DATA"):
            continue
        # Byla pozice uzavrena v okne sve parent vlny?
        parent_birth = eng.wave_birth_by_time.get(ct.wave_time)
        if parent_birth is None:
            continue
        # Najit nejnovejsi wave naroizenou do close_bar vcetne
        latest = max(
            (b for b in eng.wave_birth_by_time.values() if b <= ct.close_bar),
            default=-1,
        )
        if parent_birth == latest:
            bad_closes.append({
                "wave_time": ct.wave_time,
                "close_bar": ct.close_bar,
                "close_reason": ct.close_reason,
                "entry_tag": getattr(ct, "entry_tag", "?"),
            })
    
    assert not bad_closes, (
        f"Nalezeno {len(bad_closes)} EXT block pozic uzavrenych v okne "
        f"sve parent vlny mimo SL: {bad_closes[:5]}"
    )