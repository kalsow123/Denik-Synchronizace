
from typing import List

from config.bot_config import BotConfig


# ───── WAVE DETECTION ──────────────────────────
# Vstup dat z OHLC. Struktura vlny: emulator TradingView Pine v6
# (strategy/wave_detection_pine.py) — jedina implementace.
#
# MĚŘENÍ FIB 0 / 1 (shodné pro live i backtest):
#   UP vlna   (w_dir=+1): box_top = swing high (konec impulsu nahoru), box_bottom = start.
#      fib 0 = box_top, fib 1 = box_bottom. Rostoucí číslo fib = hlubší retracement dolů.
#   DOWN vlna (w_dir=-1): box_bottom = swing low, box_top = start (vyšší).
#      fib 0 = box_bottom, fib 1 = box_top. Rostoucí číslo fib = hlubší retracement nahoru.
#
#   Trend-follow: entry_fib a sl_fib se měří stejně od „konce“ impulsu k počátku boxu;
#   platí sl_fib_level > entry_fib_level (SL je vždy hlouběji proti směru vstupu).
#
# WAVE + (`BotConfig.wave_plus`): po potvrzení doplní čas, finální HIGH/LOW a přepočte Fiba — viz wave_detection_pine.


def detect_waves(df, cfg: BotConfig) -> List[dict]:
    """
    Detekce vln — Pine/TV logika (`strategy.wave_detection_pine.detect_waves_pine`).

    POZN.: Pro tp_mode = WAVE_TARGET_N se TP cena nepocita uz pri detekci, ale
    az v engine / live na zaklade poradi vlny v trendu
    (`strategy.wave_sequence.compute_wave_sequence_info_per_wave`).
    Funkce `apply_tp_mode_to_waves` je ponechana jako no-op stub kvuli zpetne
    kompatibilite caller signature.
    """
    from strategy.wave_detection_pine import detect_waves_pine
    from strategy.trend_bos import apply_tp_mode_to_waves

    waves = detect_waves_pine(df, cfg)
    apply_tp_mode_to_waves(waves, cfg)
    return waves
