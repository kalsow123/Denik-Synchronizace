
# ───── DATOVÁ STRUKTURA - Dokumentační reference ──────────────────────────

from typing import TypedDict

class WaveSignal(TypedDict):
    """
    Dict-shape jednoho obchodniho setupu vznikajiciho z uzavrene validni vlny.
        dir       - smer setupu (1 = BUY, -1 = SELL); je opacny vuci smeru vlny
        fib50     - cena entry (50% retracement vlny)
        sl        - stop loss (druha hrana boxu vlny)
        tp        - take profit (RRR-nasobek vzdalenosti SL od entry)
        move_pct  - procentualni velikost puvodni vlny
        wave_time - timestamp uzavreni vlny ve formatu "YYYYMMDDHHMM"
    """
    dir: int
    fib50: float
    sl: float
    tp: float
    move_pct: float
    wave_time: str
